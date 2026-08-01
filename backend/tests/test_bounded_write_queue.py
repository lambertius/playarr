"""DB-002 load coverage for bounded and coalesced cosmetic writes."""
import threading
import time

from app.pipeline_url.write_queue import _DBWriteQueue


def test_cosmetic_progress_is_bounded_coalesced_and_reports_backpressure():
    writes = []
    entered = threading.Event()
    release = threading.Event()
    queue = _DBWriteQueue(max_pending=2)

    def hold_writer():
        entered.set()
        release.wait(timeout=2)

    assert queue.submit_async(hold_writer) is True
    assert entered.wait(timeout=1)
    for value in range(1000):
        assert queue.submit_async(
            lambda current=value: writes.append(current),
            coalesce_key=("job-progress", 42),
        ) is True
    assert queue.submit_async(lambda: writes.append("filler")) is True
    assert queue.submit_async(lambda: writes.append("rejected")) is False

    health = queue.stats()
    assert health["pending"] <= health["max_pending"] == 2
    assert health["coalesced"] == 999
    assert health["rejected"] == 1
    assert health["oldest_age_seconds"] >= 0

    release.set()
    queue.drain()
    assert 999 in writes
    assert "rejected" not in writes
