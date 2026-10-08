from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.protocol.models import Discontinuity, PlaybackState
from cueverge.timing.follower import TimecodeFollower


def configured() -> tuple[TimecodeFollower, FakeMediaAgent]:
    follower = TimecodeFollower(stale_after_ms=500)
    follower.begin("scene-1", "playback-1")
    agent = FakeMediaAgent()
    agent.attach("scene-1", "playback-1")
    return follower, agent


def test_playing_estimate_is_bounded_and_becomes_stale() -> None:
    follower, agent = configured()
    assert follower.ingest(agent.position(1000), received_at=10.0).accepted

    current = follower.estimate(10.2)
    assert current is not None
    assert current.position_ms == 1200
    assert not current.degraded

    stale = follower.estimate(11.0)
    assert stale is not None
    assert stale.position_ms == 1500
    assert stale.degraded
    assert stale.reason == "telemetry_stale"


def test_pause_seek_and_loop_reanchor_without_extrapolation() -> None:
    follower, agent = configured()
    follower.ingest(agent.position(1500, state=PlaybackState.PAUSED), 1.0)
    paused = follower.estimate(2.0)
    assert paused is not None
    assert paused.position_ms == 1500

    follower.ingest(agent.position(250, discontinuity=Discontinuity.SEEK), 2.0)
    seek = follower.estimate(2.1)
    assert seek is not None
    assert seek.position_ms == 350

    follower.ingest(agent.position(20, discontinuity=Discontinuity.LOOP), 3.0)
    loop = follower.estimate(3.1)
    assert loop is not None
    assert loop.position_ms == 120


def test_duplicate_out_of_order_and_wrong_session_are_rejected() -> None:
    follower, agent = configured()
    first = agent.position(100)
    assert follower.ingest(first, 1.0).accepted
    assert follower.ingest(first, 1.1).reason == "out_of_order"

    older = first.model_copy(update={"sequence": 0})
    assert follower.ingest(older, 1.2).reason == "out_of_order"

    wrong_session = agent.position(200).model_copy(update={"playback_session_id": "old"})
    assert follower.ingest(wrong_session, 1.3).reason == "session_fence"


def test_disconnect_freezes_immediately() -> None:
    follower, agent = configured()
    follower.ingest(agent.position(1000), 1.0)
    follower.disconnect()
    estimate = follower.estimate(1.1)
    assert estimate is not None
    assert estimate.position_ms == 1000
    assert estimate.reason == "agent_disconnected"
