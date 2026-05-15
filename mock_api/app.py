from fastapi import FastAPI, Query
from datetime import datetime
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
import json
from pathlib import Path

app = FastAPI()
basic_auth = HTTPBasic()
bearer_auth = HTTPBearer()
app = FastAPI(title="Mock SAP + Kefron APIs")
BASE_DIR = Path(__file__).parent

def verify_sap(credentials: HTTPBasicCredentials = Depends(basic_auth)):
    if credentials.username != "sap_user" or credentials.password != "sap_pass":
        raise HTTPException(status_code=401, detail="Invalid SAP credentials")
    return True

def verify_kefron(token: HTTPAuthorizationCredentials = Depends(bearer_auth)):
    if token.credentials != "mock_kefron_token":
        raise HTTPException(status_code=401, detail="Invalid Kefron token")
    return True
# ---------------------------
# MOCK DATA
# ---------------------------

try:
    with open(BASE_DIR / "mock_data/invoices.json", "r") as f:
        INVOICES = json.load(f)
    print("Invoices Loaded Successfully")

except Exception as e:
    print("Invoices JSON Error:", e)


try:
    with open(BASE_DIR / "mock_data/pos.json", "r") as f:
        POS = json.load(f)
    print("PO JSON Loaded Successfully")

except Exception as e:
    print("PO JSON Error:", e)


try:
    with open(BASE_DIR / "mock_data/grns.json", "r") as f:
        GRNS = json.load(f)
    print("GRN JSON Loaded Successfully")

except Exception as e:
    print("GRN JSON Error:", e)
# ---------------------------
# HELPERS
# ---------------------------

from datetime import datetime

def filter_by_since(data, since_date):

    if not since_date:
        return data

    since_dt = datetime.fromisoformat(since_date)

    filtered = []

    for row in data:

        row_dt = datetime.fromisoformat(
            row["last_modified"]
        )

        if row_dt > since_dt:

            filtered.append(row)

    return filtered

# ---------------------------
# KEFRON-LIKE INVOICE API
# ---------------------------
# ---------------------------
# SAP-LIKE PO API
# ---------------------------

@app.get("/kefron/invoices")
def get_invoices(since_date: str = Query(None), auth=Depends(verify_kefron)):
    filtered = filter_by_since(INVOICES, since_date)
    return {
        "source": "kefron",
        "count": len(filtered),
        "data": filtered
    }

@app.get("/sap/po")
def get_pos(since_date: str = Query(None), auth=Depends(verify_sap)):
    filtered = filter_by_since(POS, since_date)
    return {
        "source": "sap_po",
        "count": len(filtered),
        "data": filtered
    }

@app.get("/sap/gr")
def get_grns(since_date: str = Query(None)):

    filtered = filter_by_since(GRNS, since_date)

    return {
        "source": "sap_gr",
        "count": len(filtered),
        "data": filtered
    }
# ---------------------------
# HEALTH CHECK
# ---------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    }