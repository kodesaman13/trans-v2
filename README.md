# Arabic Invoice Extraction - Version 1

This repository contains the existing Version 1 FastAPI pipeline with Arabic-aware
field matching layered onto an asynchronous FastOCR document flow. FastOCR is the
OCR provider for document and invoice extraction.

## Configuration

Provide these files locally; do not commit them:

- `Azure/Credentials.json`

  ```json
  {
    "API_KEY": "<azure-document-intelligence-key>",
    "ENDPOINT": "https://<resource>.cognitiveservices.azure.com/"
  }
  ```

- `db.py` with valid MySQL connection settings, or replace the connection factory
  with the deployment's secret-backed configuration. Database credentials are not
  stored in this README.

Optional English translation for selected Arabic descriptive fields uses Azure
AI Translator after extraction and validation:

```bash
export AZURE_TRANSLATOR_KEY="<translator-key>"
export AZURE_TRANSLATOR_ENDPOINT="https://api.cognitive.microsofttranslator.com"
export AZURE_TRANSLATOR_REGION="<azure-region>"
```

The original Arabic value remains in `Extracted Value`; the English result is
returned separately as `Translated Value`. Numeric IDs, dates, amounts, VAT,
CR, invoice numbers, and codes are not translated.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn api_json:app --host 127.0.0.1 --port 8000 --reload
```

Open Swagger at <http://127.0.0.1:8000/docs>.

## Upload

`POST /ocr/upload` accepts JSON:

```json
{
  "document_id": 400,
  "filename": "invoice.pdf",
  "file": "<base64 file bytes>"
}
```

The document ID selects the active `document_type_master` row and its
`extraction_rules_json`. The uploaded document is sent to the selected OCR
provider, then the existing extraction rules and validation pipeline runs. The API
returns validation rows and an XLSX download URL; normalized OCR JSON is written
to `Json/`.

For the built-in Arabic invoice template, no setup request or numeric ID is
needed. Send `"document_type": "arabic_invoice"` with the upload; the backend
loads the existing template or creates it once, then reuses it for later uploads:

```json
{
  "document_type": "arabic_invoice",
  "filename": "invoice.pdf",
  "file": "<base64 file bytes>"
}
```

Custom templates can still use `"document_id"` or inline `"rules"` as before.
The built-in template uses `"pages": {"*": [...]}` and expands that field set
to every page in each uploaded PDF, so it is not limited to pages 1 or 3.

To test every local PDF in `Input/` with one command, provide
`FASTOCR_API_KEY` in the environment and run:

```bash
FASTOCR_API_KEY='YOUR_VALID_KEY' PYTHONPATH=. .venv/bin/python test_all_fastocr_pdfs.py
```

The runner starts a temporary local API if one is not already running, only reads
`Input/*.pdf`, uses the reusable Arabic invoice template, and prints
status/page/field summaries without printing OCR contents or secrets. It stops
only a server process it started itself.

## FastOCR provider

FastOCR is the default OCR provider. Configure its asynchronous flow through the
runtime environment:

```bash
export OCR_PROVIDER=fastocr
export FASTOCR_API_KEY="<fastocr-api-key>"
# Optional: FASTOCR_BASE_URL, FASTOCR_REQUEST_TIMEOUT,
# FASTOCR_POLL_INTERVAL, FASTOCR_POLL_TIMEOUT
```

When `FASTOCR_API_KEY`/`FASTOCR_KEY` is missing, the upload returns a
configuration error rather than silently using another OCR provider.

For true field-region OCR, a typed field may include normalized PDF coordinates
`[x, y, width, height]`, each between `0` and `1`:

```json
{
  "name": "invoice_number",
  "labels": {"ar": ["رقم الفاتورة"], "en": ["Invoice Number"]},
  "region": [0.60, 0.10, 0.35, 0.08],
  "required": true
}
```

When regions are present, only those crops are sent to FastOCR. Label-only rules
send only the selected pages, and no deterministic rules send all pages.

FastOCR jobs are created, uploaded to their presigned URL with the detected
content type, started, polled with a bounded timeout, and then downloaded as
text and a searchable PDF. The text is passed through the existing field
transformation and database pipeline; the searchable PDF is included in the
response's `files` list. API keys, presigned URLs, and document contents are not
logged.

## Tests and evidence

Run the automated regression tests without a FastOCR key or database:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

This covers the Arabic matcher, FastOCR request lifecycle, timeout handling,
region validation, page preservation, and all-page wildcard expansion.

### Testing a real PDF

Keep the key only in the shell environment. Never put it in `.env`, Python
files, README files, or a commit:

```bash
export FASTOCR_API_KEY='YOUR_VALID_KEY'
export OCR_PROVIDER=fastocr
```

Start the API in a second terminal:

```bash
source .venv/bin/activate
uvicorn api_json:app --host 127.0.0.1 --port 8000 --reload
```

Check that the API and database are ready:

```bash
curl -sS http://127.0.0.1:8000/ocr/health
```

Test one PDF with the reusable all-pages Arabic invoice template:

```bash
curl -sS -X POST http://127.0.0.1:8000/ocr/upload \
  -H 'Content-Type: application/json' \
  --data-binary @<(python -c '
import base64, json, sys
p = sys.argv[1]
print(json.dumps({
  "document_type": "arabic_invoice",
  "filename": p.rsplit("/", 1)[-1],
  "file": base64.b64encode(open(p, "rb").read()).decode()
}))
' Input/invoice.pdf)
```

Replace `Input/invoice.pdf` with the local PDF path. The response includes
`extraction_summary`, `validation_data`, and generated file paths. The
`arabic_invoice` wildcard template applies its configured fields to every page
in the uploaded PDF.

Test every PDF in the local `Input/` folder with one command:

```bash
FASTOCR_API_KEY="$FASTOCR_API_KEY" \
PYTHONPATH=. .venv/bin/python test_all_fastocr_pdfs.py
```

The batch runner discovers only `Input/*.pdf`, starts a temporary API if port
8000 is not already serving, prints page/field/pass/fail summaries, and stops
only the process it started. Set `OCR_BASE_URL` when testing another local API
URL. A provider HTTP 401/403 means the key is not authorized; it is not proof
that invoice extraction failed.

After a successful upload, use the returned `document_id` to inspect persisted
results:

```bash
curl -sS http://127.0.0.1:8000/ocr/document-scores-detailed/<document_id>
curl -sS http://127.0.0.1:8000/ocr/csv-config/<document_id>
curl -sS http://127.0.0.1:8000/ocr/all-extractions/<document_id>
```

The `Input/`, `Json/`, `Output/`, and `ExtractedInput/` directories contain
local runtime artifacts and are ignored by Git. Do not commit them, API keys,
database credentials, presigned URLs, OCR contents, or generated output files.

## Safe commit scope

From the repository root, review the changes first:

```bash
git status --short
git diff --check -- README.md docs/arabic-v1-architecture.md \
  tests/test_fastocr_client.py test_all_fastocr_pdfs.py
```

The source changes for this integration are the tracked files
`api_json.py`, `document_service.py`, `fastocr_client.py`, `requirements.txt`,
`tests/test_fastocr_client.py`, `README.md`, and
`docs/arabic-v1-architecture.md`, plus the new
`test_all_fastocr_pdfs.py` runner. Stage only those files after reviewing the
diff:

```bash
git add api_json.py document_service.py fastocr_client.py requirements.txt \
  tests/test_fastocr_client.py README.md docs/arabic-v1-architecture.md \
  test_all_fastocr_pdfs.py
git diff --cached --check -- README.md docs/arabic-v1-architecture.md \
  tests/test_fastocr_client.py test_all_fastocr_pdfs.py
```

Do not use `git add .` because local PDFs, generated JSON/XLSX files, and
unrelated working-tree changes must remain outside this integration commit.

The `Input/`, `Json/`, `Output/`, and `ExtractedInput/` directories contain local
runtime artifacts and should not be used as a substitute for deployment storage.
# trans-v2
