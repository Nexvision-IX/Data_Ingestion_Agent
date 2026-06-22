from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()


def _get_env(
    primary_name: str,
    fallback_name: str | None = None,
    default: str = "",
) -> str:
    value = os.getenv(primary_name)
    if value not in (None, ""):
        return value

    if fallback_name:
        fallback_value = os.getenv(fallback_name)
        if fallback_value not in (None, ""):
            return fallback_value

    return default


# -----------------------------------
# MOCK SAP CONFIG
# -----------------------------------

SAP_BASE_URL = _get_env(
    "SAP_BASE_URL",
    "MOCK_API_BASE_URL",
    "http://127.0.0.1:8001",
).rstrip("/")

SAP_USERNAME = os.getenv(
    "SAP_USERNAME",
    "",
)

SAP_PASSWORD = os.getenv(
    "SAP_PASSWORD",
    "",
)


# -----------------------------------
# MOCK KEFRON CONFIG
# -----------------------------------

KEFRON_BASE_URL = _get_env(
    "KEFRON_BASE_URL",
    "MOCK_API_BASE_URL",
    "http://127.0.0.1:8001",
).rstrip("/")

KEFRON_API_KEY = os.getenv(
    "KEFRON_API_KEY",
    "",
)