import os

import pytest

# config.Settings() reads these at import time — set them before any test imports it.
os.environ.setdefault("API_BASE_URL", "https://api.test/api/v1")
os.environ.setdefault("CLOUD_FUNCTION_API_KEY", "test-service-key")


@pytest.fixture
def api_base_url() -> str:
    return os.environ["API_BASE_URL"]
