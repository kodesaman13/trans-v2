#!/usr/bin/env python3
"""
OCR Backend — Full Test Suite (Python)
======================================
Replaces the bash test_all_files.sh.

WHY PYTHON:
  The old bash script encoded the file as a shell variable ($B64) then passed
  it to python3 -c "..." on the command line.  For large files (invoice_amazon3
  at 1.6 MB → ~2.2 MB base64) this blows past the OS ARG_MAX limit
  (macOS: ~256 KB, Linux: ~2 MB) → "Argument list too long".

  Python reads the file from disk, never touching the shell argument list.

USAGE:
    python3 test_all_files.py [--dir /path/to/pdfs] [--url http://localhost:8000]

    All PDFs, JPGs, and XLSX files must be in the same directory as this script
    (or the directory passed with --dir).
"""

import sys
import os
import json
import base64
import argparse
import urllib.request
import urllib.error

# ── ANSI colours ────────────────────────────────────────────────────────────
GREEN  = "\033[0;32m"
RED    = "\033[0;31m"
YELLOW = "\033[1;33m"
BLUE   = "\033[0;34m"
NC     = "\033[0m"

def log(msg):  print(f"{BLUE}[INFO]{NC}  {msg}")
def ok(msg):   print(f"{GREEN}[PASS]{NC}  {msg}")
def err(msg):  print(f"{RED}[FAIL]{NC}  {msg}")
def warn(msg): print(f"{YELLOW}[SKIP]{NC}  {msg}")

# ── HTTP helpers ─────────────────────────────────────────────────────────────
def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

# ── Core helpers ─────────────────────────────────────────────────────────────
def encode_file(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def create_doc(base_url, name, category, fields):
    payload = {
        "name": name,
        "category": category,
        "pages": [{"page": 1, "fields": fields}]
    }
    resp = post_json(f"{base_url}/ocr/documents/from-form", payload)
    return resp.get("document_id")

def upload_and_test(base_url, label, doc_id, file_path, counters):
    if not os.path.isfile(file_path):
        warn(f"{label} — file not found: {file_path}")
        counters["skip"] += 1
        return

    log(f"Testing: {label}  (doc_id={doc_id})")
    fname = os.path.basename(file_path)

    try:
        b64 = encode_file(file_path)
    except Exception as e:
        err(f"{label} — could not read file: {e}")
        counters["fail"] += 1
        return

    try:
        resp = post_json(f"{base_url}/ocr/upload", {
            "document_id": int(doc_id),
            "filename":    fname,
            "file":        b64
        })
    except Exception as e:
        err(f"{label} — HTTP error: {e}")
        counters["fail"] += 1
        return

    status = resp.get("status", "error")
    if status == "success":
        vd      = resp.get("validation_data", [])
        pass_ct = sum(1 for r in vd if r.get("Status") == "Pass")
        fail_ct = sum(1 for r in vd if r.get("Status") == "Fail")
        ok(f"{label} — Pass:{pass_ct}  Fail:{fail_ct}")
        for r in vd:
            icon = "  ✓" if r["Status"] == "Pass" else ("  ⚠" if r["Status"] == "Warning" else "  ✗")
            val  = str(r.get("Extracted Value", ""))[:55]
            print(f"{icon}  {r['Field']:<32} → {val}")
        counters["pass"] += 1
    else:
        msg = resp.get("message", "unknown error")
        err(f"{label} — {msg}")
        counters["fail"] += 1

    print()

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="OCR Backend Full Test Suite")
    parser.add_argument("--dir", default=".", help="Directory containing test files")
    parser.add_argument("--url", default="http://localhost:8000", help="Backend base URL")
    args = parser.parse_args()

    BASE_URL = args.url.rstrip("/")
    PDF_DIR  = args.dir

    counters = {"pass": 0, "fail": 0, "skip": 0}

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("   OCR Backend — Full Test Suite (Python)")
    print("═══════════════════════════════════════════════════════════════")
    print()

    # ── Health check ────────────────────────────────────────────────────────
    log(f"Checking server at {BASE_URL} ...")
    try:
        get_json(f"{BASE_URL}/ocr/documents")
        ok("Server is up")
    except Exception as e:
        err(f"Server not responding at {BASE_URL} — {e}")
        err("Start it with:  uvicorn api_json:app --host 0.0.0.0 --port 8000 --reload")
        sys.exit(1)
    print()

    # ── Create document rules ────────────────────────────────────────────────
    print("── Creating document rules ─────────────────────────────────────")

    ID_01 = create_doc(BASE_URL, "Engineering Contractor Invoice", "Invoice",
        ["invoice_id","invoice_number","agreement_number","contractor_name",
         "invoice_date","total_amount","currency","vat_applied"])
    log(f"01 - Engineering Contractor Invoice → doc_id={ID_01}")

    ID_02 = create_doc(BASE_URL, "Approved Time Sheets", "Invoice",
        ["invoice_id","agreement_number","billing_period","contractor_name",
         "timesheet_batch_id","approval_status"])
    log(f"02 - Approved Time Sheets → doc_id={ID_02}")

    ID_03 = create_doc(BASE_URL, "Variation Order", "Invoice",
        ["vo_number","agreement_number","contractor_name","approved_by",
         "approved_date","value","status"])
    log(f"03 - Variation Order → doc_id={ID_03}")

    ID_04 = create_doc(BASE_URL, "Insurance Certificate", "Invoice",
        ["certificate_number","contractor_name","policy_type","insurance_provider",
         "policy_number","insurance_valid_till","status"])
    log(f"04 - Insurance Certificate → doc_id={ID_04}")

    ID_05 = create_doc(BASE_URL, "Deliverables Register", "Invoice",
        ["invoice_id","agreement_number","contractor_name","billing_period",
         "sub_contract_invoice","deliverables_present"])
    log(f"05 - Deliverables Register → doc_id={ID_05}")

    ID_06 = create_doc(BASE_URL, "Proof of Payment", "Invoice",
        ["invoice_id","agreement_number","payment_reference","subcontractor_name",
         "payment_date","payment_amount","payment_status"])
    log(f"06 - Proof of Payment → doc_id={ID_06}")

    ID_07 = create_doc(BASE_URL, "Invoice Summary Sheet", "Invoice",
        ["summary_sheet_id","invoice_id","agreement_number","contractor_name",
         "invoice_period","invoice_status","prepared_by"])
    log(f"07 - Invoice Summary Sheet → doc_id={ID_07}")

    ID_08 = create_doc(BASE_URL, "Company Instruction", "Invoice",
        ["company_instruction_id","invoice_id","agreement_number",
         "instruction_date","confirmed_by","pmt_signoff_status"])
    log(f"08 - Company Instruction → doc_id={ID_08}")

    ID_09 = create_doc(BASE_URL, "Market Financial Invoice", "Invoice",
        ["invoice_number","date","total","subtotal","customer_name","description"])
    log(f"09 - Market Financial Invoice (10pg) → doc_id={ID_09}")

    ID_10 = create_doc(BASE_URL, "AWS Bill", "Invoice",
        ["billing_period","account_id","bill_status","estimated_grand_total","due_date"])
    log(f"10 - AWS Bill → doc_id={ID_10}")

    ID_11 = create_doc(BASE_URL, "Contractor Invoice", "Invoice",
        ["invoice_number","date","grand_total","vat","tax"])
    log(f"11 - Contractor Invoice → doc_id={ID_11}")

    ID_12 = create_doc(BASE_URL, "Freelance Invoice", "Invoice",
        ["invoice_number","date","total","subtotal","discount"])
    log(f"12 - Freelance Invoice → doc_id={ID_12}")

    ID_13 = create_doc(BASE_URL, "Plumbing Invoice", "Invoice",
        ["invoice_number","date","subtotal","tax_rate","total"])
    log(f"13 - Plumbing Invoice → doc_id={ID_13}")

    ID_14 = create_doc(BASE_URL, "Proforma Invoice", "Invoice",
        ["invoice_number","date","total","subtotal","vat"])
    log(f"14 - Proforma Invoice → doc_id={ID_14}")

    ID_15 = create_doc(BASE_URL, "Rental Invoice", "Invoice",
        ["invoice_number","date","subtotal","tax","total_amount"])
    log(f"15 - Rental Invoice → doc_id={ID_15}")

    ID_16 = create_doc(BASE_URL, "Self-Employed Invoice", "Invoice",
        ["invoice_number","date","subtotal","tax","total","discount"])
    log(f"16 - Self-Employed Invoice → doc_id={ID_16}")

    ID_17 = create_doc(BASE_URL, "Microsoft Commercial Invoice", "Invoice",
        ["payment_ref","statement_date","pan_number","gstin_number",
         "order_id","total_charges","due_date"])
    log(f"17 - Microsoft Commercial Invoice → doc_id={ID_17}")

    ID_18 = create_doc(BASE_URL, "JC Pre-Invoice", "Invoice",
        ["customer_name","job_card_number","invoice_date","model_name",
         "registration_number","supplier_gstin"])
    log(f"18 - JC Pre-Invoice → doc_id={ID_18}")

    ID_19 = create_doc(BASE_URL, "Amazon Invoice", "Invoice",
        ["invoice_number","order_number","pan_number","gst_registration",
         "invoice_date","invoice_details","total_amount"])
    log(f"19 - Amazon Invoice → doc_id={ID_19}")

    ID_20 = create_doc(BASE_URL, "Amazon Invoice 18pg", "Invoice",
        ["invoice_number","order_number","pan_number","gst_registration",
         "invoice_date","total_amount"])
    log(f"20 - Amazon Invoice 18pg → doc_id={ID_20}")

    ID_21 = create_doc(BASE_URL, "ForceStore Invoice", "Invoice",
        ["invoice_number","order_number","order_date","customer_name",
         "subtotal","tax","discount","grand_total","payment_method","shipping_method"])
    log(f"21 - ForceStore Invoice (scanned) → doc_id={ID_21}")

    ID_22 = create_doc(BASE_URL, "Construction Schedule", "Invoice",
        ["project_name","project_manager","start_date","end_date","total_duration"])
    log(f"22 - Construction Schedule (scanned) → doc_id={ID_22}")

    ID_23 = create_doc(BASE_URL, "MMR Form", "Invoice",
        ["project_no","mmr_request_no","agreement_title_no","name_of_person",
         "category","job_title","nationality","base_location","rate_per_hour",
         "anticipated_mobilization_date","anticipated_demobilization_date"])
    log(f"23 - MMR Form (image) → doc_id={ID_23}")

    ID_24 = create_doc(BASE_URL, "JC Pre-Invoice Scanned", "Invoice",
        ["customer_name","job_card_number","invoice_date","model_name",
         "registration_number","supplier_gstin","model_code"])
    log(f"24 - JC Pre-Invoice Scanned → doc_id={ID_24}")

    # NEW: Restaurant menu — tests that non-invoice docs don't crash the system
    ID_25 = create_doc(BASE_URL, "Restaurant Menu", "Menu",
        ["restaurant_name","menu_category","item_name","price"])
    log(f"25 - Restaurant Menu → doc_id={ID_25}")

    # NEW: Global Invoice Template (xlsx)
    ID_26 = create_doc(BASE_URL, "Global Invoice Template XLSX", "Invoice",
        ["invoice_number","date","total","subtotal","customer_name"])
    log(f"26 - Global Invoice Template (xlsx) → doc_id={ID_26}")

    # NEW: Filled Invoice From Expense (xlsx)
    ID_27 = create_doc(BASE_URL, "Filled Invoice From Expense XLSX", "Invoice",
        ["invoice_number","date","total","subtotal","customer_name"])
    log(f"27 - Filled Invoice From Expense (xlsx) → doc_id={ID_27}")

    print()

    # ── Upload & test all files ──────────────────────────────────────────────
    print("── Uploading and testing files ─────────────────────────────────")

    upload_and_test(BASE_URL, "01 - Engineering Contractor Invoice", ID_01,
        os.path.join(PDF_DIR, "01_invoice_document_INV-100001.pdf"), counters)

    upload_and_test(BASE_URL, "02 - Approved Time Sheets", ID_02,
        os.path.join(PDF_DIR, "02_time_sheets_INV-100001.pdf"), counters)

    upload_and_test(BASE_URL, "03 - Variation Order", ID_03,
        os.path.join(PDF_DIR, "03_variation_order_VO-2026-014.pdf"), counters)

    upload_and_test(BASE_URL, "04 - Insurance Certificate", ID_04,
        os.path.join(PDF_DIR, "04_insurance_certificate_ABC.pdf"), counters)

    upload_and_test(BASE_URL, "05 - Deliverables Register", ID_05,
        os.path.join(PDF_DIR, "05_deliverables_register_INV-100001.pdf"), counters)

    upload_and_test(BASE_URL, "06 - Proof of Payment", ID_06,
        os.path.join(PDF_DIR, "06_proof_of_payment_INV-100001.pdf"), counters)

    upload_and_test(BASE_URL, "07 - Invoice Summary Sheet", ID_07,
        os.path.join(PDF_DIR, "07_invoice_summary_sheet_INV-100001.pdf"), counters)

    upload_and_test(BASE_URL, "08 - Company Instruction", ID_08,
        os.path.join(PDF_DIR, "08_company_instruction_INV-100001.pdf"), counters)

    upload_and_test(BASE_URL, "09 - Market Financial Invoice 10pg", ID_09,
        os.path.join(PDF_DIR, "10 pages pdf-invoices1.pdf"), counters)

    upload_and_test(BASE_URL, "10 - AWS Bill", ID_10,
        os.path.join(PDF_DIR, "AWS Weekly Report-07-09-23.pdf"), counters)

    upload_and_test(BASE_URL, "11 - Contractor Invoice", ID_11,
        os.path.join(PDF_DIR, "Invoice-Contractor.pdf"), counters)

    upload_and_test(BASE_URL, "12 - Freelance Invoice", ID_12,
        os.path.join(PDF_DIR, "Invoice-Freelance.pdf"), counters)

    upload_and_test(BASE_URL, "13 - Plumbing Invoice", ID_13,
        os.path.join(PDF_DIR, "Invoice-Plumbing.pdf"), counters)

    upload_and_test(BASE_URL, "14 - Proforma Invoice", ID_14,
        os.path.join(PDF_DIR, "Invoice-Proforma.pdf"), counters)

    upload_and_test(BASE_URL, "15 - Rental Invoice", ID_15,
        os.path.join(PDF_DIR, "Invoice-Rental.pdf"), counters)

    upload_and_test(BASE_URL, "16 - Self-Employed Invoice", ID_16,
        os.path.join(PDF_DIR, "Invoice-SelfEmployed (1).pdf"), counters)

    upload_and_test(BASE_URL, "17 - Microsoft Commercial Invoice", ID_17,
        os.path.join(PDF_DIR, "Invoice_202309 (5).pdf"), counters)

    upload_and_test(BASE_URL, "18 - JC Pre-Invoice (text)", ID_18,
        os.path.join(PDF_DIR, "JcPreInvoice-1.pdf"), counters)

    upload_and_test(BASE_URL, "19 - Amazon Invoice", ID_19,
        os.path.join(PDF_DIR, "invoice_amazon.pdf"), counters)

    # FIX: Doc 20 — was failing with "Argument list too long" in bash because the
    # 18-page file (~1.6MB → ~2.2MB base64) exceeded the OS shell ARG_MAX limit.
    # Python reads from disk directly — no shell argument involved — so no limit.
    upload_and_test(BASE_URL, "20 - Amazon Invoice 18pg", ID_20,
        os.path.join(PDF_DIR, "invoice_amazon3.pdf"), counters)

    print("── Scanned/Image files (Azure OCR extracts text) ───────────────")

    upload_and_test(BASE_URL, "21 - ForceStore Invoice (scanned)", ID_21,
        os.path.join(PDF_DIR, "invoice_forcestore.pdf"), counters)

    upload_and_test(BASE_URL, "22 - Construction Schedule (scanned)", ID_22,
        os.path.join(PDF_DIR, "invoice_Construction.pdf"), counters)

    upload_and_test(BASE_URL, "23 - MMR Form (image jpg)", ID_23,
        os.path.join(PDF_DIR, "mmr_image.jpg"), counters)

    # NOTE: JcPreInvoice-2 and JcPreInvoice_full have corrupted zlib streams
    # on pages 2-4. Azure may partially OCR them but results will be unreliable.
    # These files need to be re-exported from the source system for full results.
    upload_and_test(BASE_URL, "24 - JC Pre-Invoice v2 (scanned)", ID_24,
        os.path.join(PDF_DIR, "JcPreInvoice-2.pdf"), counters)

    upload_and_test(BASE_URL, "24b - JC Pre-Invoice full (scanned)", ID_24,
        os.path.join(PDF_DIR, "JcPreInvoice_full.pdf"), counters)

    # NOTE: 10 pages pdf-invoices2 also has corrupted zlib streams — needs re-export
    upload_and_test(BASE_URL, "09b - Market Invoice 10pg (scanned)", ID_09,
        os.path.join(PDF_DIR, "10 pages pdf-invoices2.pdf"), counters)

    print("── Previously untested / new file types ────────────────────────")

    upload_and_test(BASE_URL, "25 - Restaurant Menu (PDF)", ID_25,
        os.path.join(PDF_DIR, "invoice_restaurant.pdf"), counters)

    upload_and_test(BASE_URL, "26 - Global Invoice Template (xlsx)", ID_26,
        os.path.join(PDF_DIR, "Global_Invoice_Template.xlsx"), counters)

    upload_and_test(BASE_URL, "27 - Filled Invoice From Expense (xlsx)", ID_27,
        os.path.join(PDF_DIR, "Filled_Invoice_From_Expense.xlsx"), counters)

    upload_and_test(BASE_URL, "27b - Filled Invoice From Expense v2 (xlsx)", ID_27,
        os.path.join(PDF_DIR, "Filled_Invoice_From_Expense (1).xlsx"), counters)

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    print("═══════════════════════════════════════════════════════════════")
    print(
        f"  Results: {GREEN}{counters['pass']} uploads succeeded{NC}"
        f"  |  {RED}{counters['fail']} failed{NC}"
        f"  |  {YELLOW}{counters['skip']} skipped{NC}"
    )
    print("═══════════════════════════════════════════════════════════════")
    print()
    print("  To check output table for any doc, run:")
    print("    curl http://localhost:8000/ocr/document-scores/<doc_id>")
    print()
    print("  To list all master table docs:")
    print("    curl http://localhost:8000/ocr/documents")
    print()
    print("  NOTE: JcPreInvoice-2, JcPreInvoice_full, and 10 pages pdf-invoices2")
    print("  have corrupted internal streams — re-export these PDFs for full results.")
    print()


if __name__ == "__main__":
    main()
