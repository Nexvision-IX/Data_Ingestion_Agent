"""HTTP client for the shared Mock SAP reset contract."""

from __future__ import annotations

import os
import uuid
from typing import Any

import requests

from reset_contract import (
    MockSAPResetRequest,
    ResetMode,
    mock_sap_base_url,
    mock_sap_reset_route,
    mock_sap_reset_timeout,
    model_to_dict,
)


class MockSAPResetClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        url: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ):
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.response_body = response_body

    def technical_details(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status_code": self.status_code,
            "response_body": self.response_body,
            "error": str(self),
        }


def call_mock_api_admin_reset(
    reset_type: ResetMode | str,
    *,
    dry_run: bool = False,
    correlation_id: str | None = None,
    base_url: str | None = None,
    auth: tuple[str, str] | None = None,
    timeout: float | None = None,
    http_client=requests,
) -> dict[str, Any]:
    """Call the exact registered Mock SAP reset endpoint using POST."""
    mode = ResetMode(reset_type)
    url = (
        (base_url or mock_sap_base_url()).rstrip("/")
        + mock_sap_reset_route(mode)
    )
    request = MockSAPResetRequest(
        dry_run=dry_run,
        correlation_id=correlation_id or uuid.uuid4().hex,
    )
    credentials = auth or (
        os.getenv("SAP_USERNAME", "sap_user"),
        os.getenv("SAP_PASSWORD", "sap_pass"),
    )
    try:
        response = http_client.post(
            url,
            json=model_to_dict(request),
            auth=credentials,
            timeout=timeout or mock_sap_reset_timeout(),
        )
    except requests.Timeout as exc:
        raise MockSAPResetClientError(
            "Mock SAP reset timed out.",
            url=url,
        ) from exc
    except requests.ConnectionError as exc:
        raise MockSAPResetClientError(
            "Mock SAP API is unavailable.",
            url=url,
        ) from exc
    except requests.RequestException as exc:
        raise MockSAPResetClientError(
            f"Mock SAP reset request failed: {exc}",
            url=url,
        ) from exc

    if response.status_code == 404:
        message = (
            "Mock SAP reset endpoint was not found. "
            "Check the configured reset route."
        )
    elif response.status_code in {401, 403}:
        message = (
            "Mock SAP reset was not authorized. Check the configured "
            "credentials and reset permissions."
        )
    elif response.status_code >= 500:
        message = "Mock SAP API encountered an internal reset failure."
    elif response.status_code >= 400:
        message = (
            "Mock SAP reset request was rejected "
            f"with HTTP {response.status_code}."
        )
    else:
        try:
            return response.json()
        except ValueError as exc:
            raise MockSAPResetClientError(
                "Mock SAP reset returned an invalid JSON response.",
                url=url,
                status_code=response.status_code,
                response_body=response.text,
            ) from exc

    raise MockSAPResetClientError(
        message,
        url=url,
        status_code=response.status_code,
        response_body=response.text,
    )
