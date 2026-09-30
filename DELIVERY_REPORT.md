# Version 1 Arabic Invoice Delivery Report

## Scope

The changes remain inside the existing Version 1 codebase. No new OCR engine,
translation service, AWS service, or unrelated runtime dependency was added.
Azure Document Intelligence remains the only active OCR service.

The active implementation changes are in `ML/JsonTransformation.py`; focused
tests are in `tests/test_arabic_matching.py`. The upload isolation change is in
`ML/PdfToExcel.py` and `api_json.py`. Evidence is stored under
`tests/updated_v1/`.

This workspace is folder-backed and is **not a Git repository**. Therefore
`git diff --stat` and a commit-based original comparison are unavailable. The
actual touched source files are listed above; the delivery documentation and
`.gitignore` were added for packaging.

## Changes and rationale

| File | Change | Why |
|---|---|---|
| `ML/JsonTransformation.py` | Matching-only Arabic normalization for alef forms, tashkeel, tatweel, ya/alef-maqsura, ta-marbuta, digits, and whitespace | Improve label recognition without changing returned invoice values |
| `ML/JsonTransformation.py` | Central numeric-label validation | Prevent descriptions or labels from being selected as numeric totals |
| `ML/PdfToExcel.py` | Optional uploaded-file filtering | Prevent a request from OCR-processing older files left in `Input` |
| `api_json.py` | Pass current uploaded filename to the PDF runner | Ensure each request processes its submitted file only |
| `tests/test_arabic_matching.py` | Four unit tests | Cover normalization, digits, RTL matching, and numeric candidate selection |
| `README.md` | Setup and run instructions | Make the existing V1 codebase runnable locally |
| `.gitignore` | Ignore credentials, environments, and runtime artifacts | Prevent secrets and generated files from being packaged |
| `DELIVERY_REPORT.md` | Evidence and limitations | Record actual verification results |

No new package was added to `requirements.txt`.

## Phase 5 evidence

The real verification run used:

- Arabic sample: `Arabic_Invoice_Examples_Corrected_7110.pdf`
- English sample: `English_Invoices.pdf`
- Azure responses: `tests/updated_v1/raw/`
- API request metadata: `tests/updated_v1/requests/`
- API responses: `tests/updated_v1/responses/`
- XLSX results: `tests/updated_v1/output/`
- Database score rows: `tests/updated_v1/db/`
- Run summary: `tests/updated_v1/runs/index.json`

### Run summary

| Run | Pages | Deterministic | Fields | Pass | Warning | Fail | DB rows |
|---|---:|---:|---:|---:|---:|---:|---:|
| Arabic 384 | 1 | Yes | 8 | 1 | 6 | 1 | 8 |
| Arabic 399 | 1 | Yes | 11 | 0 | 2 | 9 | 11 |
| Arabic 400 | 1 | Yes | 13 | 0 | 12 | 1 | 13 |
| Arabic 402 | all | No | 3 | 3 | 0 | 0 | 3 |
| Arabic 403 | 1 | Yes | 13 | 0 | 13 | 0 | 13 |
| Arabic 404 | 1 | Yes | 13 | 2 | 11 | 0 | 13 |
| Arabic 405 | 1 | Yes | 13 | 0 | 13 | 0 | 13 |
| English 247 | 1 | Yes | 9 | 3 | 0 | 6 | 9 |
| English 382 | 1 | Yes | 5 | 2 | 1 | 2 | 5 |

All nine API runs returned `status: success`. Database row counts matched the
API validation-field counts for every run.

### Arabic field → value → confidence evidence

The following values are from the real document-400 run. Full rows are in
`tests/updated_v1/responses/arabic_invoice_doc400.json` and the corresponding
database response is in `tests/updated_v1/db/arabic_invoice_doc400.json`.

| Page | Field | Expected/source value | Extracted value | Match | Confidence | Notes |
|---:|---|---|---|:---:|---:|---|
| 1 | رقم الفاتورة | `20261001` | `20261001` | Y | 0 | Azure page-line fallback; no field confidence supplied |
| 1 | تاريخ الفاتورة | `٢٠٢٦ سبتمبر ١١` | `٢٠٢٦ سبتمبر ١١` | Y | 0 | RTL line fallback |
| 1 | الرقم الضريبي | `٣١٠٩٨٧٦٥٤٣٠٠٠٠٣` | `٣١٠٩٨٧٦٥٤٣٠٠٠٠٣` | Y | 0 | RTL line fallback; digits preserved |
| 1 | المجموع قبل الضريبة | `48,750.00 SAR` | `48,750.00 SAR` | Y | 0 | Numeric page-line fallback |
| 1 | ضريبة القيمة المضافة | `7,312.50 SAR` | `7,312.50 SAR` | Y | 0 | Numeric page-line fallback |
| 1 | الإجمالي | `56,062.50 SAR` | `56,062.50 SAR` | Y | 0 | Generic total resolved to numeric total |
| 1 | الإجمالي المستحق | `56,062.50 SAR` | `56,062.50 SAR` | Y | 0 | Correct total, not VAT amount |
| 1 | VAT | `310123456700003` | `310123456700003` | Y | 0.674 | Azure key-value confidence retained |
| 1 | CR | `1010987654` | `1010987654` | Y | 0.669 | Azure key-value confidence retained |
| 1 | السجل التجاري | `1010987654` | `1010987654` | Y | 0.669 | Explicit CR mapping |
| 1 | الشحن | no source value | empty | Y | 0 | Correctly reported missing |

Confidence is explainable: Azure KV values retain their actual Azure values
(`0.674`, `0.669`); page-line fallbacks receive `0` because Azure did not
provide a field-level confidence. The `< 0.7` status threshold marks those
values as warnings; no score was boosted or clamped.

## English verification

Post-change English runs were completed for document IDs 247 and 382 using the
real `English_Invoices.pdf`. Their complete responses and raw JSON are in the
corresponding `tests/updated_v1/` files.

A Phase 2 baseline directory was not created in this workspace:

```text
tests/baseline_english/ does not exist
```

Therefore an actual baseline-versus-post-change diff cannot be produced. The
post-change results are recorded honestly:

- English 247: 3 pass, 0 warnings, 6 missing fields.
- English 382: 2 pass, 1 warning, 2 missing fields.
- API responses succeeded.
- Database row counts matched API field counts.

These results do not prove “no English regression” against Phase 2 because the
requested baseline artifacts are absent. The missing fields are configuration
or source/template mismatches and require comparison with the original English
expected values.

## Known limitations

1. Several live Arabic master rules, especially 399 and 403–405, contain
   reversed labels or literal OCR values as rule keys. Those templates need
   master-table cleanup.
2. Page rules select and OCR pages, then filter fields. True field-region OCR
   requires coordinates/bounding boxes in the rule JSON.
3. Page-line fallback values have no Azure field-level confidence and therefore
   report `0` honestly.
4. Azure confidence for the tested VAT/CR values is below the `0.7` pass
   threshold (`0.674` and `0.669`).
5. The available samples do not provide verified business ground truth for
   every configured field, so unmatched fields are not called correct.
6. The Phase 2 English baseline was not saved and cannot be reconstructed
   without inventing a comparison.
7. `arabic_invoice_translator.py` is a standalone legacy utility containing
   Tesseract/Argos imports; it is not imported by the active API and is not
   part of the Azure V1 runtime path.
8. `db.py` and `Azure/Credentials.json` contain deployment configuration and
   must be supplied through secure deployment handling, never committed.

## 51-item task status

Status is based only on evidence available in this workspace.

| # | Task | Status | Evidence |
|---:|---|---|---|
| 1 | Read Version 1 code | Done | Phase 1 architecture report |
| 2 | Understand master table/templates | Done | Phase 1 report; `document_service.py` |
| 3 | Understand Extraction Rule JSON | Done | Phase 1 report; `parse_extraction_rules()` |
| 4 | Identify pages/fields | Done | Phase 1 report; live template inspection |
| 5 | Trace PDF/Base64 flow | Done | Phase 1 report; `api_json.py` |
| 6 | Trace Azure OCR flow | Done | Phase 1 report; `ML/PdfToExcel.py` |
| 7 | Locate field matching | Done | `ML/JsonTransformation.py` |
| 8 | Locate confidence handling | Done | `extract_data()` and `document_service.py` |
| 9 | Test English flow first | Not Done | No Phase 2 baseline artifacts; post-change English only |
| 10 | Confirm English extraction | Not Done | Baseline absent; post-change results are partial |
| 11 | Check API/Swagger | Done | Phase 5 API responses and local Swagger |
| 12 | Check database storage | Done | Phase 5 `tests/updated_v1/db/` |
| 13 | Test original Arabic invoices | Done | Phase 3 raw runs and Phase 5 runs |
| 14 | Inspect Arabic rule names | Done | Phase 3 report; live rules |
| 15 | Check Arabic recognition | Done | Phase 3 field analysis |
| 16 | Check Arabic values | Done | Phase 5 document-400 evidence |
| 17 | Check repeated wrong values | Done | Phase 3 repeated-value analysis |
| 18 | Compare Arabic/English fields | Done | Phase 3 report |
| 19 | Investigate low confidence | Done | Phase 3 confidence traces |
| 20 | Identify confidence source | Done | Phase 3 root-cause table |
| 21 | Preserve V1 architecture | Done | Existing API/Azure flow retained |
| 22 | Preserve master-table logic | Done | No schema/template code changed |
| 23 | Preserve Extraction Rule flow | Done | Parser remains active |
| 24 | Preserve Azure OCR | Done | `prebuilt-document` remains active |
| 25 | Improve Arabic matching | Done | `ML/JsonTransformation.py`; unit tests |
| 26 | Link fields to correct values | Done for tested 400 fields | Phase 5 document-400 table |
| 27 | Return actual Arabic values | Done for tested values | Phase 5 document-400 table |
| 28 | Do not translate values | Done | Active API has no translation dependency |
| 29 | Correct confidence handling | Done for fallback transparency | Azure confidence retained; fallback remains 0 |
| 30 | Avoid artificial boosts | Done | Code inspection; no boosts/clamps added |
| 31 | Test Arabic-only invoices | Done | Phase 5 Arabic runs |
| 32 | Test mixed Arabic/English invoices | Not Done | No separately verified mixed ground-truth fixture |
| 33 | Test Arabic field/Arabic value | Done | Document 400 fields |
| 34 | Test English field/English value | Done | English post-change runs |
| 35 | Test mixed field/value cases | Not Done | No independently labeled mixed fixture |
| 36 | Test VAT/CR/number/date/other fields | Done for document 400 | Phase 5 field table |
| 37 | Test multi-page rules | Partial | Document 400 page 1 slicing verified; no multi-page configured template run |
| 38 | Test 400-series 400/402/403/404/405 | Done | Phase 5 runs and index |
| 39 | Compare expected/extracted values | Partial | Document-400 source-derived table; no full ground truth |
| 40 | Verify every confidence | Done | API rows and confidence trace |
| 41 | Verify database results | Done | DB row counts and JSON rows |
| 42 | Verify API/Swagger | Done | Request/response artifacts |
| 43 | English regression | Not Done | No saved Phase 2 baseline |
| 44 | Change existing V1 code | Done | Source changes are in V1 modules |
| 45 | No integration snippets | Done | Direct source changes |
| 46 | No new OCR system | Done | Azure-only active path |
| 47 | No Google/AWS/unrelated service | Done | `requirements.txt` unchanged; active path uses Azure |
| 48 | Complete updated codebase | Partial | Runnable source/docs packaged; credentials remain deployment-supplied |
| 49 | Actual Arabic evidence | Done | `tests/updated_v1/` |
| 50 | Arabic value/confidence evidence | Done for tested fields | Document-400 evidence table |
| 51 | English V1 continues | Not Proven | Baseline missing; post-change runs recorded |

## Packaging conclusion

The deliverable is a runnable V1 codebase with Azure-only active OCR, Arabic
matching improvements, focused tests, setup documentation, and real verification
artifacts. It is not a claim that every template is production-ready: the
remaining template-rule cleanup, missing English baseline, field-region OCR,
and broader ground-truth validation are explicitly recorded above.
