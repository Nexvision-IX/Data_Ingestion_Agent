"""Environment-backed configuration for structured source clients."""

from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()


SAFE_DEPLOYMENT_ENVIRONMENTS = {
    "aws",
    "demo",
    "staging",
    "stage",
    "production",
    "prod",
}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is required when APP_ENV is a deployment environment."
        )
    return value


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
SAP_API_PORT = int(os.getenv("SAP_API_PORT", "8001"))
LOCAL_MOCK_API_URL = f"http://127.0.0.1:{SAP_API_PORT}"

if APP_ENV in SAFE_DEPLOYMENT_ENVIRONMENTS:
    MOCK_API_BASE_URL = _required("MOCK_API_BASE_URL").rstrip("/")
    SAP_BASE_URL = _required("SAP_BASE_URL").rstrip("/")
    KEFRON_BASE_URL = _required("KEFRON_BASE_URL").rstrip("/")
    SAP_USERNAME = _required("SAP_USERNAME")
    SAP_PASSWORD = _required("SAP_PASSWORD")
    KEFRON_API_KEY = _required("KEFRON_API_KEY")
else:
    MOCK_API_BASE_URL = os.getenv(
        "MOCK_API_BASE_URL",
        LOCAL_MOCK_API_URL,
    ).rstrip("/")
    SAP_BASE_URL = os.getenv(
        "SAP_BASE_URL",
        MOCK_API_BASE_URL,
    ).rstrip("/")
    KEFRON_BASE_URL = os.getenv(
        "KEFRON_BASE_URL",
        MOCK_API_BASE_URL,
    ).rstrip("/")
    SAP_USERNAME = os.getenv("SAP_USERNAME", "sap_user")
    SAP_PASSWORD = os.getenv("SAP_PASSWORD", "sap_pass")
    KEFRON_API_KEY = os.getenv("KEFRON_API_KEY", "mock_kefron_token")
