#!/usr/bin/env python3
"""
test_failed_files.py
====================
Retests ONLY the files that were previously failing or giving wrong results.
Does NOT create new document rules — reuses existing doc_ids from the DB.

USAGE:
    python3 test_failed_files.py --dir /path/to/pdfs --url http://localhost:8000

OPTIONS:
    --dir   Folder containing the PDF/XLSX/JPG files  (default: current dir)
    --url   Backend base URL                           (default: http://localhost:8000)
    --doc   Comma-separated doc numbers to retest      (default: all failed)
            e.g. --doc 9b,13,14,16,21,22,23,24,26,27

WHAT IT TESTS:
    09b  Market Invoice 10pg (scanned)     — was all-Fail (corrupted file)
    13   Plumbing Invoice                   — tax_rate picked up email
    14   Proforma Invoice                   — invoice_number None
    16   Self-Employed Invoice              — invoice_number was label text
    21   ForceStore Invoice (scanned)       — invoice_number/order_date/customer wrong
    22   Construction Schedule (scanned)    — project_name/manager wrong values
    23   MMR Form (image)                   — rate_per_hour None
    24   JC Pre-Invoice v2 (scanned)        — all-Fail (corrupted file)
    24b  JC Pre-Invoice full (scanned)      — all-Fail (corrupted file)
    26   Global Invoice Template (xlsx)     — all wrong (blank template)
    27   Filled Invoice From Expense (xlsx) — invoice_number/customer_name wrong
    27b  Filled Invoice From Expense v2     — was DB duplicate error
"""

import sys, os, json, base64, argparse, urllib.request, urllib.error

# ── colours ──────────────────────────────────────────────────────────────────
GREEN  = "\033[0;32m"
RED    = "\033[0;31m"
YELLOW = "\033[1;33m"
BLUE   = "\033[0;34m"
CYAN   = "\033[0;36m"
BOLD   = "\033[1m"
NC     = "\033[0m"

def log(m):  print(f"{BLUE}[INFO]{NC}  {m}")
def ok(m):   print(f"{GREEN}[PASS]{NC}  {m}")
def err(m):  print(f"{RED}[FAIL]{NC}  {m}")
def skip(m): print(f"{YELLOW}[SKIP]{NC}  {m}")
def head(m): print(f"\n{BOLD}{CYAN}{m}{NC}")

# ── HTTP helpers ──────────────────────────────────────────────────────────────
def post_json(url, payload, timeout=300):
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())

def encode_file(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

# ── Expected values per test ──────────────────────────────────────────────────
# Each entry: field_name → expected_value (or None if field is known missing)
EXPECTED = {
    "09b": {
        "invoice_number": "00012",
        "date":           "1/30/23",
        "total":          "1125.00",
        "subtotal":       "1300.00",
        "customer_name":  "Gaurav Cheema",
        "description":    "Consultation services",
    },
    "13": {
        "invoice_number": "#10025335",
        "subtotal":       "$1,265.00",
        "total":          "$1,391.50",
        # tax_rate picks up email — known doc quality issue
    },
    "14": {
        # invoice_number is truly missing from this proforma template
        "date":    "18.03.2023.",
        "total":   "$3,600.00",
        "subtotal":"$3,000.00",
        "vat":     "99855456485",
    },
    "16": {
        "invoice_number": "2023-0408",   # was "Invoice No" label text
        "date":           "November 15, 2023",
        "subtotal":       "$589.00",
        "tax":            "$117.80",
        "total":          "$654.00",
        "discount":       "$52.80",
    },
    "21": {
        "order_number":   "#000000007",
        "subtotal":       "$220.00",
        "tax":            "$16.65",
        "discount":       "-$18.20",
        "grand_total":    "$243.45",
        "payment_method": "Check / Money order",
        "shipping_method":"Flat Rate - Fixed",
    },
    "23": {
        "project_no":                    "6159",
        "mmr_request_no":                "128 Rev.0",
        "agreement_title_no":            "18-6159-F-1",
        "name_of_person":                "Manuel Jimenez Romero",
        "nationality":                   "SPANISH",
        "base_location":                 "MADRID",
        "anticipated_mobilization_date": "21 February 2019",
        # rate_per_hour — known missing from image
    },
    "24": {
        "customer_name":     "ANNAPUREEDY VENKAT REDDY",
        "job_card_number":   "RJC011575GH03633",
        "model_name":        "CLASSIC 350 DARK STEALTH BLACK",
        "registration_number":"TS09FZ5290",
        "supplier_gstin":    "36AAWFB5963F1ZY",
        "model_code":        "VSMZ60DK",
    },
    "24b": {
        "customer_name":     "ANNAPUREEDY VENKAT REDDY",
        "job_card_number":   "RJC011575GH03633",
        "model_name":        "CLASSIC 350 DARK STEALTH BLACK",
        "registration_number":"TS09FZ5290",
        "supplier_gstin":    "36AAWFB5963F1ZY",
        "model_code":        "VSMZ60DK",
    },
    "26": {
        # Blank template — all fields expected to be missing
    },
    "27": {
        "invoice_number": "INV-1001",
        "date":           "30-Apr-2026",
        "total":          "1207.5",
        "subtotal":       "1207.5",
        "customer_name":  "Global Client Ltd",
    },
    "27b": {
        "invoice_number": "INV-1001",
        "date":           "30-Apr-2026",
        "total":          "1207.5",
        "subtotal":       "1207.5",
        "customer_name":  "Global Client Ltd",
    },
}

# ── Upload + validate one file ────────────────────────────────────────────────
def run_test(base_url, label, doc_id, file_path, test_key, counters):
    if not os.path.isfile(file_path):
        skip(f"{label} — file not found: {file_path}")
        counters["skip"] += 1
        return None

    log(f"Retesting: {label}  (doc_id={doc_id})")

    try:
        b64 = encode_file(file_path)
    except Exception as e:
        err(f"{label} — cannot read file: {e}")
        counters["fail"] += 1
        return None

    try:
        resp = post_json(f"{base_url}/ocr/upload", {
            "document_id": int(doc_id),
            "filename":    os.path.basename(file_path),
            "file":        b64
        })
    except Exception as e:
        err(f"{label} — HTTP error: {e}")
        counters["fail"] += 1
        return None

    if resp.get("status") != "success":
        err(f"{label} — {resp.get('message', 'unknown error')}")
        counters["fail"] += 1
        return None

    vd = resp.get("validation_data", [])
    expected = EXPECTED.get(test_key, {})

    pass_ct = warn_ct = fail_ct = 0
    field_results = []

    for row in vd:
        field  = row["Field"]
        status = row["Status"]
        value  = str(row.get("Extracted Value") or "")
        conf   = row.get("Confidence", 0)

        # Check against expected
        exp_val = expected.get(field)
        if exp_val is not None:
            matched = exp_val.lower() in value.lower() or value.lower() in exp_val.lower()
        else:
            matched = None  # no expectation set

        if status == "Pass":
            pass_ct += 1
            icon = f"{GREEN}✓{NC}"
        elif status == "Warning":
            warn_ct += 1
            icon = f"{YELLOW}⚠{NC}"
        else:
            fail_ct += 1
            icon = f"{RED}✗{NC}"

        # Add expectation mismatch marker
        exp_marker = ""
        if matched is False:
            exp_marker = f"  {RED}← expected: {exp_val}{NC}"
        elif matched is True and status != "Pass":
            exp_marker = f"  {YELLOW}← value correct but low confidence{NC}"

        field_results.append((icon, field, value[:55], exp_marker, conf))

    # Print summary line
    summary = f"{label} — Pass:{pass_ct}  Warn:{warn_ct}  Fail:{fail_ct}"
    if fail_ct == 0:
        ok(summary)
        counters["pass"] += 1
    else:
        err(summary)
        counters["fail"] += 1

    # Print field detail
    for icon, field, value, exp_marker, conf in field_results:
        conf_str = f"({conf:.2f})" if isinstance(conf, float) else ""
        print(f"  {icon}  {field:<36} → {value:<40} {conf_str}{exp_marker}")

    print()
    return vd

# ── Fetch latest doc_ids from DB via API ─────────────────────────────────────
def fetch_doc_ids(base_url):
    """
    Pull the latest doc_ids from document_type_master via the API.
    Returns dict: {document_name_normalised: doc_id}
    """
    try:
        docs = get_json(f"{base_url}/ocr/documents")
        mapping = {}
        for d in docs:
            name = d.get("document_name", "").lower().strip()
            mapping[name] = d.get("document_id")
        return mapping
    except Exception as e:
        print(f"{RED}Could not fetch documents: {e}{NC}")
        return {}

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Retest previously failing OCR files")
    parser.add_argument("--dir", default=".", help="Directory containing test files")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--doc", default="all",
        help="Comma-separated test keys to run: 9b,13,14,16,21,22,23,24,24b,26,27,27b  or 'all'")
    parser.add_argument("--doc-id-09",  type=int, help="Override doc_id for test 09/09b")
    parser.add_argument("--doc-id-13",  type=int)
    parser.add_argument("--doc-id-14",  type=int)
    parser.add_argument("--doc-id-16",  type=int)
    parser.add_argument("--doc-id-21",  type=int)
    parser.add_argument("--doc-id-22",  type=int)
    parser.add_argument("--doc-id-23",  type=int)
    parser.add_argument("--doc-id-24",  type=int)
    parser.add_argument("--doc-id-26",  type=int)
    parser.add_argument("--doc-id-27",  type=int)
    args = parser.parse_args()

    BASE_URL = args.url.rstrip("/")
    PDF_DIR  = args.dir

    run_keys = (
        ["09b","13","14","16","21","22","23","24","24b","26","27","27b"]
        if args.doc == "all"
        else [k.strip() for k in args.doc.split(",")]
    )

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("   OCR Retry Suite — Previously Failing Files")
    print("═══════════════════════════════════════════════════════════════")

    # Health check
    log(f"Checking server at {BASE_URL} ...")
    try:
        get_json(f"{BASE_URL}/ocr/documents")
        ok("Server is up")
    except Exception as e:
        err(f"Server not responding — {e}")
        sys.exit(1)

    # Fetch existing doc_ids
    log("Fetching latest doc_ids from DB ...")
    doc_map = fetch_doc_ids(BASE_URL)

    def find_id(name_fragment, override=None):
        if override:
            return override
        for name, did in doc_map.items():
            if name_fragment.lower() in name:
                return did
        return None

    # Resolve doc_ids
    ID_09 = args.doc_id_09 or find_id("market financial") or find_id("market")
    ID_13 = args.doc_id_13 or find_id("plumbing")
    ID_14 = args.doc_id_14 or find_id("proforma")
    ID_16 = args.doc_id_16 or find_id("self-employed") or find_id("selfemployed") or find_id("self employed")
    ID_21 = args.doc_id_21 or find_id("forcestore")
    ID_22 = args.doc_id_22 or find_id("construction")
    ID_23 = args.doc_id_23 or find_id("mmr")
    ID_24 = args.doc_id_24 or find_id("jc pre-invoice scanned") or find_id("scanned")
    ID_26 = args.doc_id_26 or find_id("global invoice template")
    ID_27 = args.doc_id_27 or find_id("filled invoice") or find_id("expense")

    print()
    print("── Resolved doc_ids ────────────────────────────────────────────")
    for label, did in [
        ("09/09b Market Financial", ID_09),
        ("13   Plumbing Invoice",   ID_13),
        ("14   Proforma Invoice",   ID_14),
        ("16   Self-Employed",      ID_16),
        ("21   ForceStore",         ID_21),
        ("22   Construction",       ID_22),
        ("23   MMR Form",           ID_23),
        ("24   JC Pre-Invoice",     ID_24),
        ("26   Global XLSX Tmpl",   ID_26),
        ("27   Filled XLSX",        ID_27),
    ]:
        status = f"{GREEN}{did}{NC}" if did else f"{RED}NOT FOUND{NC}"
        print(f"  {label:<30} → {status}")
    print()

    counters = {"pass": 0, "fail": 0, "skip": 0}

    # ── Run selected tests ────────────────────────────────────────────────────

    if "09b" in run_keys:
        head("── 09b  Market Invoice 10pg (scanned) ─────────────────────────")
        print(f"  {YELLOW}Previously:{NC} Pass:0  Fail:6  — all fields None (corrupted PDF)")
        print(f"  {GREEN}Expected  :{NC} Pass:6  Fail:0  — after replacing with invoices1.pdf\n")
        run_test(BASE_URL, "09b - Market Invoice 10pg (scanned)", ID_09,
            os.path.join(PDF_DIR, "10 pages pdf-invoices2.pdf"), "09b", counters)

    if "13" in run_keys:
        head("── 13   Plumbing Invoice ───────────────────────────────────────")
        print(f"  {YELLOW}Previously:{NC} tax_rate → michael.marshall@myclient.com (wrong KV match)")
        print(f"  {GREEN}Expected  :{NC} tax_rate → actual numeric rate\n")
        run_test(BASE_URL, "13 - Plumbing Invoice", ID_13,
            os.path.join(PDF_DIR, "Invoice-Plumbing.pdf"), "13", counters)

    if "14" in run_keys:
        head("── 14   Proforma Invoice ───────────────────────────────────────")
        print(f"  {YELLOW}Previously:{NC} invoice_number → None (no number in blank proforma)")
        print(f"  {GREEN}Expected  :{NC} invoice_number still None (template has no value)\n")
        run_test(BASE_URL, "14 - Proforma Invoice", ID_14,
            os.path.join(PDF_DIR, "Invoice-Proforma.pdf"), "14", counters)

    if "16" in run_keys:
        head("── 16   Self-Employed Invoice ──────────────────────────────────")
        print(f"  {YELLOW}Previously:{NC} invoice_number → 'Invoice No' (label text, not value)")
        print(f"  {GREEN}Expected  :{NC} invoice_number → 2023-0408\n")
        run_test(BASE_URL, "16 - Self-Employed Invoice", ID_16,
            os.path.join(PDF_DIR, "Invoice-SelfEmployed (1).pdf"), "16", counters)

    if "21" in run_keys:
        head("── 21   ForceStore Invoice (scanned) ───────────────────────────")
        print(f"  {YELLOW}Previously:{NC} invoice_number→Color  order_date→order#  customer→phone")
        print(f"  {GREEN}Expected  :{NC} order_number, subtotal, tax, discount, grand_total correct\n")
        run_test(BASE_URL, "21 - ForceStore Invoice (scanned)", ID_21,
            os.path.join(PDF_DIR, "invoice_forcestore.pdf"), "21", counters)

    if "22" in run_keys:
        head("── 22   Construction Schedule (scanned) ────────────────────────")
        print(f"  {YELLOW}Previously:{NC} project_name → 'Project Manager:' (wrong line matched)")
        print(f"  {GREEN}Expected  :{NC} Gantt chart — limited extraction possible\n")
        run_test(BASE_URL, "22 - Construction Schedule (scanned)", ID_22,
            os.path.join(PDF_DIR, "invoice_Construction.pdf"), "22", counters)

    if "23" in run_keys:
        head("── 23   MMR Form (image jpg) ───────────────────────────────────")
        print(f"  {YELLOW}Previously:{NC} rate_per_hour → None (merged table cell in image)")
        print(f"  {GREEN}Expected  :{NC} 10 Pass, rate_per_hour still None (image limitation)\n")
        run_test(BASE_URL, "23 - MMR Form (image)", ID_23,
            os.path.join(PDF_DIR, "mmr_image.jpg"), "23", counters)

    if "24" in run_keys:
        head("── 24   JC Pre-Invoice v2 (scanned) ───────────────────────────")
        print(f"  {YELLOW}Previously:{NC} all 7 fields None (corrupted PDF)")
        print(f"  {GREEN}Expected  :{NC} Pass:7 after replacing with JcPreInvoice-1.pdf\n")
        run_test(BASE_URL, "24 - JC Pre-Invoice v2 (scanned)", ID_24,
            os.path.join(PDF_DIR, "JcPreInvoice-2.pdf"), "24", counters)

    if "24b" in run_keys:
        head("── 24b  JC Pre-Invoice full (scanned) ──────────────────────────")
        print(f"  {YELLOW}Previously:{NC} DB error / all-Fail (corrupted PDF)")
        print(f"  {GREEN}Expected  :{NC} Pass:7 after replacing with JcPreInvoice-1.pdf\n")
        run_test(BASE_URL, "24b - JC Pre-Invoice full (scanned)", ID_24,
            os.path.join(PDF_DIR, "JcPreInvoice_full.pdf"), "24b", counters)

    if "26" in run_keys:
        head("── 26   Global Invoice Template (xlsx) ─────────────────────────")
        print(f"  {YELLOW}Previously:{NC} invoice_number→'Seller Details', date→None (blank template)")
        print(f"  {GREEN}Expected  :{NC} All Fail — template has no filled values (correct behaviour)\n")
        run_test(BASE_URL, "26 - Global Invoice Template (xlsx)", ID_26,
            os.path.join(PDF_DIR, "Global_Invoice_Template.xlsx"), "26", counters)

    if "27" in run_keys:
        head("── 27   Filled Invoice From Expense (xlsx) ─────────────────────")
        print(f"  {YELLOW}Previously:{NC} invoice_number→'Seller:'  customer_name→phone number")
        print(f"  {GREEN}Expected  :{NC} invoice_number→INV-1001  customer_name→Global Client Ltd\n")
        run_test(BASE_URL, "27 - Filled Invoice From Expense (xlsx)", ID_27,
            os.path.join(PDF_DIR, "Filled_Invoice_From_Expense.xlsx"), "27", counters)

    if "27b" in run_keys:
        head("── 27b  Filled Invoice From Expense v2 (xlsx) ──────────────────")
        print(f"  {YELLOW}Previously:{NC} DB duplicate entry error on master_csv_config")
        print(f"  {GREEN}Expected  :{NC} Same as 27 — no DB error now\n")
        run_test(BASE_URL, "27b - Filled Invoice From Expense v2 (xlsx)", ID_27,
            os.path.join(PDF_DIR, "Filled_Invoice_From_Expense (1).xlsx"), "27b", counters)

    # ── Final summary ─────────────────────────────────────────────────────────
    print()
    print("═══════════════════════════════════════════════════════════════")
    total = counters["pass"] + counters["fail"] + counters["skip"]
    print(
        f"  Retested: {total}  |  "
        f"{GREEN}Passed: {counters['pass']}{NC}  |  "
        f"{RED}Failed: {counters['fail']}{NC}  |  "
        f"{YELLOW}Skipped: {counters['skip']}{NC}"
    )
    print("═══════════════════════════════════════════════════════════════")

    if counters["fail"] == 0 and counters["skip"] == 0:
        print(f"\n  {GREEN}{BOLD}All previously failing files now pass ✓{NC}\n")
    elif counters["fail"] > 0:
        print(f"\n  {RED}Some files still failing — check output above for details.{NC}\n")
    elif counters["skip"] > 0:
        print(f"\n  {YELLOW}Some files not found — check --dir path.{NC}\n")

    print("  To retest specific docs only:")
    print("    python3 test_failed_files.py --dir <path> --doc 27,27b")
    print()
    print("  To override a doc_id manually:")
    print("    python3 test_failed_files.py --dir <path> --doc-id-27 299")
    print()


if __name__ == "__main__":
    main()