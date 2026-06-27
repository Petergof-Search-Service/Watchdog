from datetime import UTC, datetime, timedelta

from handler import find_stuck_files

TIMEOUTS = {"ocr_processing": 3600, "rag_indexing": 900}
NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=UTC)


def _file(status: str, age_seconds: int | None, key: str = "incoming/a.pdf") -> dict:
    changed_at = None
    if age_seconds is not None:
        changed_at = (NOW - timedelta(seconds=age_seconds)).isoformat()
    return {"system_key": key, "status": status, "status_changed_at": changed_at}


def test_stuck_file_is_returned_with_elapsed_and_timeout():
    files = [_file("ocr_processing", age_seconds=4000)]
    stuck = find_stuck_files(files, NOW, TIMEOUTS)
    assert len(stuck) == 1
    assert stuck[0]["file"]["system_key"] == "incoming/a.pdf"
    assert stuck[0]["timeout"] == 3600
    assert stuck[0]["elapsed"] == 4000


def test_fresh_file_is_skipped():
    files = [_file("ocr_processing", age_seconds=100)]
    assert find_stuck_files(files, NOW, TIMEOUTS) == []


def test_terminal_or_unknown_status_is_skipped():
    files = [
        _file("indexed", age_seconds=10_000),
        _file("dead", age_seconds=10_000),
        _file("some_new_status", age_seconds=10_000),
    ]
    assert find_stuck_files(files, NOW, TIMEOUTS) == []


def test_missing_status_changed_at_is_skipped():
    files = [_file("ocr_processing", age_seconds=None)]
    assert find_stuck_files(files, NOW, TIMEOUTS) == []


def test_naive_timestamp_is_treated_as_utc():
    # status_changed_at without tzinfo must not raise and must be read as UTC.
    naive = (NOW - timedelta(seconds=4000)).replace(tzinfo=None).isoformat()
    files = [{"system_key": "k", "status": "ocr_processing", "status_changed_at": naive}]
    stuck = find_stuck_files(files, NOW, TIMEOUTS)
    assert len(stuck) == 1


def test_boundary_equal_to_timeout_is_not_killed():
    # elapsed == timeout is not "exceeded".
    files = [_file("ocr_processing", age_seconds=3600)]
    assert find_stuck_files(files, NOW, TIMEOUTS) == []
