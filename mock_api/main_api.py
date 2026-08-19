from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from reset_contract import (
    MockSAPDeletedCounts,
    MockSAPResetRequest,
    MockSAPResetResponse,
    MOCK_SAP_INVOICE_FLOW_RESET_ROUTE,
    MOCK_SAP_MASTER_RESET_ROUTE,
    ResetMode,
)
basic_auth = HTTPBasic()
bearer_auth = HTTPBearer()

app = FastAPI(title="Mock SAP + Kefron APIs")

BASE_DIR = Path(__file__).parent
MOCK_DATA_DIR = BASE_DIR / "mock_data"
MOCK_DATA_DIR.mkdir(parents=True, exist_ok=True)

INVOICE_JSON_PATH = MOCK_DATA_DIR / "invoices.json"
PO_JSON_PATH = MOCK_DATA_DIR / "pos.json"
GRN_JSON_PATH = MOCK_DATA_DIR / "grns.json"
POSTED_INVOICE_JSON_PATH = MOCK_DATA_DIR / "posted_invoices.json"


# ---------------------------
# AUTH
# ---------------------------

def verify_sap(credentials: HTTPBasicCredentials = Depends(basic_auth)) -> bool:
    if credentials.username != "sap_user" or credentials.password != "sap_pass":
        raise HTTPException(status_code=401, detail="Invalid SAP credentials")
    return True


def verify_kefron(token: HTTPAuthorizationCredentials = Depends(bearer_auth)) -> bool:
    if token.credentials != "mock_kefron_token":
        raise HTTPException(status_code=401, detail="Invalid Kefron token")
    return True


# ---------------------------
# JSON STORAGE HELPERS
# ---------------------------

def load_json_list(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def save_json_file(path: Path, data: List[Dict[str, Any]]) -> None:
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    temporary_path.replace(path)


def normalize_dt(value: Optional[str]) -> str:
    if value:
        return value
    return datetime.now().isoformat()


def filter_by_since(
    data,
    since_date
):

    if not since_date:
        return data

    try:

        since_dt = datetime.fromisoformat(
            since_date
        )

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail=f"Invalid since_date: {since_date}"
        )

    filtered = []

    for row in data:

        ts = row.get(
            "last_modified"
        )

        if not ts:
            continue

        try:

            row_dt = datetime.fromisoformat(
                ts
            )

        except ValueError:
            continue

        if row_dt > since_dt:

            filtered.append(
                row
            )

    return filtered


# ---------------------------
# REQUEST SCHEMAS
# ---------------------------

class LineItem(BaseModel):
    line_no: int
    description: str
    qty: float
    unit_price: float
    line_amount: float


class InvoiceRequest(BaseModel):
    document_type: str = "invoice"
    invoice_number: str
    po_number: str
    vendor_name: str
    vendor_number: Optional[str] = None
    invoice_date: str
    due_date: Optional[str] = None
    currency: str
    document_subtotal: float
    tax_amount: float
    vat_percent: float
    document_total: float
    amount: Optional[float] = None
    payment_terms: Optional[str] = None
    payment_status: str
    line_items: List[LineItem] = Field(default_factory=list)
    last_modified: Optional[str] = None

class PostedInvoiceRequest(BaseModel):
    document_type: str = "posted_invoice"
    invoice_number: str
    po_number: str
    vendor_name: str
    vendor_number: Optional[str] = None
    invoice_date: str
    due_date: Optional[str] = None
    currency: str

    document_subtotal: float
    tax_amount: float
    vat_percent: Optional[float] = None
    document_total: float
    amount: Optional[float] = None

    payment_status: str = "Posted"
    payment_terms: Optional[str] = None
    posting_status: str = "POSTED"
    sap_document_number: Optional[str] = None
    posting_message: Optional[str] = None
    source_system: str = "AP_AGENT"

    line_items: List[LineItem] = Field(default_factory=list)

    posted_at: Optional[str] = None
    last_modified: Optional[str] = None


class PORequest(BaseModel):
    document_type: str = "po"
    po_number: str
    vendor_name: str
    vendor_number: Optional[str] = None
    po_date: str
    currency: str
    document_subtotal: float
    tax_amount: float
    vat_percent: float
    document_total: float
    amount: Optional[float] = None
    payment_terms: Optional[str] = None
    po_status: str
    line_items: List[LineItem] = Field(default_factory=list)
    last_modified: Optional[str] = None


class GRNRequest(BaseModel):
    document_type: str = "grn"
    gr_number: Optional[str] = None
    grn_number: Optional[str] = None
    po_number: str
    vendor_name: str
    vendor_number: Optional[str] = None
    gr_date: str
    currency: str
    document_subtotal: float
    document_total: float
    amount: Optional[float] = None
    gr_status: str
    line_items: List[LineItem] = Field(default_factory=list)
    last_modified: Optional[str] = None

    def resolved_gr_number(self) -> str:
        return self.gr_number or self.grn_number or ""


def _reset_mock_sap_data(
    mode: ResetMode,
    request: MockSAPResetRequest,
) -> MockSAPResetResponse:
    global INVOICES, POS, GRNS, POSTED_INVOICES

    correlation_id = request.correlation_id or uuid.uuid4().hex
    relevant_paths = [
        INVOICE_JSON_PATH,
        POSTED_INVOICE_JSON_PATH,
    ]
    if mode == ResetMode.MASTER:
        relevant_paths.extend([PO_JSON_PATH, GRN_JSON_PATH])
    _validate_reset_paths(relevant_paths)

    # Reload files at reset time so a long-running API process never leaves
    # externally synchronized JSON records behind because of stale globals.
    INVOICES = load_json_list(INVOICE_JSON_PATH)
    POS = load_json_list(PO_JSON_PATH)
    GRNS = load_json_list(GRN_JSON_PATH)
    POSTED_INVOICES = load_json_list(POSTED_INVOICE_JSON_PATH)
    snapshots = {
        "invoices": list(INVOICES),
        "purchase_orders": list(POS),
        "grns": list(GRNS),
        "posted_invoices": list(POSTED_INVOICES),
    }
    targets = {
        "invoices": INVOICE_JSON_PATH,
        "posted_invoices": POSTED_INVOICE_JSON_PATH,
    }
    if mode == ResetMode.MASTER:
        targets.update(
            {
                "purchase_orders": PO_JSON_PATH,
                "grns": GRN_JSON_PATH,
            }
        )

    deleted = MockSAPDeletedCounts(
        purchase_orders=(
            len(snapshots["purchase_orders"])
            if mode == ResetMode.MASTER
            else 0
        ),
        grns=(
            len(snapshots["grns"])
            if mode == ResetMode.MASTER
            else 0
        ),
        posted_invoices=len(snapshots["posted_invoices"]),
        other_records=len(snapshots["invoices"]),
        files=sum(1 for key in targets if snapshots[key]),
    )
    retained = (
        {}
        if mode == ResetMode.MASTER
        else {
            "purchase_orders": len(POS),
            "grns": len(GRNS),
        }
    )
    if request.dry_run:
        return MockSAPResetResponse(
            success=True,
            reset_mode=mode,
            deleted=MockSAPDeletedCounts(),
            retained=retained,
            warnings=["Preflight only; no Mock SAP records were deleted."],
            correlation_id=correlation_id,
        )

    try:
        for key, path in targets.items():
            save_json_file(path, [])
        INVOICES = []
        POSTED_INVOICES = []
        if mode == ResetMode.MASTER:
            POS = []
            GRNS = []
    except Exception:
        INVOICES = snapshots["invoices"]
        POS = snapshots["purchase_orders"]
        GRNS = snapshots["grns"]
        POSTED_INVOICES = snapshots["posted_invoices"]
        for key, path in {
            "invoices": INVOICE_JSON_PATH,
            "purchase_orders": PO_JSON_PATH,
            "grns": GRN_JSON_PATH,
            "posted_invoices": POSTED_INVOICE_JSON_PATH,
        }.items():
            save_json_file(path, snapshots[key])
        raise

    return MockSAPResetResponse(
        success=True,
        reset_mode=mode,
        deleted=deleted,
        retained=retained,
        warnings=[],
        correlation_id=correlation_id,
        completed_at=datetime.now(timezone.utc),
    )


def _validate_reset_paths(paths: list[Path]) -> None:
    for path in paths:
        if not path.parent.exists() or not os.access(path.parent, os.W_OK):
            raise HTTPException(
                status_code=500,
                detail=f"Mock reset directory is not writable: {path.parent}",
            )
        if path.exists() and not (
            os.access(path, os.R_OK) and os.access(path, os.W_OK)
        ):
            raise HTTPException(
                status_code=500,
                detail=f"Mock reset file is not accessible: {path}",
            )


# ---------------------------
# INITIAL DATA LOAD
# ---------------------------

INVOICES = load_json_list(INVOICE_JSON_PATH)
POS = load_json_list(PO_JSON_PATH)
GRNS = load_json_list(GRN_JSON_PATH)
POSTED_INVOICES = load_json_list(POSTED_INVOICE_JSON_PATH)


# ---------------------------
# ROUTES
# ---------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    }


@app.post(
    MOCK_SAP_INVOICE_FLOW_RESET_ROUTE,
    response_model=MockSAPResetResponse,
)
def reset_invoice_flow(
    request: MockSAPResetRequest,
    auth=Depends(verify_sap),
):
    return _reset_mock_sap_data(ResetMode.INVOICE_FLOW, request)


@app.post(
    MOCK_SAP_MASTER_RESET_ROUTE,
    response_model=MockSAPResetResponse,
)
def reset_master(
    request: MockSAPResetRequest,
    auth=Depends(verify_sap),
):
    return _reset_mock_sap_data(ResetMode.MASTER, request)

#------------DELETE INVOICE ----------

@app.delete("/kefron/invoices/{invoice_number}")
def delete_invoice(
    invoice_number: str,
    auth=Depends(verify_kefron),
):

    global INVOICES

    original_count = len(INVOICES)

    INVOICES = [
        row
        for row in INVOICES
        if row.get("invoice_number") != invoice_number
    ]

    if len(INVOICES) == original_count:

        return {
            "status": "not_found",
            "invoice_number": invoice_number
        }

    with open(
        BASE_DIR / "mock_data/invoices.json",
        "w"
    ) as f:

        json.dump(
            INVOICES,
            f,
            indent=4
        )

    return {
        "status": "deleted",
        "invoice_number": invoice_number
    }

#------------DELETE PO ----------

@app.delete("/sap/po/{po_number}")
def delete_po(
    po_number: str,
    auth=Depends(verify_sap),
):

    global POS

    original_count = len(POS)

    POS = [
        row
        for row in POS
        if row.get("po_number") != po_number
    ]

    if len(POS) == original_count:

        return {
            "status": "not_found",
            "po_number": po_number
        }

    with open(
        BASE_DIR / "mock_data/pos.json",
        "w"
    ) as f:

        json.dump(
            POS,
            f,
            indent=4
        )

    return {
        "status": "deleted",
        "po_number": po_number
    }

#------------DELETE GRN ----------


@app.delete("/sap/gr/{gr_number}")
@app.delete("/sap/grn/{gr_number}")
def delete_grn(
    gr_number: str,
    auth=Depends(verify_sap),
):

    global GRNS

    original_count = len(GRNS)

    GRNS = [
        row
        for row in GRNS
        if row.get("gr_number") != gr_number
    ]

    if len(GRNS) == original_count:

        return {
            "status": "not_found",
            "gr_number": gr_number
        }

    with open(
        BASE_DIR / "mock_data/grns.json",
        "w"
    ) as f:

        json.dump(
            GRNS,
            f,
            indent=4
        )

    return {
        "status": "deleted",
        "gr_number": gr_number
    }
#------------DELETE POSTED INVOICE ----------

@app.delete("/sap/posted-invoices/{invoice_number}")
def delete_posted_invoice(
    invoice_number: str,
    auth=Depends(verify_sap),
):

    global POSTED_INVOICES

    original_count = len(POSTED_INVOICES)

    POSTED_INVOICES = [
        row
        for row in POSTED_INVOICES
        if row.get("invoice_number") != invoice_number
    ]

    if len(POSTED_INVOICES) == original_count:

        return {
            "status": "not_found",
            "invoice_number": invoice_number
        }

    save_json_file(
        POSTED_INVOICE_JSON_PATH,
        POSTED_INVOICES
    )

    return {
        "status": "deleted",
        "invoice_number": invoice_number
    }
# ---------- KEFRON INVOICES ----------

# ---------- LEGACY KEFRON INVOICES ----------
# Disabled for final demo flow.
# Invoices now enter through upload/manual entry into invoice_master.
# API should contain only successfully posted invoices.

@app.get("/kefron/invoices")
def get_invoices(
    since_date: Optional[str] = Query(None),
    auth=Depends(verify_kefron),
):
    return {
        "source": "kefron_legacy_disabled",
        "count": 0,
        "data": [],
        "message": (
            "Source invoice API is disabled. "
            "Invoices now enter through upload/manual entry. "
            "Use /sap/posted-invoices for successfully posted invoices."
        ),
    }


@app.post("/kefron/invoices")
def create_invoice(
    payload: InvoiceRequest,
    auth=Depends(verify_kefron),
):
    raise HTTPException(
        status_code=410,
        detail=(
            "Source invoice API is disabled. "
            "Invoices must enter through upload/manual entry. "
            "Only posted invoices should be pushed to /sap/posted-invoices."
        ),
    )


# ---------- SAP PO ----------

# ---------- SAP POSTED INVOICES ----------

@app.get("/sap/posted-invoices")
def get_posted_invoices(
    since_date: Optional[str] = Query(None),
    auth=Depends(verify_sap),
):
    filtered = filter_by_since(
        POSTED_INVOICES,
        since_date
    )

    return {
        "source": "sap_posted_invoices",
        "count": len(filtered),
        "data": filtered,
    }


@app.post("/sap/posted-invoices")
def create_posted_invoice(
    payload: PostedInvoiceRequest,
    auth=Depends(verify_sap),
):
    global POSTED_INVOICES

    new_invoice = payload.dict()

    new_invoice["posted_invoice_id"] = str(uuid.uuid4())

    new_invoice["amount"] = (
        new_invoice.get("amount")
        or new_invoice["document_total"]
    )

    new_invoice["posted_at"] = normalize_dt(
        new_invoice.get("posted_at")
    )

    new_invoice["last_modified"] = normalize_dt(
        new_invoice.get("last_modified")
    )

    # upsert by invoice_number
    POSTED_INVOICES = [
        row
        for row in POSTED_INVOICES
        if row.get("invoice_number") != new_invoice["invoice_number"]
    ]

    POSTED_INVOICES.append(
        new_invoice
    )

    save_json_file(
        POSTED_INVOICE_JSON_PATH,
        POSTED_INVOICES
    )

    return {
        "status": "success",
        "record": new_invoice,
    }

@app.get("/sap/po")
def get_pos(
    since_date: Optional[str] = Query(None),
    auth=Depends(verify_sap),
):
    filtered = filter_by_since(POS, since_date)
    return {
        "source": "sap_po",
        "count": len(filtered),
        "data": filtered,
    }


@app.post("/sap/po")
def create_po(
    payload: PORequest,
    auth=Depends(verify_sap),
):
    new_po = payload.dict()
    new_po["po_id"] = str(uuid.uuid4())
    new_po["amount"] = new_po.get("amount") or new_po["document_total"]
    new_po["last_modified"] = normalize_dt(new_po.get("last_modified"))

    POS.append(new_po)
    save_json_file(PO_JSON_PATH, POS)

    return {
        "status": "success",
        "record": new_po,
    }


# ---------- SAP GRN ----------

@app.get("/sap/gr")
@app.get("/sap/grn")
def get_grns(
    since_date: Optional[str] = Query(None),
    auth=Depends(verify_sap),
):
    filtered = filter_by_since(GRNS, since_date)
    return {
        "source": "sap_grn",
        "count": len(filtered),
        "data": filtered,
    }


@app.post("/sap/gr")
@app.post("/sap/grn")
def create_grn(
    payload: GRNRequest,
    auth=Depends(verify_sap),
):
    gr_number = payload.resolved_gr_number()
    if not gr_number:
        raise HTTPException(status_code=422, detail="gr_number is required")

    new_grn = payload.dict()
    new_grn["gr_number"] = gr_number
    new_grn.pop("grn_number", None)
    new_grn["grn_id"] = str(uuid.uuid4())
    new_grn["amount"] = new_grn.get("amount") or new_grn["document_total"]
    new_grn["last_modified"] = normalize_dt(new_grn.get("last_modified"))

    GRNS.append(new_grn)
    save_json_file(GRN_JSON_PATH, GRNS)

    return {
        "status": "success",
        "record": new_grn,
    }


if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("SAP_API_PORT", "8001")),
    )
