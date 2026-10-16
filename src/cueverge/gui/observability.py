import shutil
import sqlite3
import threading
from collections import Counter, deque
from dataclasses import dataclass
from time import perf_counter

from cueverge.gui.repository import GuiRepository


@dataclass(frozen=True)
class ApiLatencySnapshot:
    request_count: int
    error_count: int
    mean_ms: float
    p95_ms: float
    maximum_ms: float


class GuiObservability:
    def __init__(self, repository: GuiRepository, data_root: str) -> None:
        self.repository = repository
        self.data_root = data_root
        self._latencies: deque[float] = deque(maxlen=5000)
        self._status_counts: Counter[int] = Counter()
        self._browser_errors: deque[dict[str, str]] = deque(maxlen=100)
        self._lock = threading.Lock()

    def record_api(self, duration_ms: float, status_code: int) -> None:
        with self._lock:
            self._latencies.append(duration_ms)
            self._status_counts[status_code] += 1

    def record_browser_error(self, message: str, source: str, occurred_at: str) -> None:
        with self._lock:
            self._browser_errors.append(
                {"message": message, "source": source, "occurred_at": occurred_at}
            )

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            latencies = sorted(self._latencies)
            statuses = dict(self._status_counts)
            browser_errors = tuple(self._browser_errors)
        request_count = len(latencies)
        p95_index = max(0, min(request_count - 1, int(request_count * 0.95) - 1))
        latency = ApiLatencySnapshot(
            request_count=request_count,
            error_count=sum(
                count for status, count in statuses.items() if status >= 400
            ),
            mean_ms=round(sum(latencies) / request_count, 2) if request_count else 0,
            p95_ms=round(latencies[p95_index], 2) if request_count else 0,
            maximum_ms=round(latencies[-1], 2) if request_count else 0,
        )
        render_states = Counter(
            str(row["state"])
            for row in self._query("SELECT state FROM render_jobs")
        )
        asset_states = Counter(
            str(row["status"])
            for row in self._query("SELECT status FROM assets")
        )
        publication_states = Counter(
            str(row["state"])
            for row in self._query_optional("SELECT state FROM published_packages")
        )
        presence = self.repository.list_device_presence()
        usage = shutil.disk_usage(self.data_root)
        audits = self.repository.list_audit_events(limit=1000)
        simulation_runs = sum(event.action == "simulation_scenario.run" for event in audits)
        return {
            "api_latency": latency.__dict__,
            "http_status_counts": {
                str(status): count for status, count in statuses.items()
            },
            "background_jobs": dict(render_states),
            "asset_states": dict(asset_states),
            "websocket_health": {
                "connected": sum(item.disconnected_at_ms is None for item in presence),
                "disconnected": sum(item.disconnected_at_ms is not None for item in presence),
            },
            "browser_errors": list(browser_errors),
            "storage": {
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
            },
            "simulation_runs": simulation_runs,
            "publication_states": dict(publication_states),
        }

    def _query(self, sql: str) -> tuple[sqlite3.Row, ...]:
        connection = sqlite3.connect(self.repository.database_path)
        connection.row_factory = sqlite3.Row
        try:
            return tuple(connection.execute(sql).fetchall())
        finally:
            connection.close()

    def _query_optional(self, sql: str) -> tuple[sqlite3.Row, ...]:
        try:
            return self._query(sql)
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error):
                raise
            return ()


class ApiTimer:
    def __init__(self) -> None:
        self.started = perf_counter()

    def elapsed_ms(self) -> float:
        return (perf_counter() - self.started) * 1000
