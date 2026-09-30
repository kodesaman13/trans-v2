# Arabic support in Version 1

The existing V1 request path is retained:

```text
Invoice upload
  -> document_type_master lookup
  -> extraction_rules_json page selection
  -> FastOCR asynchronous document job (create/upload/start/poll/output)
  -> page-aware Arabic/English matching
  -> value validation and confidence/status
  -> optional translation stage for selected descriptive fields
  -> existing JSON/XLSX/API response
  -> existing V1 score/configuration tables
```

## Rule compatibility

Legacy rules remain valid:

```json
{"pages": {"1": ["Invoice Number", "Invoice Date"]}}
```

Typed rules are supported without changing the database column:

```json
{
  "document_type": "arabic_tax_invoice",
  "pages": {
    "1": {
      "fields": [
        {
          "name": "invoice_number",
          "labels": {
            "ar": ["رقم الفاتورة"],
            "en": ["Invoice Number", "Invoice No"]
          },
          "method": "key_value_or_page_line",
          "value_type": "invoice_number",
          "required": true,
          "translate": false
        }
      ]
    }
  }
}
```

`ML.JsonTransformation.parse_extraction_rules()` continues to return the
legacy `{page: [field_name]}` shape for page slicing and existing consumers.
`parse_extraction_rule_specs()` supplies aliases and validation metadata to the
field matcher.

## Page and database mapping

Configured pages are sliced before OCR. Results retain original page numbers.
Each field continues to map to the existing V1 score row:

```text
document_scores_dbt(document_id, page, field, extracted_values,
                    confidence, status, message, filename)
```

The optional migration in `migrations/001_extraction_run_provenance.sql`
preserves historical runs and stores match/validation provenance. It is not
executed automatically.

## Arabic processing boundaries

Arabic normalization is used only for label matching. The returned value is
the OCR value after safe RTL restoration; it is not translated. Numeric IDs,
dates, amounts, VAT numbers, CR numbers, and codes remain unchanged.

Confidence is not boosted. Provider confidence is retained when available;
page-line/regex fallback remains zero and is reported as a warning. Typed value
validation can additionally mark an extracted value as a warning when it does
not match the configured value type. FastOCR currently returns completed text without provider page-confidence
metadata, so its normalized page-line fallback is explicitly treated as
lower-confidence rather than fabricated as a provider score.

Translation is a separate post-validation stage. The default descriptive-field
selection covers Arabic descriptions, names, customer/vendor fields, addresses,
projects, and bill-to/invoice-to fields. A typed rule can override this with
`"translate": true` or `"translate": false`. The original Arabic value remains
in `Extracted Value`; the English result is stored separately in
`Translated Value`.

Configure Azure AI Translator with environment variables:

```text
AZURE_TRANSLATOR_KEY
AZURE_TRANSLATOR_ENDPOINT   # defaults to Azure's public Translator endpoint
AZURE_TRANSLATOR_REGION
```

Missing credentials or a Translator failure is reported in `Translation` and
`Translation Error`; OCR extraction is not silently replaced or altered.
