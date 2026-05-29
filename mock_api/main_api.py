from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

basic_auth = HTTPBasic()
bearer_auth = HTTPBearer()

app = FastAPI(title="Mock SAP + Kefron APIs")

BASE_DIR = Path(__file__).parent
MOCK_DATA_DIR = BASE_DIR / "mock_data"
MOCK_DATA_DIR.mkdir(parents=True, exist_ok=True)

INVOICE_JSON_PATH = MOCK_DATA_DIR / "invoices.json"
PO_JSON_PATH = MOCK_DATA_DIR / "pos.json"
GRN_JSON_PATH = MOCK_DATA_DIR / "grns.json"


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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def normalize_dt(value: Optional[str]) -> str:
    if value:
        return value
    return datetime.now().isoformat()


def filter_by_since(data: List[Dict[str, Any]], since_date: Optional[str]) -> List[Dict[str, Any]]:
    if not since_date:
        return data

    try:
        since_dt = (
    datetime
    .fromisoformat(since_date)
    .date()
)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid since_date: {since_date}") from exc

    filtered: List[Dict[str, Any]] = []
    for row in data:
        ts = row.get("last_modified")
        if not ts:
            continue
        try:
            row_dt = (
    datetime
    .fromisoformat(ts)
    .date()
)
        except ValueError:
            continue
        if row_dt > since_dt:
            filtered.append(row)

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
    invoice_date: str
    currency: str
    document_subtotal: float
    tax_amount: float
    vat_percent: float
    document_total: float
    amount: Optional[float] = None
    payment_status: str
    line_items: List[LineItem] = Field(default_factory=list)
    last_modified: Optional[str] = None


class PORequest(BaseModel):
    document_type: str = "po"
    po_number: str
    vendor_name: str
    po_date: str
    currency: str
    document_subtotal: float
    tax_amount: float
    vat_percent: float
    document_total: float
    amount: Optional[float] = None
    po_status: str
    line_items: List[LineItem] = Field(default_factory=list)
    last_modified: Optional[str] = None


class GRNRequest(BaseModel):
    document_type: str = "grn"
    gr_number: Optional[str] = None
    grn_number: Optional[str] = None
    po_number: str
    vendor_name: str
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


# ---------------------------
# INITIAL DATA LOAD
# ---------------------------

INVOICES = load_json_list(INVOICE_JSON_PATH)
POS = load_json_list(PO_JSON_PATH)
GRNS = load_json_list(GRN_JSON_PATH)


# ---------------------------
# ROUTES
# ---------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    }

#------------DELETE INVOICE ----------

@app.delete("/kefron/invoices/{invoice_number}")
def delete_invoice(invoice_number: str):

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
def delete_po(po_number: str):

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
def delete_grn(gr_number: str):

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
# ---------- KEFRON INVOICES ----------

@app.get("/kefron/invoices")
def get_invoices(
    since_date: Optional[str] = Query(None),
    auth=Depends(verify_kefron),
):
    filtered = filter_by_since(INVOICES, since_date)
    return {
        "source": "kefron",
        "count": len(filtered),
        "data": filtered,
    }


@app.post("/kefron/invoices")
def create_invoice(
    payload: InvoiceRequest,
    auth=Depends(verify_kefron),
):
    new_invoice = payload.dict()
    new_invoice["invoice_id"] = str(uuid.uuid4())
    new_invoice["amount"] = new_invoice.get("amount") or new_invoice["document_total"]
    new_invoice["last_modified"] = normalize_dt(new_invoice.get("last_modified"))

    INVOICES.append(new_invoice)
    save_json_file(INVOICE_JSON_PATH, INVOICES)

    return {
        "status": "success",
        "record": new_invoice,
    }


# ---------- SAP PO ----------

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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
