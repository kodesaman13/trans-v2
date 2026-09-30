from fastapi import FastAPI, Body, APIRouter
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import json
import base64
import binascii
import io
import re
from PIL import Image
from PyPDF2 import PdfReader, PdfWriter
from datetime import datetime

from document_service import *

# FIX: import from JsonTransformation (has all 4 strategies incl. search_page_lines)
# Previously imported from json_transform_dynamic which had NO page_lines support —
# meaning every scanned document silently skipped the OCR text fallback in production.
from ML.JsonTransformation import process
from fastocr_client import FastOCRError, FastOCRClient

app = FastAPI()

ocr_router = APIRouter(prefix="/ocr", tags=["OCR"])

origins = [
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

INPUT_DIR     = os.path.join(CURRENT_DIR, "Input")
JSON_DIR      = os.path.join(CURRENT_DIR, "Json")
OUTPUT_DIR    = os.path.join(CURRENT_DIR, "Output")
EXTRACTED_DIR = os.path.join(CURRENT_DIR, "ExtractedInput")

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".xlsx", ".xls"}


def _content_type(filename):
    return {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(os.path.splitext(filename)[1].lower(), "application/octet-stream")


def _fastocr_json(text):
    lines = [{"content": line, "page_number": 1} for line in text.splitlines() if line.strip()]
    return [{"fields": {}, "key_value_pairs": [], "page_lines": lines, "full_text": text, "pages_analyzed": [1]}]


def _fastocr_json_by_page(results):
    page_lines = []
    full_text = []
    pages = []
    for page_number, result in results:
        pages.append(page_number)
        full_text.append(result.text)
        page_lines.extend(
            {"content": line, "page_number": page_number}
            for line in result.text.splitlines()
            if line.strip()
        )
    return [{
        "fields": {},
        "key_value_pairs": [],
        "page_lines": page_lines,
        "full_text": "\n".join(full_text),
        "pages_analyzed": pages,
    }]


def _fastocr_process_upload(filename, content, content_type, pages_to_extract):
    client = FastOCRClient.from_environment()
    if not pages_to_extract or not filename.lower().endswith(".pdf"):
        return [(1, client.process_file(filename, content, content_type))]

    reader = PdfReader(io.BytesIO(content))
    results = []
    valid_pages = sorted({
        int(page) for page in pages_to_extract
        if str(page).isdigit() and 1 <= int(page) <= len(reader.pages)
    })
    pages = valid_pages or list(range(1, len(reader.pages) + 1))
    for page_number in pages:
        writer = PdfWriter()
        writer.add_page(reader.pages[page_number - 1])
        buffer = io.BytesIO()
        writer.write(buffer)
        results.append((
            page_number,
            client.process_file(filename, buffer.getvalue(), "application/pdf"),
        ))
    return results


@ocr_router.get("/documents")
def get_docs():
    return get_active_documents()


@ocr_router.post("/documents")
def create_doc(payload: dict = Body(...)):
    name     = payload.get("name")
    category = payload.get("category")
    rules    = payload.get("rules", {})
    document_id = create_document(name, category, rules)
    return {"status": "created", "document_id": document_id}


@ocr_router.put("/documents/{doc_id}")
def update_doc(doc_id: int, payload: dict = Body(...)):
    update_document(doc_id, payload)
    return {"status": "updated"}


@ocr_router.post("/documents/from-form")
def create_doc_from_form(payload: dict = Body(...)):
    name     = payload.get("name")
    category = payload.get("category")
    pages    = payload.get("pages", [])

    rules = {"pages": {}}
    for p in pages:
        page_no = str(p.get("page"))
        fields  = p.get("fields") or p.get("keys", [])
        if page_no and fields:
            rules["pages"][page_no] = fields

    document_id = create_document(name, category, rules)
    return {"status": "created", "document_id": document_id, "rules": rules}


@ocr_router.post("/upload")
def upload_file(payload: dict = Body(...)):
    try:
        os.makedirs(INPUT_DIR,     exist_ok=True)
        os.makedirs(JSON_DIR,      exist_ok=True)
        os.makedirs(OUTPUT_DIR,    exist_ok=True)
        os.makedirs(EXTRACTED_DIR, exist_ok=True)

        # Clear generated artifacts only. Keep uploaded/source samples in Input
        # so they remain available for comparison and repeat testing.
        for folder in [JSON_DIR, OUTPUT_DIR, EXTRACTED_DIR]:
            for f in os.listdir(folder):
                file_path = os.path.join(folder, f)
                if os.path.isfile(file_path):
                    os.remove(file_path)

        document_id = payload.get("document_id")
        filename    = payload.get("filename")
        file_base64 = payload.get("file")

        if not isinstance(filename, str) or not filename.strip():
            return {"status": "error", "message": "filename is required"}
        if not isinstance(file_base64, str) or not file_base64:
            return {"status": "error", "message": "file must be a base64-encoded string"}

        try:
            # Swagger and copied terminal output may wrap long Base64 values.
            # Ignore formatting whitespace, but continue rejecting invalid data.
            normalized_base64 = re.sub(r"\s+", "", file_base64)
            file_bytes = base64.b64decode(normalized_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            return {"status": "error", "message": f"Invalid base64 file: {exc}"}

        filename = os.path.basename(filename)
        input_path = os.path.join(INPUT_DIR, filename)

        with open(input_path, "wb") as buffer:
            buffer.write(file_bytes)

        ext = os.path.splitext(filename)[1].lower()

        # Validate extension
        if ext not in SUPPORTED_EXTENSIONS:
            return {
                "status":  "error",
                "message": f"Unsupported file type: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            }

        # Convert image → PDF so Azure can process it
        if ext in {".jpg", ".jpeg", ".png"}:
            image = Image.open(input_path)
            if image.mode == "RGBA":
                image = image.convert("RGB")
            pdf_path = os.path.splitext(input_path)[0] + ".pdf"
            image.save(pdf_path, "PDF", resolution=300.0)
            os.remove(input_path)
            filename = os.path.basename(pdf_path)
            input_path = pdf_path

        # ── XLSX: extract fields directly via openpyxl (no Azure needed) ───────
        if ext in {".xlsx", ".xls"}:
            try:
                import openpyxl, re as _re

                wb = openpyxl.load_workbook(input_path, data_only=True)
                ws = wb.active

                doc_rec = get_document_by_id(document_id) if document_id else None
                rules_raw = payload.get("rules") or (doc_rec.get("extraction_rules_json") if doc_rec else None)
                if isinstance(rules_raw, str):
                    try:
                        rules = json.loads(rules_raw)
                    except Exception:
                        rules = {}
                elif isinstance(rules_raw, dict):
                    rules = rules_raw
                else:
                    rules = {}

                from ML.JsonTransformation import parse_extraction_rules
                page_fields_map = parse_extraction_rules(rules)
                rule_items = []
                if page_fields_map:
                    for p_num, keys in sorted(page_fields_map.items()):
                        for k in keys:
                            rule_items.append((p_num, k))
                else:
                    rule_items = []

                def _norm(s):
                    return _re.sub(r'[\s_\-]', '', str(s)).lower()

                # Build two lookup structures from the spreadsheet:
                # 1. label_map  — {normalised_label: value}  from "Label: Value" cells
                # 2. two_col    — {normalised_col_A_label: col_D_value} for side-by-side layouts
                label_map = {}
                two_col   = {}

                all_rows = list(ws.iter_rows(values_only=True))
                for row in all_rows:
                    cells = [str(c).strip() if c is not None else "" for c in row]
                    non_empty = [c for c in cells if c]

                    # Pattern 1: "Invoice No: INV-1001"  — label+value in SAME cell
                    for cell in non_empty:
                        if ":" in cell:
                            parts = cell.split(":", 1)
                            lbl = _norm(parts[0])
                            val = parts[1].strip()
                            if lbl and val:
                                label_map[lbl] = val

                    # Pattern 2: "Invoice Number:"  in col[0],  value in col[1] or col[3]
                    if len(cells) >= 2:
                        left_lbl = _norm(cells[0].rstrip(":"))
                        left_val = cells[1].strip() if len(cells) > 1 else ""
                        if left_lbl and left_val:
                            label_map[left_lbl] = left_val

                    # Pattern 3: side-by-side pairs (col 0/1 and col 3/4)
                    if len(cells) >= 4:
                        right_lbl = _norm(cells[3].rstrip(":"))
                        right_val = cells[4].strip() if len(cells) > 4 else ""
                        if right_lbl and right_val:
                            label_map[right_lbl] = right_val

                    # Pattern 4: "Total:  " in col[4], value in col[5]
                    if len(cells) >= 6:
                        mid_lbl = _norm(cells[4].rstrip(":"))
                        mid_val = cells[5].strip()
                        if mid_lbl and mid_val:
                            label_map[mid_lbl] = mid_val

                # Also collect standalone values (company name, address rows)
                # Row where col0 has a value but no colon → potential name/address
                standalone_vals = []
                for row in all_rows:
                    cells = [str(c).strip() if c is not None else "" for c in row]
                    if cells and cells[0] and ":" not in cells[0]:
                        standalone_vals.append(cells[0])

                file_name  = os.path.splitext(filename)[0]
                validation_data = []
                invoice_data    = {}

                # If no deterministic rules provided, extract all discovered labels from spreadsheet
                if not rule_items:
                    rule_items = [(1, raw_lbl) for raw_lbl in label_map.keys()]

                for p_num, key in rule_items:
                    key_norm = _norm(key)
                    value    = None

                    # Direct lookup in label_map
                    if key_norm in label_map:
                        value = label_map[key_norm]
                    else:
                        # Alias map for common XLSX label variations
                        XLSX_ALIAS = {
                            "invoicenumber": ["invoiceno", "invoiceid", "invno", "inv"],
                            "date":          ["invoicedate", "invoiceddate", "dated"],
                            "duedate":       ["paymentdue", "dueby", "duebydate"],
                            "total":         ["totaldue", "totalamount", "grandtotal", "amountdue"],
                            "subtotal":      ["sub", "subtotalamount"],
                            "customername":  ["clientname", "billto", "billedto", "soldto"],
                        }
                        aliases = XLSX_ALIAS.get(key_norm, [])
                        for alias in aliases:
                            if alias in label_map:
                                value = label_map[alias]
                                break

                    # Fuzzy fallback: substring match
                    if not value:
                        for lbl, val in label_map.items():
                            if key_norm in lbl or lbl in key_norm:
                                value = val
                                break

                    # Special fallbacks per field
                    if not value:
                        if key_norm in ("customername", "clientname", "billto"):
                            for row in all_rows:
                                cells = [str(c).strip() if c is not None else "" for c in row]
                                if len(cells) >= 4 and cells[3] and ":" not in cells[3]:
                                    v = cells[3].strip()
                                    if v and not _re.match(r'^[\+\d\s\(\)\-]+$', v):
                                        value = v
                                        break

                    # Confidence and status
                    if value and str(value).strip() not in ("", "None"):
                        confidence = 0.95
                        status     = "Pass"
                        message    = "Valid"
                    else:
                        value      = None
                        confidence = 0.0
                        status     = "Fail"
                        message    = "Required field missing"

                    invoice_data[key] = value
                    validation_data.append({
                        "FileName":        file_name,
                        "Page":            p_num,
                        "Field":           key,
                        "Extracted Value": value,
                        "Confidence":      confidence,
                        "Status":          status,
                        "Message":         message,
                    })

                # Save Excel output
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                output_file = os.path.join(OUTPUT_DIR, f"{file_name}.xlsx")
                import pandas as pd
                with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
                    pd.DataFrame([invoice_data]).to_excel(writer, sheet_name="Summary",    index=False)
                    pd.DataFrame(validation_data).to_excel(writer, sheet_name="Validation", index=False)

                # Save to DB
                save_document_scores(document_id, validation_data)
                save_to_document_scores_table(document_id, validation_data)

                columns_json  = json.dumps([key for _, key in rule_items])
                csv_data_json = json.dumps([invoice_data])
                save_to_master_csv_config(
                    document_id, filename, columns_json, csv_data_json
                )

                update_extracted_data_location(document_id, {
                    "timestamp":    __import__("datetime").datetime.now().isoformat(),
                    "file_name":    filename,
                    "total_fields": len(validation_data),
                    "passed":       sum(1 for r in validation_data if r["Status"] == "Pass"),
                    "warnings":     sum(1 for r in validation_data if r["Status"] == "Warning"),
                    "failed":       sum(1 for r in validation_data if r["Status"] == "Fail"),
                })

                os.remove(input_path)

                return {
                    "status":          "success",
                    "validation_data": validation_data,
                    "files": [{
                        "filename":     os.path.basename(output_file),
                        "download_url": f"/ocr/download/{os.path.basename(output_file)}"
                    }]
                }

            except Exception as e:
                return {"status": "error", "message": f"XLSX processing failed: {e}"}

        doc = None
        if document_id:
            try:
                doc = get_document_by_id(document_id)
            except Exception as e:
                print(f"Warning: could not fetch doc {document_id}: {e}")

        rules_raw = payload.get("rules")
        if not rules_raw and doc and doc.get("extraction_rules_json"):
            rules_raw = doc.get("extraction_rules_json")

        if isinstance(rules_raw, str):
            try:
                rules = json.loads(rules_raw)
            except Exception:
                rules = {}
        elif isinstance(rules_raw, dict):
            rules = rules_raw
        else:
            rules = {}

        from ML.JsonTransformation import parse_extraction_rules
        page_fields_map = parse_extraction_rules(rules)
        pages_to_extract = sorted(page_fields_map.keys()) if page_fields_map else None

        searchable_pdf_path = None
        try:
            with open(input_path, "rb") as uploaded_file:
                upload_bytes = uploaded_file.read()
            fastocr_results = _fastocr_process_upload(
                filename, upload_bytes, _content_type(filename), pages_to_extract
            )
            json_path = os.path.join(JSON_DIR, f"{os.path.splitext(filename)[0]}.json")
            with open(json_path, "w", encoding="utf-8") as json_file:
                json.dump(_fastocr_json_by_page(fastocr_results), json_file, ensure_ascii=False)
            searchable_pdf_name = f"{os.path.splitext(filename)[0]}.fastocr.pdf"
            searchable_pdf_path = os.path.join(OUTPUT_DIR, searchable_pdf_name)
            searchable_pdf = PdfWriter()
            for _, result in fastocr_results:
                for page in PdfReader(io.BytesIO(result.searchable_pdf)).pages:
                    searchable_pdf.add_page(page)
            with open(searchable_pdf_path, "wb") as pdf_file:
                searchable_pdf.write(pdf_file)
        except (FastOCRError, OSError, ValueError) as exc:
            return {"status": "error", "message": f"FastOCR processing failed: {exc}"}

        output_files, validation_data = process(JSON_DIR, OUTPUT_DIR, rules)

        # ============ STORE ALL OUTPUT IN DATABASE ============
        
        # 1. Save to document_scores_dbt (main scores table)
        if validation_data and document_id:
            save_document_scores(document_id, validation_data)
        
        # 2. Save to document_scores table (has approval fields)
        if document_id:
            save_to_document_scores_table(
                document_id=document_id,
                validation_data=validation_data,
                approved_by=None
            )
        
        # 3. Build invoice_data from validation results
        invoice_data = {}
        for item in validation_data:
            field_name = item.get("Field", "").rstrip(":")
            extracted_value = item.get("Extracted Value", "")
            invoice_data[field_name] = extracted_value
        
        # 4. Save to master_csv_config as CSV data
        excel_file_path = output_files[0] if output_files else None
        
        columns_json = json.dumps(list(invoice_data.keys()))
        csv_data_json = json.dumps([invoice_data])  # Array of rows
        
        if document_id:
            save_to_master_csv_config(
                document_id=document_id,
                csv_filename=filename.replace('.pdf', '.csv'),
                columns_json=columns_json,
                csv_data_json=csv_data_json,
                primary_key="document_id"
            )
            
            # 5. Update extracted_data_location in document_type_master
            extraction_info = {
                "extraction_id": int(datetime.now().timestamp()),
                "timestamp": datetime.now().isoformat(),
                "file_name": filename,
                "total_fields": len(validation_data),
                "passed": sum(1 for v in validation_data if v.get("Status") == "Pass"),
                "warnings": sum(1 for v in validation_data if v.get("Status") == "Warning"),
                "failed": sum(1 for v in validation_data if v.get("Status") == "Fail"),
                "excel_file": excel_file_path,
                "output_files": [os.path.basename(f) for f in output_files],
                "pages_processed": pages_to_extract if pages_to_extract else "all"
            }
            
            update_extracted_data_location(document_id, extraction_info)

        return {
            "status": "success",
            "validation_data": validation_data,
            "extraction_summary": {
                "total_fields": len(validation_data),
                "pages_processed": pages_to_extract if pages_to_extract else "all",
                "deterministic_rules_applied": bool(page_fields_map),
                "saved_to_document_scores_dbt": bool(document_id),
                "saved_to_document_scores": bool(document_id),
                "saved_to_master_csv_config": bool(document_id),
                "updated_document_master": bool(document_id),
                "statistics": {
                    "passed": sum(1 for v in validation_data if v.get("Status") == "Pass"),
                    "warnings": sum(1 for v in validation_data if v.get("Status") == "Warning"),
                    "failed": sum(1 for v in validation_data if v.get("Status") == "Fail")
                }
            },
            "files": ([{
                    "filename": os.path.basename(searchable_pdf_path),
                    "download_url": f"/ocr/download/{os.path.basename(searchable_pdf_path)}"
                }] if searchable_pdf_path else []) + [
                {
                    "filename": os.path.basename(f),
                    "download_url": f"/ocr/download/{os.path.basename(f)}"
                }
                for f in output_files
            ]
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


@ocr_router.get("/download/{filename}")
def download_file(filename: str):
    file_path = os.path.join(OUTPUT_DIR, filename)
    media_type = (
        "application/pdf"
        if filename.lower().endswith(".pdf")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=media_type
    )


@ocr_router.post("/save-document-scores")
def save_scores(payload: dict = Body(...)):
    try:
        document_id     = payload.get("document_id")
        validation_data = payload.get("validation_data", [])
        save_document_scores(document_id, validation_data)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@ocr_router.delete("/documents/{document_id}")
def delete_doc(document_id: int):
    try:
        delete_document(document_id)
        return {"status": "deleted"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@ocr_router.get("/document-scores/{document_id}")
def get_scores(document_id: int):
    return get_document_scores(document_id)


@app.get("/document-scores/{document_id}")
def get_scores_top(document_id: int):
    return get_document_scores(document_id)


# ============ NEW API ENDPOINTS FOR YOUR 4 TABLES ============

@ocr_router.get("/document-scores-detailed/{document_id}")
def get_detailed_scores(document_id: int):
    """Get scores from document_scores table (with approval info)"""
    try:
        scores = get_document_scores_with_approval(document_id)
        return {"status": "success", "data": scores}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@ocr_router.post("/approve-extraction/{score_id}")
def approve_extraction(score_id: int, payload: dict = Body(...)):
    """Approve or correct an extracted value"""
    try:
        approved_value = payload.get("approved_value")
        approved_by = payload.get("approved_by", "admin")
        
        result = approve_extraction_value(score_id, approved_value, approved_by)
        return {"status": "success", "data": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@ocr_router.get("/csv-config/{document_id}")
def get_csv_config(document_id: int):
    """Get CSV configuration for a document"""
    try:
        configs = get_csv_config_by_document(document_id)
        return {"status": "success", "data": configs}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@ocr_router.get("/extraction-summary/{document_id}")
def get_extraction_summary_endpoint(document_id: int):
    """Get extraction summary statistics"""
    try:
        summary = get_extraction_summary(document_id)
        return {"status": "success", "data": summary}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@ocr_router.get("/document-master/{document_id}")
def get_document_master(document_id: int):
    """Get document master record with extraction history"""
    try:
        doc = get_document_by_id(document_id)
        if doc and doc.get('extracted_data_location'):
            try:
                doc['extraction_history'] = json.loads(doc['extracted_data_location'])
            except:
                doc['extraction_history'] = []
        return {"status": "success", "data": doc}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@ocr_router.get("/all-extractions/{document_id}")
def get_all_extractions(document_id: int):
    """Get all extraction data from all tables for a document"""
    try:
        result = {
            "document_info": get_document_by_id(document_id),
            "scores_dbt": get_document_scores(document_id),
            "scores_detailed": get_document_scores_with_approval(document_id),
            "csv_configs": get_csv_config_by_document(document_id),
            "extraction_summary": get_extraction_summary(document_id)
        }
        
        # Parse extraction history
        if result["document_info"] and result["document_info"].get('extracted_data_location'):
            try:
                result["extraction_history"] = json.loads(result["document_info"]["extracted_data_location"])
            except:
                result["extraction_history"] = []
        
        return {"status": "success", "data": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@ocr_router.get("/health")
def health_check():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchall()
        cursor.close()
        conn.close()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": "disconnected", "error": str(e)}


app.include_router(ocr_router)