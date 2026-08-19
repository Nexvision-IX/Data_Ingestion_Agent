"""Integration tests for the Admin Data Manager reset contract."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent_app"), str(ROOT)]
os.environ["APP_ENV"] = "test"
os.environ["ALLOW_DESTRUCTIVE_MASTER_RESET"] = "true"
os.environ["ALLOW_DESTRUCTIVE_AGENT_RESET"] = "true"

from reset_client import (  # noqa: E402
    MockSAPResetClientError,
    call_mock_api_admin_reset,
)
from reset_contract import (  # noqa: E402
    MOCK_SAP_INVOICE_FLOW_RESET_ROUTE,
    MOCK_SAP_MASTER_RESET_ROUTE,
)
from reset_workflow import run_staged_reset  # noqa: E402


AUTH = ("sap_user", "sap_pass")


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _RecordingHTTPClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def main() -> int:
    import mock_api.main_api as mock_api

    original = _isolate_mock_api(mock_api)
    client = TestClient(mock_api.app)
    try:
        registered = {
            (method, route.path)
            for route in mock_api.app.routes
            for method in getattr(route, "methods", set())
        }
        assert ("POST", MOCK_SAP_MASTER_RESET_ROUTE) in registered
        assert ("POST", MOCK_SAP_INVOICE_FLOW_RESET_ROUTE) in registered

        # Test 1 - registered master route and structured counts.
        _seed_mock(mock_api, pos=2, grns=1, invoices=1, posted=1)
        response = client.post(
            MOCK_SAP_MASTER_RESET_ROUTE,
            json={"correlation_id": "test-1"},
            auth=AUTH,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["deleted"]["purchase_orders"] == 2
        assert payload["deleted"]["grns"] == 1
        assert payload["deleted"]["posted_invoices"] == 1
        assert payload["deleted"]["other_records"] == 1
        print("[PASS] Test 1 - registered master reset route")

        # Test 2 - the UI client helper uses POST and the registered path.
        recording = _RecordingHTTPClient(
            _Response(
                payload={
                    "success": True,
                    "reset_mode": "master",
                    "deleted": {},
                }
            )
        )
        call_mock_api_admin_reset(
            "master",
            base_url="http://mock-sap.test",
            auth=AUTH,
            http_client=recording,
        )
        assert recording.calls[0][0] == (
            "http://mock-sap.test" + MOCK_SAP_MASTER_RESET_ROUTE
        )
        assert recording.calls[0][1]["json"]["dry_run"] is False
        print("[PASS] Test 2 - Streamlit reset helper contract")

        # Test 3 and Test 7 - empty and repeated master reset.
        empty = client.post(
            MOCK_SAP_MASTER_RESET_ROUTE,
            json={},
            auth=AUTH,
        )
        assert empty.status_code == 200
        assert all(value == 0 for value in empty.json()["deleted"].values())
        repeated = client.post(
            MOCK_SAP_MASTER_RESET_ROUTE,
            json={},
            auth=AUTH,
        )
        assert repeated.status_code == 200
        assert all(
            value == 0 for value in repeated.json()["deleted"].values()
        )
        print("[PASS] Test 3 - empty environment is idempotent")
        print("[PASS] Test 7 - repeated master reset is safe")

        # Test 4 - a missing endpoint fails in preflight before deletion.
        deletion_calls = []
        missing_client = _RecordingHTTPClient(
            _Response(404, text='{"detail":"Not Found"}')
        )
        previous_route = os.environ.get("MOCK_SAP_MASTER_RESET_ROUTE")
        os.environ["MOCK_SAP_MASTER_RESET_ROUTE"] = (
            "/invalid-reset-route"
        )

        def missing_preflight():
            call_mock_api_admin_reset(
                "master",
                base_url="http://mock-sap.test",
                auth=AUTH,
                http_client=missing_client,
            )
            return {}

        try:
            missing_result = run_staged_reset(
                reset_mode="master",
                preflight=missing_preflight,
                operations=[
                    (
                        "MASTER_DATABASE_RESET",
                        lambda: deletion_calls.append("deleted") or {},
                    )
                ],
            )
        finally:
            if previous_route is None:
                os.environ.pop("MOCK_SAP_MASTER_RESET_ROUTE", None)
            else:
                os.environ["MOCK_SAP_MASTER_RESET_ROUTE"] = previous_route
        assert missing_result["status"] == "PREFLIGHT_FAILED"
        assert missing_result["failed_stage"] == "PREFLIGHT"
        assert deletion_calls == []
        assert "endpoint was not found" in (
            missing_result["technical_details"]["error"]
        )
        assert missing_client.calls[0][0].endswith(
            "/invalid-reset-route"
        )
        print("[PASS] Test 4 - missing endpoint prevents deletion")

        with tempfile.TemporaryDirectory() as temp_dir:
            agent_engine, master_engine = _databases(temp_dir)
            try:
                # Test 5 - invoice-flow reset retains PO and GRN.
                _seed_databases(agent_engine, master_engine)
                _seed_mock(mock_api, pos=1, grns=1, invoices=1, posted=1)
                invoice_result = _run_database_reset(
                    mode="invoice_flow",
                    agent_engine=agent_engine,
                    master_engine=master_engine,
                    mock_client=client,
                )
                assert invoice_result["success"] is True
                _assert_agent_empty(agent_engine)
                _assert_master_counts(
                    master_engine,
                    invoices=0,
                    posted=0,
                    pos=1,
                    grns=1,
                )
                assert len(mock_api.POS) == 1
                assert len(mock_api.GRNS) == 1
                assert mock_api.INVOICES == []
                assert mock_api.POSTED_INVOICES == []
                print("[PASS] Test 5 - invoice-flow reset retains PO/GRN")

                # Test 6 and Test 9 - master reset and no orphan rows.
                _seed_databases(
                    agent_engine,
                    master_engine,
                    only_missing=True,
                )
                _seed_mock(
                    mock_api,
                    pos=1,
                    grns=1,
                    invoices=1,
                    posted=1,
                )
                master_result = _run_database_reset(
                    mode="master",
                    agent_engine=agent_engine,
                    master_engine=master_engine,
                    mock_client=client,
                )
                assert master_result["success"] is True
                _assert_agent_empty(agent_engine)
                _assert_master_counts(
                    master_engine,
                    invoices=0,
                    posted=0,
                    pos=0,
                    grns=0,
                )
                assert (
                    mock_api.INVOICES
                    == mock_api.POS
                    == mock_api.GRNS
                    == mock_api.POSTED_INVOICES
                    == []
                )
                print("[PASS] Test 6 - master reset clears all demo data")
                print("[PASS] Test 9 - no orphan dependent rows")
            finally:
                agent_engine.dispose()
                master_engine.dispose()

        # Test 8 - partial failure and retry skip completed stages.
        stage_calls = []

        def completed_stage():
            stage_calls.append("completed")
            return {"deleted": 1}

        def failed_stage():
            stage_calls.append("failed")
            raise RuntimeError("simulated late failure")

        partial = run_staged_reset(
            reset_mode="master",
            preflight=lambda: {"ok": True},
            operations=[
                ("AP_AGENT_RESET", completed_stage),
                ("MOCK_SAP_RESET", failed_stage),
            ],
        )
        assert partial["status"] == "PARTIAL_FAILURE"
        retried = run_staged_reset(
            reset_mode="master",
            preflight=lambda: {"ok": True},
            operations=[
                ("AP_AGENT_RESET", completed_stage),
                (
                    "MOCK_SAP_RESET",
                    lambda: stage_calls.append("retry") or {},
                ),
            ],
            resume_result=partial,
        )
        assert retried["success"] is True
        assert stage_calls == ["completed", "failed", "retry"]
        print("[PASS] Test 8 - partial failure retry skips completed stages")
    finally:
        _restore_mock_api(mock_api, original)

    print("[SUCCESS] Admin reset contract tests passed.")
    return 0


def _isolate_mock_api(module):
    original = {
        "paths": (
            module.INVOICE_JSON_PATH,
            module.PO_JSON_PATH,
            module.GRN_JSON_PATH,
            module.POSTED_INVOICE_JSON_PATH,
        ),
        "data": (
            list(module.INVOICES),
            list(module.POS),
            list(module.GRNS),
            list(module.POSTED_INVOICES),
        ),
    }
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    module.INVOICE_JSON_PATH = root / "invoices.json"
    module.PO_JSON_PATH = root / "pos.json"
    module.GRN_JSON_PATH = root / "grns.json"
    module.POSTED_INVOICE_JSON_PATH = root / "posted_invoices.json"
    original["temp"] = temp
    _seed_mock(module, pos=0, grns=0, invoices=0, posted=0)
    return original


def _restore_mock_api(module, original):
    (
        module.INVOICE_JSON_PATH,
        module.PO_JSON_PATH,
        module.GRN_JSON_PATH,
        module.POSTED_INVOICE_JSON_PATH,
    ) = original["paths"]
    (
        module.INVOICES,
        module.POS,
        module.GRNS,
        module.POSTED_INVOICES,
    ) = original["data"]
    original["temp"].cleanup()


def _seed_mock(module, *, pos, grns, invoices, posted):
    module.POS = [{"po_number": f"PO-{index}"} for index in range(pos)]
    module.GRNS = [
        {"gr_number": f"GRN-{index}"} for index in range(grns)
    ]
    module.INVOICES = [
        {"invoice_number": f"INV-{index}"} for index in range(invoices)
    ]
    module.POSTED_INVOICES = [
        {"invoice_number": f"POSTED-{index}"} for index in range(posted)
    ]
    module.save_json_file(module.PO_JSON_PATH, module.POS)
    module.save_json_file(module.GRN_JSON_PATH, module.GRNS)
    module.save_json_file(module.INVOICE_JSON_PATH, module.INVOICES)
    module.save_json_file(
        module.POSTED_INVOICE_JSON_PATH,
        module.POSTED_INVOICES,
    )


def _databases(temp_dir):
    from ap_database.agent_artifact_models import ArtifactBase
    from ap_database.master_models import MasterBase
    from app.db import Base
    import app.models  # noqa: F401 - register Agent tables

    agent = create_engine(
        f"sqlite:///{Path(temp_dir, 'agent.db').as_posix()}",
        future=True,
    )
    master = create_engine(
        f"sqlite:///{Path(temp_dir, 'master.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(agent)
    ArtifactBase.metadata.create_all(agent)
    MasterBase.metadata.create_all(master)
    return agent, master


def _seed_databases(agent_engine, master_engine, only_missing=False):
    from ap_database.agent_artifact_models import InvoiceArtifact
    from ap_database.master_models import (
        InvoiceMaster,
        SapGRNMaster,
        SapPOMaster,
        SapPostedInvoiceMaster,
    )
    from app.models import (
        Communication,
        ExceptionCase,
        Invoice,
        InvoiceLine,
        POGRNConsumptionLedger,
        PostingAttempt,
        ValidationResult,
        WorkflowEvent,
    )

    with Session(master_engine) as db:
        if not only_missing or not db.get(SapPOMaster, "PO-RESET"):
            db.merge(SapPOMaster(po_number="PO-RESET"))
        if not only_missing or not db.get(SapGRNMaster, "GRN-RESET"):
            db.merge(SapGRNMaster(gr_number="GRN-RESET"))
        db.merge(InvoiceMaster(invoice_number="INV-RESET"))
        db.merge(
            SapPostedInvoiceMaster(invoice_number="POSTED-RESET")
        )
        db.commit()

    with Session(agent_engine) as db:
        invoice = Invoice(
            source="TEST",
            original_filename="reset.json",
            vendor_name="Reset Vendor",
            vendor_number="RESET_VENDOR",
            invoice_number="INV-RESET",
            invoice_date=date(2026, 7, 20),
            po_number="PO-RESET",
            currency="INR",
            subtotal=10,
            tax_amount=0,
            total_amount=10,
            status="READY_FOR_POSTING",
            extraction_confidence=1,
            extraction_raw={},
        )
        db.add(invoice)
        db.flush()
        exception = ExceptionCase(
            invoice_id=invoice.id,
            category="GRN_MISSING",
        )
        db.add(exception)
        db.flush()
        db.add_all(
            [
                InvoiceLine(
                    invoice_id=invoice.id,
                    line_number=1,
                    description="Reset line",
                    quantity=1,
                    unit_price=10,
                    tax_rate=0,
                    po_item="00001",
                ),
                ValidationResult(
                    invoice_id=invoice.id,
                    rule_code="TEST",
                    rule_name="Reset test",
                    passed=False,
                    message="Reset",
                    details={},
                ),
                Communication(
                    invoice_id=invoice.id,
                    exception_id=exception.id,
                    subject="Reset",
                    body="Reset",
                ),
                WorkflowEvent(
                    invoice_id=invoice.id,
                    event_type="RESET_TEST",
                    agent_name="Test",
                    message="Reset",
                    metadata_json={},
                ),
                PostingAttempt(
                    invoice_id=invoice.id,
                    status="FAILED",
                    message="Reset",
                ),
                POGRNConsumptionLedger(
                    invoice_id=invoice.id,
                    invoice_number=invoice.invoice_number,
                    business_invoice_key=(
                        "1000|RESETVENDOR|INVRESET|2026"
                    ),
                    company_code="1000",
                    fiscal_year=2026,
                    po_number="PO-RESET",
                    po_item="00001",
                    active_key=(
                        "1000|RESETVENDOR|INVRESET|2026:"
                        "PO-RESET:00001"
                    ),
                    grn_number="GRN-RESET",
                    quantity=1,
                    amount=10,
                    ledger_status="RESERVED",
                    source="AP_AGENT",
                    reason="Reset test",
                ),
                InvoiceArtifact(
                    invoice_number=invoice.invoice_number,
                    artifact_type="ORIGINAL",
                    storage_backend="local",
                    uri="file:///reset.pdf",
                ),
            ]
        )
        db.commit()


def _run_database_reset(
    *,
    mode,
    agent_engine,
    master_engine,
    mock_client,
):
    import ap_database.master_repository as repository
    from app.services.demo_reset_service import reset_agent_invoice_flow

    original_get_engine = repository.get_master_engine
    repository.get_master_engine = lambda: master_engine
    try:
        master_operation = (
            repository.reset_invoice_flow_data
            if mode == "invoice_flow"
            else repository.reset_demo_environment
        )
        route = (
            MOCK_SAP_INVOICE_FLOW_RESET_ROUTE
            if mode == "invoice_flow"
            else MOCK_SAP_MASTER_RESET_ROUTE
        )
        return run_staged_reset(
            reset_mode=mode,
            preflight=lambda: _test_preflight(mock_client, route),
            operations=[
                ("MASTER_DATABASE_RESET", master_operation),
                (
                    "AP_AGENT_RESET",
                    lambda: {
                        "deleted_rows": reset_agent_invoice_flow(
                            agent_engine
                        )
                    },
                ),
                (
                    "MOCK_SAP_RESET",
                    lambda: _test_mock_reset(mock_client, route),
                ),
            ],
        )
    finally:
        repository.get_master_engine = original_get_engine


def _test_preflight(client, route):
    response = client.post(
        route,
        json={"dry_run": True},
        auth=AUTH,
    )
    assert response.status_code == 200
    return response.json()


def _test_mock_reset(client, route):
    response = client.post(route, json={}, auth=AUTH)
    assert response.status_code == 200
    return response.json()


def _assert_agent_empty(engine):
    from ap_database.agent_artifact_models import InvoiceArtifact
    from app.models import (
        Communication,
        ExceptionCase,
        Invoice,
        InvoiceLine,
        POGRNConsumptionLedger,
        PostingAttempt,
        ValidationResult,
        WorkflowEvent,
    )

    with Session(engine) as db:
        for model in (
            InvoiceArtifact,
            InvoiceLine,
            ValidationResult,
            Communication,
            ExceptionCase,
            WorkflowEvent,
            PostingAttempt,
            POGRNConsumptionLedger,
            Invoice,
        ):
            assert db.scalar(
                select(func.count()).select_from(model)
            ) == 0


def _assert_master_counts(
    engine,
    *,
    invoices,
    posted,
    pos,
    grns,
):
    from ap_database.master_models import (
        InvoiceMaster,
        SapGRNMaster,
        SapPOMaster,
        SapPostedInvoiceMaster,
    )

    expected = {
        InvoiceMaster: invoices,
        SapPostedInvoiceMaster: posted,
        SapPOMaster: pos,
        SapGRNMaster: grns,
    }
    with Session(engine) as db:
        for model, count in expected.items():
            assert db.scalar(
                select(func.count()).select_from(model)
            ) == count


if __name__ == "__main__":
    raise SystemExit(main())
