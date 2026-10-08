import asyncio
from dataclasses import dataclass
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.media_agent.fake import FakeMediaAgent


@dataclass
class ManualClock:
    now: float = 10.0

    def __call__(self) -> float:
        return self.now


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)


def test_command_acknowledgments_are_fenced_and_time_out() -> None:
    async def scenario() -> None:
        clock = ManualClock()
        runtime = ControllerRuntime(
            clock=clock,
            command_timeout_ms=100,
            heartbeat_interval_ms=100,
            heartbeat_timeout_ms=300,
            controller_epoch="controller-1",
        )
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        await runtime.start_scene("synthetic-sweep")

        first, second = channel.sent
        completed = agent.acknowledge(first["request_id"])
        assert runtime.acknowledge_command(completed).accepted
        assert runtime.status()["pending_commands"] == 1
        assert runtime.acknowledge_command(completed).reason == "unknown_request"

        old_epoch = agent.acknowledge(second["request_id"]).model_copy(
            update={"controller_epoch": "old-controller"}
        )
        assert runtime.acknowledge_command(old_epoch).reason == "controller_epoch_fence"
        old_session = agent.acknowledge(second["request_id"]).model_copy(
            update={"agent_session_id": "old-agent-session"}
        )
        assert runtime.acknowledge_command(old_session).reason == "agent_session_fence"

        clock.now += 0.2
        status = runtime.status()
        assert status["pending_commands"] == 0
        assert status["command_timeouts"] == 1
        assert status["degraded_reason"] == "command_timeout"
        assert not status["render_loop_running"]

        await runtime.start_scene("synthetic-sweep")
        error_command = channel.sent[-2]
        failed = agent.acknowledge(
            error_command["request_id"],
            status="error",
            error_code="prepare_failed",
            detail="synthetic failure",
        )
        assert runtime.acknowledge_command(failed).accepted
        assert runtime.status()["degraded_reason"] == "command_error:prepare_failed"
        assert runtime.status()["command_errors"] == 1
        await runtime.shutdown()

    asyncio.run(scenario())


def test_heartbeat_refreshes_liveness_and_expiry_stops_rendering() -> None:
    async def scenario() -> None:
        clock = ManualClock()
        runtime = ControllerRuntime(
            clock=clock,
            heartbeat_interval_ms=100,
            heartbeat_timeout_ms=300,
            command_timeout_ms=1000,
        )
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        active = await runtime.start_scene("synthetic-sweep")
        agent.attach(active.scene_instance_id, active.playback_session_id)
        for command in channel.sent:
            assert runtime.acknowledge_command(agent.acknowledge(command["request_id"])).accepted
        assert runtime.ingest(agent.position(100)).accepted

        clock.now += 0.2
        heartbeat = agent.heartbeat(200)
        assert runtime.heartbeat(heartbeat).accepted
        assert runtime.heartbeat(heartbeat).reason == "out_of_order"
        old_epoch = agent.heartbeat(210).model_copy(update={"controller_epoch": "old"})
        assert runtime.heartbeat(old_epoch).reason == "controller_epoch_fence"

        clock.now += 0.31
        status = runtime.status()
        assert status["connected_agents"] == []
        assert status["heartbeat_timeouts"] == 1
        assert status["degraded_reason"] == "heartbeat_timeout"
        assert not status["render_loop_running"]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_matching_reconnect_continues_but_mismatch_stops() -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(controller_epoch="controller-1")
        agent = FakeMediaAgent()
        first_channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), first_channel))
        active = await runtime.start_scene("synthetic-sweep")
        agent.attach(active.scene_instance_id, active.playback_session_id)
        assert runtime.ingest(agent.position(1000)).accepted

        runtime.disconnect_agent(agent.agent_id, first_channel)
        agent.reconnect()
        matching = runtime.register_agent(
            agent.hello(agent.snapshot(1200)), MemoryCommandChannel([])
        )
        assert matching.reconciliation == "continue"
        assert runtime.status()["reconciliations"] == 1
        assert runtime.render_loop_running

        agent.reconnect()
        mismatch = agent.snapshot(1300).model_copy(update={"playback_session_id": "other"})
        stopped = runtime.register_agent(agent.hello(mismatch), MemoryCommandChannel([]))
        assert stopped.reconciliation == "stop"
        assert runtime.status()["degraded_reason"] == "reconciliation_required"
        assert not runtime.render_loop_running

        restarted_controller = ControllerRuntime(controller_epoch="controller-2")
        after_restart = restarted_controller.register_agent(
            agent.hello(agent.snapshot(1400)), MemoryCommandChannel([])
        )
        assert after_restart.reconciliation == "stop"
        await runtime.shutdown()

    asyncio.run(scenario())
