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

FastOCR jobs are created, uploaded to their presigned URL with the detected
content type, started, polled with a bounded timeout, and then downloaded as
text and a searchable PDF. The text is passed through the existing field
transformation and database pipeline; the searchable PDF is included in the
response's `files` list. API keys, presigned URLs, and document contents are not
logged.

## Tests and evidence

Run the focused matcher tests:

```bash
python -m unittest discover -s tests -v
```

Real invoice evidence from the verification run is under
`tests/updated_v1/`, including requests, API responses, raw Azure JSON, XLSX
outputs, and database score responses. The detailed status and limitations are in
`DELIVERY_REPORT.md`.

The `Input/`, `Json/`, `Output/`, and `ExtractedInput/` directories contain local
runtime artifacts and should not be used as a substitute for deployment storage.
# trans-v2
