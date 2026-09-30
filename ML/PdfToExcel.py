import sys
import os
import json
import subprocess
from pathlib import Path
import PyPDF2
from azure.core.exceptions import ResourceNotFoundError, ServiceRequestError
from azure.core.credentials import AzureKeyCredential
from azure.ai.formrecognizer import DocumentAnalysisClient
import socket


def validate_azure_api(endpoint):
    hostname = endpoint.replace('https://', '').replace('http://', '').split('/')[0]
    try:
        socket.gethostbyname(hostname)
        return True
    except socket.gaierror:
        raise Exception("Azure API endpoint cannot be resolved, service may be expired or not working")


def read_json_Instructions(pdf_file):
    try:
        json_pdf_file = os.path.splitext(pdf_file)[0] + ".json"
        if os.path.exists(json_pdf_file):
            with open(json_pdf_file, 'r', encoding='utf-8') as f:
                json_file = json.load(f)
            return json_file['Pages']
        else:
            return False
    except ValueError:
        return False


def pdf_to_text(pdf_file, pages_to_extract=None):
    """
    Send PDF/image to Azure Form Recognizer (prebuilt-document).
    COST OPTIMIZATION:
    - If pages_to_extract is specified (e.g. [1, 3]), only those pages are sliced
      and sent to Azure, avoiding OCR billing for unneeded pages.
    - Sliced pages are remapped back to original document page numbers.
    - If pages_to_extract is None/empty, all pages are processed.
    """
    credentials_path = os.path.join(
        os.path.abspath(os.getcwd()),
        "Azure",
        "Credentials.json"
    )

    with open(credentials_path, 'r', encoding='utf-8') as f:
        credentials = json.load(f)
    API_KEY = credentials['API_KEY']
    ENDPOINT = credentials['ENDPOINT']

    validate_azure_api(ENDPOINT)

    form_recognizer_client = DocumentAnalysisClient(
        endpoint=ENDPOINT,
        credential=AzureKeyCredential(API_KEY)
    )

    if pages_to_extract is None:
        instructions = read_json_Instructions(pdf_file)
        if instructions:
            pages_to_extract = instructions

    slice_page_map = None
    ext = os.path.splitext(pdf_file)[1].lower()

    if pages_to_extract and ext == ".pdf":
        with open(pdf_file, "rb") as f:
            pdf_reader = PyPDF2.PdfReader(f)
            pageCount = len(pdf_reader.pages)
            pages = list(pdf_reader.pages)

            if isinstance(pages_to_extract, (int, str)):
                pages_to_extract = [pages_to_extract]

            valid_pages = []
            for p in pages_to_extract:
                try:
                    p_int = int(p)
                    if 1 <= p_int <= pageCount and p_int not in valid_pages:
                        valid_pages.append(p_int)
                except (ValueError, TypeError):
                    continue
            valid_pages.sort()

            if valid_pages:
                pdf_writer = PyPDF2.PdfWriter()
                for p in valid_pages:
                    pdf_writer.add_page(pages[p - 1])

                extracted_pdf_path = pdf_file.replace("Input", "ExtractedInput")
                os.makedirs(os.path.dirname(extracted_pdf_path), exist_ok=True)
                with open(extracted_pdf_path, "wb") as output_file:
                    pdf_writer.write(output_file)
                with open(extracted_pdf_path, "rb") as f:
                    form = f.read()

                # Map sliced Azure page numbers (1..len(valid_pages)) back to original page numbers
                slice_page_map = {idx + 1: orig_p for idx, orig_p in enumerate(valid_pages)}
                print(f"📄 Cost optimization: OCR ONLY pages {valid_pages} (out of {pageCount} total pages)")
            else:
                print(f"⚠️ Requested pages {pages_to_extract} out of range (total pages: {pageCount}). Reading full file.")
                with open(pdf_file, "rb") as f:
                    form = f.read()
    else:
        print("📄 No deterministic page restriction: Performing OCR on ALL pages.")
        with open(pdf_file, "rb") as f:
            form = f.read()

    print("FILE SIZE:", len(form))
    print("PDF PATH:", pdf_file)

    try:
        # prebuilt-document handles ALL document types (invoices, certs, timesheets, MMR forms)
        poller = form_recognizer_client.begin_analyze_document(
            "prebuilt-document",
            form
        )
        result = poller.result()

    except Exception as e:
        print("AZURE ERROR:", str(e))
        raise

    output = {}

    # Structured fields (present for invoice-type docs)
    if result.documents:
        doc = result.documents[0]
        doc_dict = doc.to_dict()
        fields_dict = doc_dict.get("fields", {})
        if slice_page_map:
            for field_name, field_val in fields_dict.items():
                if isinstance(field_val, dict) and "bounding_regions" in field_val:
                    for br in field_val.get("bounding_regions", []):
                        if isinstance(br, dict) and "page_number" in br:
                            br["page_number"] = slice_page_map.get(br["page_number"], br["page_number"])
        output["fields"] = fields_dict
    else:
        output["fields"] = {}

    # Raw key-value pairs (works for text-based PDFs)
    kv_list = []
    for kv in result.key_value_pairs:
        key_content = kv.key.content if kv.key else ""
        val_content = kv.value.content if kv.value else ""
        p_num = 1
        if kv.key and hasattr(kv.key, "bounding_regions") and kv.key.bounding_regions:
            p_num = kv.key.bounding_regions[0].page_number
        elif kv.value and hasattr(kv.value, "bounding_regions") and kv.value.bounding_regions:
            p_num = kv.value.bounding_regions[0].page_number

        orig_p_num = slice_page_map.get(p_num, p_num) if slice_page_map else p_num

        kv_list.append({
            "key": {"content": key_content},
            "value": {"content": val_content},
            "confidence": kv.confidence if kv.confidence else 0,
            "page_number": orig_p_num
        })
    output["key_value_pairs"] = kv_list

    # Page lines — fallback for scanned/image PDFs where KV pairs are empty
    page_lines = []
    for page in result.pages:
        orig_page_num = slice_page_map.get(page.page_number, page.page_number) if slice_page_map else page.page_number
        for line in page.lines:
            page_lines.append({
                "content": line.content,
                "page_number": orig_page_num
            })
    output["page_lines"] = page_lines
    output["pages_analyzed"] = list(slice_page_map.values()) if slice_page_map else [p.page_number for p in result.pages]

    return [output]


def save_to_json(data, json_file):
    """Save raw Python object as formatted JSON."""
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4, default=str)
    print(f"Text saved to {json_file}")


def delete_Json_Files(output_dir):
    for i in os.listdir(output_dir):
        file_path = os.path.join(output_dir, i)
        if os.path.isfile(file_path):
            os.remove(file_path)


def delete_Extracted_Pdfs(extractedPdfs_dir):
    for i in os.listdir(extractedPdfs_dir):
        file_path = os.path.join(extractedPdfs_dir, i)
        if os.path.isfile(file_path):
            os.remove(file_path)


def process_pdf_files_in_directory(pdf_dir, output_dir, pages_to_extract=None, input_files=None):
    selected_files = set(input_files) if input_files else None
    for pdf_file in os.listdir(pdf_dir):
        if pdf_file.lower().endswith((".pdf", ".png", ".jpg", ".jpeg")):
            if selected_files is not None and pdf_file not in selected_files:
                continue
            pdf_path = os.path.join(pdf_dir, pdf_file)
            all_text = pdf_to_text(pdf_path, pages_to_extract=pages_to_extract)
            json_file = os.path.join(output_dir, f"{os.path.splitext(pdf_file)[0]}.json")
            save_to_json(all_text, json_file)


def call_another_script(current_directory):
    script_path = os.path.join(current_directory, "ML", "JsonTransformation.py")
    subprocess.run([sys.executable, script_path])


def main(pages_to_extract=None, input_files=None):
    current_directory = os.path.abspath(os.getcwd())

    pdf_dir = os.path.join(current_directory, "Input")
    output_dir = os.path.join(current_directory, "Json")
    extractedPdfs_dir = os.path.join(current_directory, "ExtractedInput")

    os.makedirs(pdf_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(extractedPdfs_dir, exist_ok=True)

    delete_Json_Files(output_dir)
    delete_Extracted_Pdfs(extractedPdfs_dir)

    process_pdf_files_in_directory(
        pdf_dir,
        output_dir,
        pages_to_extract=pages_to_extract,
        input_files=input_files,
    )


if __name__ == "__main__":
    main()