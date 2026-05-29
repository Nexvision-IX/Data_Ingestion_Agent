
import json
import uuid
from datetime import datetime
from pathlib import Path
import sqlite3

import pandas as pd
import requests
import streamlit as st
from ingestion.master_ingestion import (

    delete_invoice,
    delete_po,
    delete_grn,

    clear_invoice_table,
    clear_po_table,
    clear_grn_table,

    keep_latest_rows,
    reset_demo_environment
)

from pipeline_runner import process_invoice_pipeline, sync_structured_sources

# -----------------------------------
# DATABASE PATH
# -----------------------------------

DB_PATH = "data/master/ap_master.db"
API_BASE_URL = "https://data-ingestion-agent.onrender.com"
# -----------------------------------
# INPUT DIRECTORY
# -----------------------------------

INPUT_DIR = Path("unstructured_ingestion/unstructured_inputs")
INPUT_DIR.mkdir(parents=True, exist_ok=True)


# -----------------------------------
# HELPERS
# -----------------------------------

def save_uploaded_file(uploaded_file):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    extension = Path(uploaded_file.name).suffix.lower()
    internal_filename = f"{timestamp}_{unique_id}{extension}"
    save_path = INPUT_DIR / internal_filename

    with open(save_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    return save_path


def load_table_data(table_name, limit=10):
    conn = sqlite3.connect(DB_PATH)
    try:
        query = f"""
            SELECT *
            FROM {table_name}
            ORDER BY ROWID DESC
            LIMIT {limit}
        """
        return pd.read_sql_query(query, conn)
    finally:
        conn.close()


def get_table_count(table_name):
    conn = sqlite3.connect(DB_PATH)
    try:
        query = f"""
            SELECT COUNT(*) AS total
            FROM {table_name}
        """
        df = pd.read_sql_query(query, conn,)
        return int(df["total"][0])
    finally:
        conn.close()


def init_line_items(state_key, default_item):
    if state_key not in st.session_state:
        st.session_state[state_key] = [default_item]


def build_line_item_payload(items, qty_key="qty", amount_key="line_amount"):
    payload_items = []
    subtotal = 0.0

    for i, item in enumerate(items, start=1):
        qty = float(item.get(qty_key, 0))
        unit_price = float(item.get("unit_price", 0.0))
        line_amount = float(item.get(amount_key, qty * unit_price))

        normalized = dict(item)
        normalized["line_no"] = i
        normalized["qty"] = int(qty)
        normalized["unit_price"] = unit_price
        normalized["line_amount"] = line_amount

        payload_items.append(normalized)
        subtotal += line_amount

    return payload_items, subtotal


def render_line_items_editor(
    state_key,
    prefix,
    title,
    qty_label="Qty",
    qty_min=1,
):
    st.subheader(title)
    init_line_items(
        state_key,
        {
            "line_no": 1,
            "description": "",
            "qty": 1,
            "unit_price": 0.0,
            "line_amount": 0.0,
        },
    )

    subtotal = 0.0

    for i, item in enumerate(st.session_state[state_key]):
        st.markdown(f"### Item {i + 1}")
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            item["description"] = st.text_input(
                "Description",
                value=item.get("description", ""),
                key=f"{prefix}_description_{i}",
            )

        with col2:
            item["qty"] = st.number_input(
                qty_label,
                min_value=qty_min,
                value=int(item.get("qty", 1)),
                key=f"{prefix}_qty_{i}",
            )

        with col3:
            item["unit_price"] = st.number_input(
                "Unit Price",
                min_value=0.0,
                value=float(item.get("unit_price", 0.0)),
                key=f"{prefix}_unit_price_{i}",
            )

        item["line_no"] = i + 1
        item["line_amount"] = float(item["qty"]) * float(item["unit_price"])

        with col4:
            st.number_input(
                "Line Amount",
                value=float(item["line_amount"]),
                disabled=True,
                key=f"{prefix}_line_amount_{i}",
            )

        subtotal += float(item["line_amount"])
        st.divider()

    col_add, col_remove = st.columns(2)

    with col_add:
        if st.button(f"Add {title[:-1]} Item", key=f"{prefix}_add_item"):
            st.session_state[state_key].append(
                {
                    "line_no": len(st.session_state[state_key]) + 1,
                    "description": "",
                    "qty": 1,
                    "unit_price": 0.0,
                    "line_amount": 0.0,
                }
            )
            st.rerun()

    with col_remove:
        if st.button(f"Remove Last {title[:-1]} Item", key=f"{prefix}_remove_item"):
            if len(st.session_state[state_key]) > 1:
                st.session_state[state_key].pop()
                st.rerun()

    return subtotal, st.session_state[state_key]


# -----------------------------------
# PAGE CONFIG
# -----------------------------------

st.set_page_config(page_title="AP Automation Platform", layout="wide")

st.title("AP Automation & Data Ingestion Platform")

st.markdown(
    """
    Demo platform for:
    - Structured SAP/Kefron ingestion
    - OCR invoice extraction
    - AI-powered invoice understanding
    - Centralized AP repository
    """
)

st.sidebar.header("Platform Controls")
selected_module = st.sidebar.radio(
    "Choose Module",
    [
        "Dashboard",
        "Structured Data Intake (API)",
        "Invoice Processing (PDF/Image)",
        "Manual Data Entry (API)",
        "Admin Data Manager",
    ],
)

# ===================================
# DASHBOARD
# ===================================

if selected_module == "Dashboard":
    st.header("Platform Overview")

    col1, col2, col3 = st.columns(3)

    with col1:
        try:
            st.metric("Invoices Processed", get_table_count("invoice_master"))
        except Exception:
            st.metric("Invoices Processed", 0)

    with col2:
        try:
            st.metric("PO Records", get_table_count("sap_po_master"))
        except Exception:
            st.metric("PO Records", 0)

    with col3:
        try:
            st.metric("GRN Records", get_table_count("sap_grn_master"))
        except Exception:
            st.metric("GRN Records", 0)

    st.info("System ready for processing.")

    st.subheader("Recent Invoices")
    try:
        invoice_df=load_table_data("invoice_master",limit=10)
        invoice_df.index=(invoice_df.index+1)
        invoice_df.index.name="R.no"
        st.dataframe(invoice_df, use_container_width=True)
    except Exception as e:
        st.error(f"Invoice table error: {e}")

    st.subheader("Recent Purchase Orders")
    try:
        po_df=load_table_data("sap_po_master", limit=10)
        po_df.index=(po_df.index+1)
        po_df.index.name="R.no"
        st.dataframe(po_df, use_container_width=True)
    except Exception as e:
        st.error(f"PO table error: {e}")

    st.subheader("Recent GRNs")
    try:
        grn_df=load_table_data("sap_grn_master", limit=10)
        grn_df.index=(grn_df.index+1)
        grn_df.index.name="R.no"
        st.dataframe(grn_df, use_container_width=True)
    except Exception as e:
        st.error(f"GRN table error: {e}")


# ===================================
# STRUCTURED INGESTION
# ===================================

elif selected_module == "Structured Data Intake (API)":
    st.header("Structured Data Intake")
    st.write(
        """
        Sync structured records from:
        - SAP Purchase Orders
        - SAP GRNs
        - Kefron Invoice APIs
        """
    )

    if st.button("Start Structured Sync"):
        with st.spinner("Running structured ingestion..."):
            try:
                result = sync_structured_sources()
                st.write(result)

                status = result.get("status", "failed")
                if status == "success":
                    st.success("Structured ingestion completed.")
                    details = result.get("details", {})

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Invoices Synced", details.get("invoice_count", 0))
                    with col2:
                        st.metric("PO Records Synced", details.get("po_count", 0))
                    with col3:
                        st.metric("GRN Records Synced", details.get("grn_count", 0))

                    st.info(f"Total Sync Time: {result.get('total_time_sec', 'N/A')} sec")
                else:
                    st.error(
                        f"Sync Failed: {result.get('error', result.get('message', 'Unknown error'))}"
                    )
            except Exception as e:
                st.exception(e)


# ===================================
# INVOICE PROCESSING
# ===================================

elif selected_module == "Invoice Processing (PDF/Image)":
    st.header("Invoice OCR & AI Extraction")

    uploaded_file = st.file_uploader("Upload Invoice", type=["pdf", "png", "jpg", "jpeg"])

    if uploaded_file:
        st.success(f"Uploaded: {uploaded_file.name}")

        if st.button("Process Invoice"):
            with st.spinner("Running OCR + AI extraction..."):
                try:
                    saved_file_path = save_uploaded_file(uploaded_file)
                    result = process_invoice_pipeline(saved_file_path)
                    st.write(result)

                    status = result.get("status", "failed")
                    if status == "success":
                        st.success("Invoice processed successfully.")

                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("OCR Time", f"{result.get('ocr_time_sec', 'N/A')} sec")
                        with col2:
                            st.metric("Groq Time", f"{result.get('groq_time_sec', 'N/A')} sec")
                        with col3:
                            st.metric("Total Time", f"{result.get('total_time_sec', 'N/A')} sec")

                        st.subheader("Extracted Invoice Data")
                        st.json(result.get("parsed_json", {}))
                    else:
                        st.error(
                            f"Processing Failed: {result.get('error', result.get('message', 'Unknown error'))}"
                        )
                except Exception as e:
                    st.exception(e)


# ===================================
# MANUAL DATA ENTRY
# ===================================

elif selected_module == "Manual Data Entry (API)":
    st.header("Mock Source Manager")
    st.write(
        """
        Create mock SAP/Kefron records
        directly from UI.
        """
    )

    tab1, tab2, tab3 = st.tabs(["Create Invoice", "Create PO", "Create GRN"])

    # ===================================
    # CREATE INVOICE
    # ===================================
    with tab1:
        try:
            st.subheader("Create Mock Invoice")

            col1, col2 = st.columns(2)
            with col1:
                invoice_number = st.text_input("Invoice Number", key="invoice_number_input")
                po_number = st.text_input("Related PO Number", key="invoice_po_number_input")
                vendor_name = st.text_input("Vendor Name", key="invoice_vendor_name_input")
            with col2:
                invoice_date = st.date_input("Invoice Date", key="invoice_date_input")
                currency = st.selectbox("Currency", options=["INR", "USD", "EUR", "GBP"], index=0, key="invoice_currency_input")
                payment_status = st.selectbox(
                    "Payment Status",
                    options=["Pending", "Paid", "Rejected", "Overdue"],
                    key="invoice_payment_status_input",
                )

            invoice_subtotal, invoice_line_items = render_line_items_editor(
                state_key="invoice_line_items",
                prefix="invoice",
                title="Invoice Line Items",
                qty_label="Quantity",
                qty_min=1,
            )

            st.subheader("Invoice Totals")
            vat_percent = st.number_input(
                "VAT %",
                min_value=0.0,
                max_value=100.0,
                value=18.0,
                key="invoice_vat_percent_input",
            )
            tax_amount = invoice_subtotal * (vat_percent / 100)
            document_total = invoice_subtotal + tax_amount

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Subtotal", f"{invoice_subtotal:.2f} {currency}")
            with col2:
                st.metric("Tax Amount", f"{tax_amount:.2f} {currency}")
            with col3:
                st.metric("Total", f"{document_total:.2f} {currency}")

            if st.button("Create Invoice", key="create_invoice_button"):
                try:
                    payload = {
                        "document_type": "invoice",
                        "invoice_number": invoice_number,
                        "po_number": po_number,
                        "vendor_name": vendor_name,
                        "invoice_date": str(invoice_date),
                        "currency": currency,
                        "document_subtotal": invoice_subtotal,
                        "tax_amount": tax_amount,
                        "vat_percent": vat_percent,
                        "document_total": document_total,
                        "amount": document_total,
                        "payment_status": payment_status,
                        "line_items": invoice_line_items,
                        "last_modified": datetime.now().isoformat(),
                    }

                    response = requests.post(
                        f"{API_BASE_URL}/kefron/invoices",
                        json=payload,
                        headers={"Authorization": "Bearer mock_kefron_token"},
                        timeout=60,
                    )

                    if response.status_code == 200:
                        st.success("Invoice created successfully.")
                        st.json(response.json())
                    else:
                        st.error(f"Error creating invoice: {response.text}")
                except Exception as e:
                    st.exception(e)
        except Exception as invoice_exception:
            st.error(f"Error in Invoice creation form: {invoice_exception}")

    # ===================================
    # CREATE PO
    # ===================================
    with tab2:
        try:
            st.subheader("Create Mock Purchase Order")

            col1, col2 = st.columns(2)
            with col1:
                po_number = st.text_input("PO Number", key="po_number_input")
                po_vendor_name = st.text_input("Vendor Name", key="po_vendor_name_input")
                po_date = st.date_input("PO Date", key="po_date_input")
            with col2:
                po_currency = st.selectbox("Currency", options=["INR", "USD", "EUR", "GBP"], index=0, key="po_currency_input")
                vat_percent = st.number_input("VAT %", min_value=0.0, max_value=100.0, value=18.0, key="po_vat_percent_input")
                po_status = st.selectbox("PO Status", options=["Open", "Closed", "Cancelled", "Partially Received"], key="po_status_input")

            po_subtotal, po_line_items = render_line_items_editor(
                state_key="po_line_items",
                prefix="po",
                title="PO Line Items",
                qty_label="Quantity",
                qty_min=1,
            )

            st.subheader("PO Totals")
            tax_amount = po_subtotal * (vat_percent / 100)
            po_document_total = po_subtotal + tax_amount

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Subtotal", f"{po_subtotal:.2f} {po_currency}")
            with col2:
                st.metric("Tax Amount", f"{tax_amount:.2f} {po_currency}")
            with col3:
                st.metric("Total", f"{po_document_total:.2f} {po_currency}")

            if st.button("Create Purchase Order", key="create_po_button"):
                try:
                    payload = {
                        "document_type": "po",
                        "po_number": po_number,
                        "vendor_name": po_vendor_name,
                        "po_date": str(po_date),
                        "currency": po_currency,
                        "document_subtotal": po_subtotal,
                        "tax_amount": tax_amount,
                        "vat_percent": vat_percent,
                        "document_total": po_document_total,
                        "amount": po_document_total,
                        "po_status": po_status,
                        "line_items": po_line_items,
                        "last_modified": datetime.now().isoformat(),
                    }

                    response = requests.post(
                        f"{API_BASE_URL}/sap/po",
                        json=payload,
                        auth=("sap_user", "sap_pass"),
                        timeout=60,
                    )

                    if response.status_code == 200:
                        st.success("Purchase Order created successfully.")
                        st.json(response.json())
                    else:
                        st.error(f"Error creating PO: {response.text}")
                except Exception as e:
                    st.exception(e)
        except Exception as po_exception:
            st.error(f"Error in PO creation form: {po_exception}")

    # ===================================
    # CREATE GRN
    # ===================================
    with tab3:
        try:
            st.subheader("Create Mock GRN")

            col1, col2 = st.columns(2)
            with col1:
                gr_number = st.text_input("GRN Number", key="grn_number_input")
                po_number = st.text_input("Related PO Number", key="grn_po_number_input")
                vendor_name = st.text_input("Vendor Name", key="grn_vendor_name_input")
            with col2:
                gr_date = st.date_input("GRN Date", key="grn_date_input")
                currency = st.selectbox("Currency", options=["INR", "USD", "EUR", "GBP"], index=0, key="grn_currency_input")
                gr_status = st.selectbox(
                    "GRN Status",
                    options=["Received", "Partially Received", "Pending"],
                    key="grn_status_input",
                )

            grn_subtotal, grn_line_items = render_line_items_editor(
                state_key="grn_line_items",
                prefix="grn",
                title="GRN Line Items",
                qty_label="Received Quantity",
                qty_min=0,
            )

            st.subheader("GRN Total")
            grn_document_total = grn_subtotal

            col1, col2 = st.columns(2)
            with col1:
                st.metric("Document Subtotal", f"{grn_subtotal:.2f} {currency}")
            with col2:
                st.metric("Total Amount", f"{grn_document_total:.2f} {currency}")

            if st.button("Create GRN", key="create_grn_button"):
                try:
                    payload = {
                        "document_type": "grn",
                        "gr_number": gr_number,
                        "po_number": po_number,
                        "vendor_name": vendor_name,
                        "gr_date": str(gr_date),
                        "currency": currency,
                        "document_subtotal": grn_subtotal,
                        "document_total": grn_document_total,
                        "amount": grn_document_total,
                        "gr_status": gr_status,
                        "line_items": grn_line_items,
                        "last_modified": datetime.now().isoformat(),
                    }

                    response = requests.post(
                        f"{API_BASE_URL}/sap/grn",
                        json=payload,
                        auth=("sap_user", "sap_pass"),
                        timeout=60,
                    )

                    if response.status_code == 200:
                        st.success("GRN created successfully.")
                        st.json(response.json())
                    else:
                        st.error(f"Error creating GRN: {response.text}")
                except Exception as e:
                    st.exception(e)
        except Exception as grn_exception:
            st.error(f"Error in GRN creation form: {grn_exception}")

# -----------------------------------
# ADMIN DATA MANAGER
# -----------------------------------

elif selected_module == "Admin Data Manager":

    st.header(
        "Admin Data Manager"
    )

    st.warning(
        "Danger Zone - Database Operations"
    )
    st.divider()

confirm_reset = st.checkbox(
    "I understand this will clear demo state",
    key="confirm_reset_demo"
)

if confirm_reset and st.button(
    "Reset Demo Environment",
    key="reset_demo_btn"
):

    try:

        result = reset_demo_environment()

        if result.get("status") == "success":

            st.success(
                "Demo environment reset."
            )

            st.rerun()

        else:

            st.error(
                result.get(
                    "error",
                    "Reset failed"
                )
            )

    except Exception as e:

        st.exception(e)
    # ===================================
    # DELETE SINGLE ROWS
    # ===================================

    st.subheader(
        "Delete Single Records"
    )

    delete_invoice_no = st.text_input(
        "Invoice Number",
        key="delete_invoice"
    )

    if st.button(
        "Delete Invoice",
        key="delete_invoice_btn"
    ):

        delete_invoice(
            delete_invoice_no
        )

        st.success(
            "Invoice deleted."
        )

    delete_po_no = st.text_input(
        "PO Number",
        key="delete_po"
    )

    if st.button(
        "Delete PO",
        key="delete_po_btn"
    ):

        delete_po(
            delete_po_no
        )

        st.success(
            "PO deleted."
        )

    delete_grn_no = st.text_input(
        "GRN Number",
        key="delete_grn"
    )

    if st.button(
        "Delete GRN",
        key="delete_grn_btn"
    ):

        delete_grn(
            delete_grn_no
        )

        st.success(
            "GRN deleted."
        )

    # ===================================
    # CLEAR TABLES
    # ===================================

    st.subheader(
        "Clear Tables"
    )

    col1, col2, col3 = st.columns(3)

    with col1:

        if st.button(
            "Clear Invoice Table"
        ):

            clear_invoice_table()

            st.success(
                "Invoice table cleared."
            )

    with col2:

        if st.button(
            "Clear PO Table"
        ):

            clear_po_table()

            st.success(
                "PO table cleared."
            )

    with col3:

        if st.button(
            "Clear GRN Table"
        ):

            clear_grn_table()

            st.success(
                "GRN table cleared."
            )

    # ===================================
    # KEEP ONLY LATEST ROWS
    # ===================================

    st.subheader(
        "Keep Latest Rows"
    )

    keep_count = st.number_input(

        "Rows to Keep",

        min_value=1,

        value=10
    )

    selected_table = st.selectbox(

        "Select Table",

        [
    "invoice_master",
    "sap_po_master",
    "sap_grn_master"
]
    )

    if st.button(
        "Apply Cleanup"
    ):

        keep_latest_rows(

            selected_table,

            keep_count
        )

        st.success(
            "Cleanup completed."
        )