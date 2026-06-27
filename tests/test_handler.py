from datetime import UTC, datetime, timedelta

import pytest

import handler as handler_module
from handler import handler

FILES_URL = "https://api.test/api/v1/files/service"
PATCH_URL = "https://api.test/api/v1/files/by-key/status"


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch):
    now = datetime(2026, 6, 27, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(handler_module, "_now_utc", lambda: now)
    return now


def _stuck_file(key: str, status: str = "ocr_processing") -> dict:
    old = (datetime(2026, 6, 27, 12, 0, 0, tzinfo=UTC) - timedelta(hours=5))
    return {"system_key": key, "status": status, "status_changed_at": old.isoformat()}


def test_fetch_failure_returns_500_and_no_patch(requests_mock):
    requests_mock.get(FILES_URL, status_code=500)
    patch = requests_mock.patch(PATCH_URL, json={"ok": True})

    assert handler({}, None) == {"statusCode": 500}
    assert patch.call_count == 0


def test_no_stuck_files_returns_200_and_no_patch(requests_mock):
    requests_mock.get(FILES_URL, json={"files": []})
    patch = requests_mock.patch(PATCH_URL, json={"ok": True})

    assert handler({}, None) == {"statusCode": 200}
    assert patch.call_count == 0


def test_stuck_file_is_killed_by_key(requests_mock):
    requests_mock.get(FILES_URL, json={"files": [_stuck_file("incoming/a.pdf")]})
    patch = requests_mock.patch(PATCH_URL, json={"ok": True})

    result = handler({}, None)

    assert result["statusCode"] == 200
    assert '"killed": ["incoming/a.pdf"]' in result["body"]
    assert patch.call_count == 1
    body = patch.last_request.json()
    assert body == {
        "system_key": "incoming/a.pdf",
        "status": "dead",
        "error_message": body["error_message"],
    }
    assert "Watchdog: timeout" in body["error_message"]
    assert patch.last_request.headers["x-service-key"] == "test-service-key"


def test_partial_patch_failure_splits_killed_and_errors(requests_mock):
    requests_mock.get(
        FILES_URL,
        json={"files": [_stuck_file("incoming/ok.pdf"), _stuck_file("incoming/bad.pdf")]},
    )

    def _patch_cb(request, context):
        if "bad" in request.json()["system_key"]:
            context.status_code = 500
            return {}
        context.status_code = 200
        return {"ok": True}

    requests_mock.patch(PATCH_URL, json=_patch_cb)

    result = handler({}, None)
    assert result["statusCode"] == 200
    assert '"killed": ["incoming/ok.pdf"]' in result["body"]
    assert '"errors": ["incoming/bad.pdf"]' in result["body"]
