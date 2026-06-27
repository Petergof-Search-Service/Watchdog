"""
Watchdog cloud function — Yandex Cloud timer (cron) trigger, runs once a minute.

A file is driven through the upload pipeline by other cloud functions (OCR, RAG).
If one of them is killed mid-run (timeout, OOM, quota, crash) nobody updates the
file status, so it stays "in progress" forever. This watchdog lists all
non-terminal files via the API, and marks any that have been in their current
status longer than the configured limit as "dead".

It is stateless: all state lives in the API/DB. It authenticates with the
service key (x-service-key) — never JWT — and talks to two service endpoints:
  GET   {API_BASE_URL}/files/service?status=...   list files (optionally filtered)
  PATCH {API_BASE_URL}/files/by-key/status        set status by system_key

Entrypoint: handler.handler
"""

import json
from datetime import UTC, datetime

import requests

from config import STATUS_TIMEOUTS, settings


def _now_utc() -> datetime:
    return datetime.now(UTC)


def find_stuck_files(
    files: list[dict],
    now: datetime,
    timeouts: dict[str, int],
) -> list[dict]:
    """
    Pure filter: given files and the current time, return those that have
    exceeded their per-status timeout. No I/O — unit-testable in isolation.

    Each returned item is {"file": <file>, "elapsed": int, "timeout": int}.
    Files in a terminal/unknown status (not in `timeouts`) or without a
    parseable `status_changed_at` are skipped.
    """
    stuck = []
    for f in files:
        timeout = timeouts.get(f["status"])
        if timeout is None:
            continue  # terminal or unknown status

        changed_at_str = f.get("status_changed_at")
        if not changed_at_str:
            continue

        changed_at = datetime.fromisoformat(changed_at_str)
        if changed_at.tzinfo is None:
            changed_at = changed_at.replace(tzinfo=UTC)

        elapsed = (now - changed_at).total_seconds()
        if elapsed > timeout:
            stuck.append({"file": f, "elapsed": int(elapsed), "timeout": timeout})

    return stuck


def _get_stuck_files() -> list[dict]:
    """Fetch all non-terminal files from the API and return the stuck ones."""
    url = f"{settings.API_BASE_URL}/files/service"
    resp = requests.get(
        url,
        params={"status": list(STATUS_TIMEOUTS.keys())},
        headers={"x-service-key": settings.CLOUD_FUNCTION_API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    all_files: list[dict] = resp.json().get("files", [])
    return find_stuck_files(all_files, _now_utc(), STATUS_TIMEOUTS)


def _patch_status(system_key: str, error_message: str) -> None:
    url = f"{settings.API_BASE_URL}/files/by-key/status"
    resp = requests.patch(
        url,
        json={
            "system_key": system_key,
            "status": "dead",
            "error_message": error_message,
        },
        headers={
            "x-service-key": settings.CLOUD_FUNCTION_API_KEY,
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    resp.raise_for_status()


def handler(event: dict, context: object) -> dict:
    print("Watchdog started")

    try:
        stuck_files = _get_stuck_files()
    except Exception as exc:
        print(f"Failed to fetch files: {exc}")
        return {"statusCode": 500}

    if not stuck_files:
        print("No stuck files found")
        return {"statusCode": 200}

    killed = []
    errors = []
    for item in stuck_files:
        f = item["file"]
        system_key = f["system_key"]
        status = f["status"]
        elapsed = item["elapsed"]
        timeout = item["timeout"]
        error_msg = f"Watchdog: timeout in status '{status}' ({elapsed}s > {timeout}s)"
        print(f"Killing system_key={system_key}: {error_msg}")
        try:
            _patch_status(system_key, error_msg)
            killed.append(system_key)
        except Exception as exc:
            print(f"Failed to kill system_key={system_key}: {exc}")
            errors.append(system_key)

    print(f"Done: killed={killed}, errors={errors}")
    return {
        "statusCode": 200,
        "body": json.dumps({"killed": killed, "errors": errors}),
    }
