import json
import re
import unicodedata
from difflib import SequenceMatcher
import pandas as pd
import os
import urllib.parse
import urllib.request


def normalize(s):
    """Normalize labels for matching only; never use this for returned values."""
    text = unicodedata.normalize("NFKC", str(s)).casefold()
    text = text.replace("\u200e", "").replace("\u200f", "")
    text = re.sub(r"[\u064B-\u065F\u0670\u06D6-\u06ED]", "", text)
    text = text.replace("\u0640", "")
    text = text.translate(str.maketrans({
        "\u06cc": "\u064a",  # Persian yeh
        "\u06d2": "\u064a",  # yeh barree
        "\u06a9": "\u0643",  # Persian kaf
        "\u06c6": "\u0648",  # waw with ring
    }))
    text = re.sub(r"[\u0622\u0623\u0625\u0671]", "\u0627", text)
    text = text.replace("\u0649", "\u064A").replace("\u0629", "\u0647")
    digit_map = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    text = text.translate(digit_map)
    text = re.sub(r"\s+", " ", text).strip()
    return "".join(char for char in text if char.isalnum())


def _arabic_variants(text):
    """Return normalized forms for correctly and visually reversed Arabic OCR."""
    value = str(text or "").strip()
    variants = {normalize(value)}
    if re.search(r"[\u0600-\u06FF]", value):
        # Some PDF/OCR combinations reverse the complete Arabic label.
        variants.add(normalize(value[::-1]))
        # Others reverse characters inside each Arabic word.
        words = value.split()
        variants.add(normalize(" ".join(word[::-1] for word in words)))
        # Tesseract/PDF text extraction can also reverse Arabic word order
        # while reversing characters within each word.
        variants.add(normalize(" ".join(
            word[::-1] for word in reversed(words)
        )))
    return {variant for variant in variants if variant}


def restore_arabic_ocr(text):
    """Restore Arabic characters in OCR output without translating the value."""
    value = str(text or "").strip()
    if not re.search(r"[\u0600-\u06FF]", value):
        return value

    tokens = value.split()
    restored = []
    for token in tokens:
        restored.append(
            re.sub(
                r"[\u0621-\u063A\u0641-\u064A]+",
                lambda match: match.group(0)[::-1],
                token,
            )
        )
    return " ".join(restored)


def _is_numeric_label(key):
    key_norm = normalize(key)
    return key_norm in {
        "taxrate", "tax", "total", "subtotal", "grandtotal",
        "totalamount", "invoicetotal", "discount", "rateperhour",
        "paymentamount", "value"
    } or any(token in key_norm for token in (
        "اجمالي", "المجموع", "ضريبة", "قيمة", "مبلغ", "سعر", "كمية"
    ))


def _labels_match(left, right):
    """Match labels across common Arabic OCR character omissions/reversals."""
    for left_part in left:
        for right_part in right:
            if left_part == right_part:
                return True
            if min(len(left_part), len(right_part)) >= 5:
                ratio = SequenceMatcher(None, left_part, right_part).ratio()
                if ratio >= 0.72:
                    return True
    return False


def _line_label_match(key, label):
    key_variants = _arabic_variants(key)
    label_variants = _arabic_variants(label)
    if key_variants & label_variants:
        return True
    key_words = str(key).split()
    label_words = str(label).split()
    if len(key_words) > 1 and len(label_words) > 1:
        label_word_variants = [_arabic_variants(word) for word in label_words]
        matched_words = 0
        for key_word in key_words:
            key_word_variants = _arabic_variants(key_word)
            if any(
                key_word_variants & candidate_variants
                or _labels_match(key_word_variants, candidate_variants)
                for candidate_variants in label_word_variants
            ):
                matched_words += 1
        if matched_words == len(key_words):
            return True
        # Azure may split a reversed Arabic word into separate visual tokens
        # (for example "المضافة" -> "فاض ملا"). Match every rule word to a
        # distinct OCR word using normalized similarity, never one shared word.
        def word_forms(word):
            compact = normalize(word)
            return {
                compact,
                normalize(word[::-1]),
            } - {""}

        available = [word_forms(word) for word in label_words]
        for index in range(len(label_words) - 1):
            merged = normalize(label_words[index] + label_words[index + 1])
            if merged:
                available.append({merged, merged[::-1]})
        matched = 0
        for key_word in key_words:
            candidates = word_forms(key_word)
            if any(
                any(
                    left == right or (
                        min(len(left), len(right)) >= 4
                        and SequenceMatcher(None, left, right).ratio() >= 0.62
                    )
                    for left in candidates for right in target
                )
                for target in available
            ):
                matched += 1
        if matched == len(key_words):
            return True
    # Fuzzy matching is only safe for single-word labels. For compound labels,
    # matching one shared word can associate a value with the wrong field.
    if len(key_words) == 1 and len(label_words) == 1 and _labels_match(key_variants, label_variants):
        return True
    # OCR may omit a short label's prefix, but do not let one generic word
    # (for example "فاتورة") match an unrelated compound field.
    normalized_key_lengths = [len(part) for part in key_variants]
    if len(key_words) != 1 or not normalized_key_lengths or min(normalized_key_lengths) > 8:
        return False
    return any(
        _labels_match(key_variants, _arabic_variants(word))
        for word in str(label).split()
    )


def _validation_status(value, confidence):
    """Apply the same honest status policy to deterministic and auto-detected fields."""
    if value in [None, "", "blank"]:
        return "Fail", "Required field missing"
    if isinstance(confidence, (int, float)) and confidence < 0.7:
        return "Warning", "Low confidence"
    return "Pass", "Valid"


def _rule_fields(fields):
    """Return field names from list, string, or field-spec objects."""
    if isinstance(fields, str):
        return [fields.strip()] if fields.strip() else []
    if not isinstance(fields, list):
        return []

    names = []
    for field in fields:
        if isinstance(field, str) and field.strip():
            names.append(field.strip())
        elif isinstance(field, dict):
            name = field.get("name") or field.get("field") or field.get("key")
            if name and str(name).strip():
                names.append(str(name).strip())
    return names


def _field_spec(field):
    """Normalize a legacy string or typed rule entry without changing its name."""
    if isinstance(field, str):
        name = field.strip()
        return {
            "name": name,
            "labels": [name] if name else [],
            "required": True,
        }
    if not isinstance(field, dict):
        return None
    name = field.get("name") or field.get("field") or field.get("key")
    if not name or not str(name).strip():
        return None
    labels = field.get("labels", [])
    if isinstance(labels, dict):
        labels = [value for value in labels.values() if isinstance(value, str)]
    elif isinstance(labels, str):
        labels = [labels]
    elif not isinstance(labels, list):
        labels = []
    aliases = field.get("aliases", [])
    if isinstance(aliases, str):
        aliases = [aliases]
    if not isinstance(aliases, list):
        aliases = []
    candidates = [str(name).strip()]
    candidates.extend(str(value).strip() for value in labels + aliases if str(value).strip())
    return {
        **field,
        "name": str(name).strip(),
        "labels": list(dict.fromkeys(candidates)),
        "required": field.get("required", True),
    }


def parse_extraction_rule_specs(rules):
    """Return page-aware typed specs while accepting every legacy V1 rule shape."""
    if not rules:
        return {}
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except Exception:
            return {}

    page_map = {}
    pages = rules.get("pages", rules) if isinstance(rules, dict) else rules
    entries = []
    if isinstance(pages, dict):
        entries = [
            (page, fields.get("fields", fields.get("keys", []))
             if isinstance(fields, dict) else fields)
            for page, fields in pages.items()
        ]
    elif isinstance(pages, list):
        entries = [
            (item.get("page", 1), item.get("fields") or item.get("keys", []))
            for item in pages if isinstance(item, dict)
        ]

    for page, fields in entries:
        try:
            page_num = int(page)
        except (TypeError, ValueError):
            continue
        if isinstance(fields, (str, dict)):
            fields = [fields]
        if not isinstance(fields, list):
            continue
        specs = [spec for spec in (_field_spec(field) for field in fields) if spec]
        if specs:
            page_map[page_num] = specs
    return page_map


def parse_extraction_rules(rules):
    """
    Parses extraction rules into a standardized dictionary:
    {
        1: ["field1", "field2", ...],
        3: ["field3", "field4", ...]
    }
    Returns {} if no deterministic rules are defined.
    """
    return {
        page: [spec["name"] for spec in specs]
        for page, specs in parse_extraction_rule_specs(rules).items()
    }


def _value_validation(value, value_type):
    """Validate common invoice values without rejecting unknown custom types."""
    if not value_type or value is None:
        return True, "not_configured"
    text = str(value).strip()
    kind = str(value_type).casefold()
    if kind in {"number", "numeric", "amount", "currency"}:
        return bool(re.search(r"\d", text)), "numeric" if re.search(r"\d", text) else "expected_numeric"
    if kind in {"date", "invoice_date"}:
        valid = bool(re.search(r"\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}", text) or
                     re.search(r"\d{4}", text))
        return valid, "date" if valid else "expected_date"
    if kind in {"invoice_number", "id", "code", "tax_id", "vat_id"}:
        valid = bool(re.search(r"[A-Za-z0-9\u0660-\u0669\u06F0-\u06F9]", text))
        return valid, "identifier" if valid else "expected_identifier"
    return True, "not_configured"


_TRANSLATION_CACHE = {}


def _translation_requested(key, spec, value):
    """Select descriptive Arabic fields without translating structured values."""
    if not value or not re.search(r"[\u0600-\u06FF]", str(value)):
        return False
    if spec.get("translate") is True:
        return True
    if spec.get("translate") is False:
        return False
    if spec.get("value_type"):
        return str(spec["value_type"]).casefold() in {"text", "description", "name"}
    key_norm = normalize(key)
    if key_norm in {
        normalize("إلى فاتورة"),
        normalize("فاتورة إلى"),
        normalize("Invoice To"),
    }:
        return True
    return any(token in key_norm for token in (
        "وصف", "اسم", "عميل", "مورد", "عنوان", "description", "name",
        "customer", "vendor", "supplier", "address", "project", "billto",
        "invoiceto",
    ))


def translate_arabic_to_english(value):
    """Translate one Arabic value through Azure AI Translator REST API."""
    text = str(value or "").strip()
    if not text or not re.search(r"[\u0600-\u06FF]", text):
        return text, "not_requested", None
    if text in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[text], "translated", None

    key = os.getenv("AZURE_TRANSLATOR_KEY")
    endpoint = os.getenv(
        "AZURE_TRANSLATOR_ENDPOINT",
        "https://api.cognitive.microsofttranslator.com",
    ).rstrip("/")
    region = os.getenv("AZURE_TRANSLATOR_REGION")
    if not key or not region:
        return None, "not_configured", (
            "Azure Translator requires AZURE_TRANSLATOR_KEY and "
            "AZURE_TRANSLATOR_REGION"
        )

    query = urllib.parse.urlencode({
        "api-version": "3.0",
        "from": "ar",
        "to": "en",
    })
    request = urllib.request.Request(
        f"{endpoint}/translate?{query}",
        data=json.dumps([{"Text": text}]).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": key,
            "Ocp-Apim-Subscription-Region": region,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        translated = payload[0]["translations"][0]["text"].strip()
    except Exception as exc:
        return None, "failed", f"Azure Translator request failed: {exc}"

    _TRANSLATION_CACHE[text] = translated
    return translated, "translated", None


def find_field(fields, key, target_page=None):
    matched_val = None
    if key in fields:
        matched_val = fields[key]
    else:
        key_norms = _arabic_variants(key)
        for k in fields:
            if key_norms & _arabic_variants(k):
                matched_val = fields[k]
                break
        if not matched_val:
            AZURE_ALIAS = {
                "invoicenumber":        "InvoiceId",
                "invoiceno":            "InvoiceId",
                "invoiceid":            "InvoiceId",
                "vendorname":           "VendorName",
                "suppliername":         "VendorName",
                "customername":         "CustomerName",
                "billto":               "CustomerName",
                "invoicedate":          "InvoiceDate",
                "date":                 "InvoiceDate",
                "invoicetotal":         "InvoiceTotal",
                "totalamount":          "InvoiceTotal",
                "total":                "InvoiceTotal",
                "grandtotal":           "InvoiceTotal",
                "subtotal":             "SubTotal",
                "ponumber":             "PurchaseOrder",
                "purchaseorder":        "PurchaseOrder",
                "ordernumber":          "PurchaseOrder",
                "duedate":              "DueDate",
                "paymentdue":           "DueDate",
                "remittanceto":         "RemittanceAddress",
                "shippingto":           "ShippingAddress",
                "billingaddress":       "BillingAddress",
                "vendoraddress":        "VendorAddress",
                "customeraddress":      "CustomerAddress",
                "tax":                  "TaxDetails",
                "vatrate":              "TaxRate",
                "taxrate":              "TaxRate",
            }
            for key_norm in key_norms:
                alias_target = AZURE_ALIAS.get(key_norm)
                if alias_target and alias_target in fields:
                    matched_val = fields[alias_target]
                    break
        if not matched_val:
            for k in fields:
                field_norms = _arabic_variants(k)
                if any(
                    key_norm in field_norm
                    or field_norm in key_norm
                    for key_norm in key_norms
                    for field_norm in field_norms
                ):
                    matched_val = fields[k]
                    break

    if matched_val and target_page is not None and isinstance(matched_val, dict):
        br = matched_val.get("bounding_regions", [])
        if br and isinstance(br, list) and isinstance(br[0], dict):
            field_page = br[0].get("page_number")
            if field_page is not None and field_page != target_page:
                return None

    return matched_val


def search_raw_kv(json_data, key, target_page=None):
    key_norms = _arabic_variants(key)
    kv_pairs = json_data.get("key_value_pairs", [])
    best_value, best_score = None, 0

    # Fields that must have numeric/currency values — reject non-numeric matches
    for pair in kv_pairs:
        if target_page is not None and pair.get("page_number") is not None:
            if pair.get("page_number") != target_page:
                continue

        kv_key = pair.get("key", {})
        kv_val = pair.get("value", {})
        label = kv_key.get("content", "") if kv_key else ""
        value = kv_val.get("content", "") if kv_val else ""
        confidence = pair.get("confidence", 0)

        if not label or not value:
            continue

        # For numeric fields, skip values that contain no digits
        if _is_numeric_label(key) and not re.search(r'\d', value):
            continue

        label_norms = _arabic_variants(label)
        score = 0
        if _labels_match(key_norms, label_norms):
            score = 100
        elif any(
            label_norm.startswith(key_norm)
            for key_norm in key_norms
            for label_norm in label_norms
        ):
            score = 80
        else:
            for key_norm in key_norms:
                for label_norm in label_norms:
                    if key_norm in label_norm:
                        length_penalty = len(label_norm) - len(key_norm)
                        score = max(score, 60 - length_penalty * 5)
                    elif label_norm in key_norm and len(label_norm) >= 4:
                        score = max(score, 50)
        if score > best_score and score >= 40:
            best_score = score
            best_value = {
                "value": restore_arabic_ocr(value),
                "content": restore_arabic_ocr(value),
                "confidence": confidence,
            }

    return best_value


def search_page_lines(json_data, key, target_page=None):
    """
    3-strategy fallback for SCANNED / IMAGE PDFs.
    Azure OCR puts text in result.pages[].lines → stored as page_lines by PdfToExcel.py.

    Strategy 1 : "LABEL : value"  on same line  (MMR form, JC pre-invoice)
    Strategy 2 : label on line N, value on line N+1  (table / form layout)
    Strategy 3 : inline regex patterns  (ForceStore INVOICE #, totals, etc.)
    """
    key_norms = _arabic_variants(key)
    key_norm = normalize(key)
    all_lines = json_data.get("page_lines", [])
    if target_page is not None:
        raw_lines = [
            l.get("content", "").strip()
            for l in all_lines
            if l.get("content", "").strip() and l.get("page_number", 1) == target_page
        ]
    else:
        raw_lines = [
            l.get("content", "").strip()
            for l in all_lines
            if l.get("content", "").strip()
        ]

    # Arabic invoice dates are commonly emitted as a reversed label with the
    # date on the left side of the colon.
    if any("تاريخ" in variant or "خيرات" in variant for variant in key_norms):
        for line in raw_lines:
            if ":" not in line:
                continue
            left, right = (part.strip() for part in line.split(":", 1))
            if (
                re.search(r"\d", left)
                and any(token in normalize(right) for token in ("تاريخ", "خيرات"))
            ):
                return {
                    "value": restore_arabic_ocr(left),
                    "content": restore_arabic_ocr(left),
                    "confidence": 0,
                }

    # ── Strategy 1 : same-line colon separator ──────────────────────────────
    for line in raw_lines:
        if ":" in line:
            parts = line.split(":", 1)
            label_norms = _arabic_variants(parts[0])
            value_part = parts[1].strip()
            if not value_part:
                continue
            if key_norms & label_norms:
                return {"value": restore_arabic_ocr(value_part), "content": restore_arabic_ocr(value_part), "confidence": 0}

            # Arabic RTL OCR can emit "value : label" even when the source
            # document is laid out as "label : value".
            right_label_norms = _arabic_variants(parts[1])
            left_value = parts[0].strip()
            if left_value and _line_label_match(key, parts[1]):
                return {"value": restore_arabic_ocr(left_value), "content": restore_arabic_ocr(left_value), "confidence": 0}

    for line in raw_lines:  # partial match
        if ":" in line:
            parts = line.split(":", 1)
            label_norms = _arabic_variants(parts[0])
            value_part = parts[1].strip()
            if not value_part:
                continue
            if _line_label_match(key, parts[0]):
                return {"value": restore_arabic_ocr(value_part), "content": restore_arabic_ocr(value_part), "confidence": 0}

            right_label_norms = _arabic_variants(parts[1])
            left_value = parts[0].strip()
            if left_value and _line_label_match(key, parts[1]):
                return {"value": restore_arabic_ocr(left_value), "content": restore_arabic_ocr(left_value), "confidence": 0}

    # ── Strategy 2 : label-then-value on consecutive lines ──────────────────
    def _looks_like_value(line):
        """
        Returns True if the line looks like an actual value, not a bare label.
        Guards against picking up the next label as the value (e.g. "Invoice No" → "Date").
        A value must either contain a digit, a currency symbol, or be a short word
        that isn't itself a common label keyword.
        """
        if not line:
            return False

        # Has digits → almost certainly a value
        if re.search(r'\d', line):
            return True
        # Has currency / special chars → value
        if re.search(r'[\$\£\€\₹\#]', line):
            return True
        # Short line (≤4 words) with no colon and not a known label keyword → treat as value
        words = line.split()
        label_keywords = {
            'invoice', 'date', 'number', 'no', 'total', 'subtotal', 'tax',
            'due', 'bill', 'to', 'from', 'name', 'address', 'phone', 'email',
            'description', 'quantity', 'amount', 'rate', 'discount', 'payment',
            'customer', 'vendor', 'supplier', 'period', 'status', 'type',
        }
        if len(words) <= 4 and not any(w.lower() in label_keywords for w in words):
            return True
        # Longer lines that don't start with a capital label-like word are values
        if len(words) > 4:
            return True
        return False

    numeric_label = _is_numeric_label(key)
    generic_total = key_norm == normalize("الإجمالي")

    for i in range(len(raw_lines) - 1):
        label_norms = _arabic_variants(raw_lines[i])
        next_line = raw_lines[i + 1]
        if (
            (
                _line_label_match(key, raw_lines[i])
                or (
                    generic_total
                    and any(key_norm in variant for variant in _arabic_variants(raw_lines[i]))
                )
            )
            and ":" not in raw_lines[i]
            and _looks_like_value(next_line)
            and (not numeric_label or re.search(r"\d", next_line))
        ):
            return {"value": restore_arabic_ocr(next_line), "content": restore_arabic_ocr(next_line), "confidence": 0}

    for i in range(len(raw_lines) - 1):  # partial match
        label_norms = _arabic_variants(raw_lines[i])
        next_line = raw_lines[i + 1]
        if (
            (
                _line_label_match(key, raw_lines[i])
                or (
                    generic_total
                    and any(key_norm in variant for variant in _arabic_variants(raw_lines[i]))
                )
            )
            and ":" not in raw_lines[i]
            and _looks_like_value(next_line)
            and (not numeric_label or re.search(r"\d", next_line))
        ):
            return {"value": restore_arabic_ocr(next_line), "content": restore_arabic_ocr(next_line), "confidence": 0}

    # ── Strategy 3 : inline regex patterns ──────────────────────────────────
    INLINE_PATTERNS = {
        # Invoice numbers — handles "#12345", "INVOICE # 12345", "Invoice No. 12345",
        # "12022/23" (Proforma — bare number after date on preceding line),
        # "Invoice No\n2023-0408" (SelfEmployed label-then-value handled above,
        # but also add regex fallback), "INVOICE # 2352/2023" (Rental)
        "invoicenumber": [
            r'invoice\s*#\s*([\w\-/]+)',
            r'invoice\s*no\.?\s*[:#]?\s*([\w\-/]+)',
            r'invoice\s*number\s*[:#]?\s*([\w\-/]+)',
            r'proforma\s*invoice\s*no\.?\s*[:#]?\s*([\w\-/]+)',
            r'proforma\s*no\.?\s*[:#]?\s*([\w\-/]+)',
            r'(?:^|\s)(INV[-\s][\w\-]+)',
            r'(?:INVOICE|Invoice)\s*:\s*([\w/\-]+)',
            r'#\s*(\d{4,}(?:/\d{2,4})?)',
            r'\b(\d{5}/\d{4})\b',                          # Proforma "12022/23" style
        ],
        "invoiceid": [
            r'invoice\s*#?\s*([\w\-/]+)',
        ],
        "ordernumber": [
            r'order\s*#?\s*([\w\-/]+)',
            r'order\s*number\s*[:#]?\s*([\w\-/]+)',
        ],
        "orderdate": [
            r'order\s*date\s*[:#]?\s*(\d[\d/\-\.\s,\w]+\d{4})',
            r'order\s*date\s*[:#]?\s*([\w]+\s+\d{1,2},?\s+\d{4})',
            r'order\s*date\s*[:#]?\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})',
        ],
        # Date — generic date patterns for invoices where date has no label
        "date": [
            r'date[:\s]+(\d[\d/\-\.]+(?:\s+\d[\d:APMapm\s]*)?)',
            r'(?:Due\s+By|Due\s+Date)[:\s]+([\w\s,]+\d{4})',
            r'(?:November|October|September|August|July|June|May|April|March|February|January)\s+\d{1,2},?\s+\d{4}',
            r'\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}',   # 10/10/2023 or 01-09-2023
        ],
        # Customer name — "TO:\n<Name>" pattern (Market Financial)
        "customername": [
            r'TO:\s*\n?\s*([A-Z][a-zA-Z\s]+?)(?:\n|$)',
            r'customer\s*name\s*[:#]?\s*(.+)',
            r'bill\s*to\s*[:#]?\s*(.+)',
        ],
        # PAN number — handles "PAN No: XXXXX", "PAN No. XXXXX", "PAN No.\nXXXXX"
        "pannumber": [
            r'PAN\s*No[\.:]?\s*:?\s*([A-Z]{5}\d{4}[A-Z])',
            r'PAN\s*No\b[^:]*\n\s*([A-Z]{5}\d{4}[A-Z])',  # label-then-value on next line
            r'\b([A-Z]{5}\d{4}[A-Z])\b',                   # raw PAN format anywhere
        ],
        # GSTIN — handles "GSTIN No.\nXXXXX", "GSTIN/UIN No.\nXXXXX", "GSTIN: XXXXX"
        "gstinnumber": [
            r'GSTIN\s*(?:/UIN)?\s*No\.?\s*[:#]?\s*\n?\s*([A-Z0-9]{15})',
            r'GSTIN\s*[:#]\s*([A-Z0-9]{15})',
            r'\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]{3})\b',  # raw GSTIN format
        ],
        # GST registration (Amazon style "GST Registration No: XXXXX")
        "gstregistration": [
            r'GST\s*Registration\s*No[\.:]?\s*:?\s*([A-Z0-9]{15})',
            r'gst\s*(?:registration)?\s*no\.?\s*[:#]?\s*([\w]+)',
        ],
        "gstin": [
            r'gstin\s*[:#]?\s*([\w]+)',
            r'\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]{3})\b',
        ],
        "grandtotal": [
            r'grand\s*total\s*\$?([\d,\.]+)',
            r'GRAND\s+TOTAL[:\s]*\$?([\d,\.]+)',
        ],
        "total": [
            r'\bTOTAL\b[:\s]*\$?([\d,\.]+)',
            r'\btotal\b[:\s]*\$?([\d,\.]+)',
        ],
        "subtotal": [
            r'sub\s*total\s*\$?([\d,\.]+)',
            r'subtotal\s*\$?([\d,\.]+)',
            r'Sub\s+Total[:\s]*\$?([\d,\.]+)',
        ],
        "tax": [
            r'\btax\b\s*\$?([\d,\.]+)',
            r'TAX[:\s]*\$?([\d,\.]+)',
        ],
        "discount": [
            r'discount\s*[-\$]?([\d,\.]+)',
        ],
        "shippinghandling": [
            r'shipping.*?handling\s*\$?([\d,\.]+)',
        ],
        # JC Pre-Invoice fields
        "jobcarddate": [
            r'Job\s*Card\s*Date\s*\n?\s*([\d\-:\s]+)',
            r'job\s*card\s*date[:\s]+([\d\-:\s]+)',
        ],
        "invoicedate": [
            r'Invoice\s*Date\s*\n?\s*([\d\.\-/]+)',
            r'invoice\s*date[:\s]+([\d\.\-/]+)',
            r'Job\s*Card\s*Date\s*\n?\s*([\d\-:\s]+)',  # JC Pre-Invoice uses this label
        ],
        "pannumber":           [r'PAN\s*No[\.:]?\s*:?\s*([A-Z]{5}\d{4}[A-Z])',
                                r'PAN\s*No\b[^:]*\n\s*([A-Z]{5}\d{4}[A-Z])',
                                r'\b([A-Z]{5}\d{4}[A-Z])\b'],
        "projectno":           [r'project\s*no\.?\s*[:#]?\s*(\w+)'],
        "mmrrequestno":        [r'mmr\s*request\s*no\.?\s*[:#]?\s*([\w\s\.]+)'],
        "agreementtitleno":    [r'agreement\s*title.*?[:#]\s*([\w\-]+)'],
        "nameofperson":        [r'name\s*of\s*person.*?[:#]\s*(.+?)\s*/'],
        "employeeid":          [r'employee\s*id.*?[:#]\s*(\w+)', r'name.*?/\s*(\d+)'],
        "category":            [r'\bcategory\b\s*[:#]\s*(\w+)'],
        "jobtitle":            [r'job\s*title\s*[:#]\s*(.+)'],
        "nationality":         [r'nationality\s*[:#]\s*(\w+)'],
        "baselocation":        [r'base\s*location\s*[:#]\s*(.+)'],
        "rateperhour":         [r'rate\s*(?:us)?\$?\s*[:#]?\s*([\d,\.]+)',
                                r'rate\s*per\s*hour\s*[:#]?\s*(.+)',
                                r'rate\s*us\$\s*([\d,\.]+)',
                                r'(?:hourly\s*)?rate\s*[:\-]?\s*\$?([\d,\.]+)'],
        "jobcardnumber":       [r'job\s*card\s*(?:number|no)\.?\s*([\w]+)'],
        "modelname":           [r'model\s*name\s+([\w\s]+)'],
        "registrationnumber":  [r'registration\s*(?:number|no)\.?\s*([\w]+)'],
        "suppliergstin":       [r'supplier\s*gstin\s*([\w]+)',
                                r'Supplier\s*GSTIN\s*\n?\s*([A-Z0-9]{15})'],
        "recipientgstin":      [r'recipient\s*gstin\s*([\w]+)'],
        "projectname":         [r'project\s*name\s*[:#]\s*(.+)',
                                r'Project\s*Name[:\s]+(.+)'],
        "projectmanager":      [r'project\s*manager\s*[:#]\s*(.+)',
                                r'Project\s*Manager[:\s]+(.+)'],
        "startdate":           [r'start\s*date\s*[:#]?\s*([\d/\-\.]+)',
                                r'[Ss]tart\s*[Dd]ate\s*[:#]?\s*([\d/\-\.]+)'],
        "enddate":             [r'end\s*date\s*[:#]?\s*([\d/\-\.]+)',
                                r'[Ee]nd\s*[Dd]ate\s*[:#]?\s*([\d/\-\.]+)'],
        "totalduration":       [r'total\s*duration\s*[:#]?\s*(\w+)',
                                r'[Dd]uration\s*[:#]?\s*(\w+)'],
        "paymentmethod":       [r'payment\s*method\s*[:#]?\s*(.+)'],
        "shippingmethod":      [r'shipping\s*method\s*[:#]?\s*(.+)'],
        "modelcode":           [r'model\s*code\s*\n?\s*([\w]+)', r'Model\s*Code\s*\n([\w]+)'],
    }

    patterns  = INLINE_PATTERNS.get(key_norm, [])
    full_text = "\n".join(raw_lines)

    for pat in patterns:
        m = re.search(pat, full_text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
            return {"value": restore_arabic_ocr(val), "content": restore_arabic_ocr(val), "confidence": 0}

    return None


def search_full_text(json_data, key, target_page=None):
    """
    Strategy 4: search the raw full_text field if present (some Azure responses include it).
    Also searches key_value_pairs content field as a last resort for design-embedded values
    like invoice numbers that appear as plain text without a label nearby.
    """
    key_norm = normalize(key)

    # For invoice numbers embedded in design (Freelance: "Invoice #100052-23", Rental: "INVOICE # 2352/2023")
    # These appear in the content of kv pairs or page text without a matching label
    DESIGN_PATTERNS = {
        "invoicenumber": [
            r'Invoice\s*#\s*([\w\-/]+)',
            r'INVOICE\s*#\s*([\w\-/]+)',
            r'Invoice\s*No\s*([\w\-/]+)',
            r'Inv\.\s*#?\s*([\w\-/]+)',
        ],
        "date": [
            r'(?:Nov(?:ember)?|Oct(?:ober)?|Sep(?:tember)?|Aug(?:ust)?|Jul(?:y)?|Jun(?:e)?|'
            r'May|Apr(?:il)?|Mar(?:ch)?|Feb(?:ruary)?|Jan(?:uary)?)\s+\d{1,2},?\s+\d{4}',
            r'\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}',
        ],
        "customername": [
            r'TO:\s*\n\s*([A-Z][a-zA-Z\s\.]+)',
        ],
    }

    patterns = DESIGN_PATTERNS.get(key_norm, [])
    if not patterns:
        return None

    # Search in all kv pair keys and values
    all_text_parts = []
    for pair in json_data.get("key_value_pairs", []):
        if target_page is not None and pair.get("page_number") is not None and pair.get("page_number") != target_page:
            continue
        k = pair.get("key", {})
        v = pair.get("value", {})
        if k:
            all_text_parts.append(k.get("content", ""))
        if v:
            all_text_parts.append(v.get("content", ""))

    full_text = "\n".join(all_text_parts)

    for pat in patterns:
        m = re.search(pat, full_text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
            return {"value": restore_arabic_ocr(val), "content": restore_arabic_ocr(val), "confidence": 0}

    return None


def extract_data(json_path, rules):
    with open(json_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)

    if not json_data:
        return None, None, None

    if isinstance(json_data, str):
        json_data = json.loads(json_data)

    data      = json_data[0] if isinstance(json_data, list) else json_data
    file_name = os.path.splitext(os.path.basename(json_path))[0]
    fields    = data.get("fields", {})

    invoice    = {"FileName": file_name}
    items      = []
    validation = []

    page_fields_map = parse_extraction_rules(rules)
    page_rule_specs = parse_extraction_rule_specs(rules)

    if page_fields_map:
        # CASE 1: Deterministic rules specified per page (Shaista requirement: fewer fields per page of interest)
        for page_no, rule_keys in sorted(page_fields_map.items()):
            for key in rule_keys:
                spec = next(
                    (item for item in page_rule_specs.get(page_no, [])
                     if item["name"] == key),
                    {"name": key, "labels": [key], "required": True},
                )
                # 1. Search scoped to the target page
                field = None
                matched_label = key
                for label in spec.get("labels", [key]):
                    field = find_field(fields, label, target_page=page_no)
                    if not field and re.search(r"[\u0600-\u06FF]", str(label)):
                        field = search_page_lines(data, label, target_page=page_no)
                    if not field:
                        field = search_raw_kv(data, label, target_page=page_no)
                    if not field and not re.search(r"[\u0600-\u06FF]", str(label)):
                        field = search_page_lines(data, label, target_page=page_no)
                    if field:
                        matched_label = label
                        break
                # For Arabic labels, page lines preserve the document reading
                # order and are more reliable than an ambiguous Azure KV pair.
                if (
                    not field
                    and normalize(key) in {
                        normalize("السجل التجاري"),
                        normalize("الرقم التجاري"),
                    }
                ):
                    field = search_raw_kv(data, "CR", target_page=page_no)
                if not field:
                    field = search_full_text(data, key, target_page=page_no)

                value      = None
                confidence = 0
                match_method = "not_found"
                if field:
                    match_method = "structured_or_rule_match"
                    if field.get("confidence", 0) == 0:
                        match_method = "page_line_or_regex_fallback"

                if field:
                    if "value" in field:
                        raw = field["value"]
                        if isinstance(raw, dict):
                            amount = raw.get("amount")
                            symbol = raw.get("symbol") or ""
                            code   = raw.get("code") or ""
                            currency = symbol or code or ""
                            if amount is not None:
                                value = f"{currency}{amount}".strip() if currency else str(amount)
                            else:
                                parts = [str(v) for v in raw.values() if v is not None]
                                value = ", ".join(parts) if parts else None
                        elif isinstance(raw, list):
                            value = "; ".join(str(i) for i in raw)
                        else:
                            value = raw
                    elif "content" in field:
                        value = field["content"]
                    confidence = field.get("confidence", 0)
                    if value is not None:
                        value = str(value).strip()
                        if value.lower() in ("none", ""):
                            value = None

                valid, validation_kind = _value_validation(
                    value, spec.get("value_type")
                )
                if value not in [None, "", "blank"] and not valid:
                    status, message = "Warning", f"Validation failed: {validation_kind}"
                elif value in [None, "", "blank"] and not spec.get("required", True):
                    status, message = "Warning", "Optional field missing"
                else:
                    status, message = _validation_status(value, confidence)
                    translated_value = None
                    translation_status = "not_requested"
                    translation_error = None
                    if _translation_requested(key, spec, value):
                        translated_value, translation_status, translation_error = (
                            translate_arabic_to_english(value)
                        )
                        if translation_status == "failed":
                            message = f"{message}; translation failed"
                        elif translation_status == "not_configured":
                            message = f"{message}; translation not configured"

                    invoice[key] = value
                    if translated_value is not None:
                        invoice[f"{key} (English)"] = translated_value
                    validation.append({
                    "FileName":        file_name,
                    "Page":            page_no,
                    "Field":           key,
                    "Extracted Value": value,
                    "Confidence":      round(float(confidence), 4) if confidence else 0,
                    "Status":          status,
                    "Message":         message,
                    "Match Label":     matched_label if field else None,
                    "Match Method":    match_method,
                    "Validation":      validation_kind,
                    "Translated Value": translated_value,
                    "Translation":     translation_status,
                    "Translation Error": translation_error,
                })

    else:
        # CASE 2: No deterministic rules provided (OCR performed on all pages, auto-detect all fields)
        extracted_keys = set()

        # 1. Structured fields detected by Azure
        for k, v in fields.items():
            val = None
            conf = 0.0
            p_no = 1
            if isinstance(v, dict):
                raw = v.get("value")
                if isinstance(raw, dict):
                    amount = raw.get("amount")
                    symbol = raw.get("symbol") or ""
                    code = raw.get("code") or ""
                    currency = symbol or code or ""
                    val = f"{currency}{amount}".strip() if currency else str(amount) if amount is not None else None
                elif isinstance(raw, list):
                    val = "; ".join(str(i) for i in raw)
                else:
                    val = raw
                if val is None and "content" in v:
                    val = v["content"]
                conf = v.get("confidence", 0.0)
                br = v.get("bounding_regions", [])
                if br and isinstance(br, list) and isinstance(br[0], dict):
                    p_no = br[0].get("page_number", 1)
            else:
                val = v

            if val is not None and str(val).strip() not in ("", "None"):
                clean_val = str(val).strip()
                invoice[k] = clean_val
                extracted_keys.add(k.lower())
                status, message = _validation_status(clean_val, conf)
                translated_value = None
                translation_status = "not_requested"
                translation_error = None
                if _translation_requested(k, {}, clean_val):
                    translated_value, translation_status, translation_error = (
                        translate_arabic_to_english(clean_val)
                    )
                    if translation_status == "failed":
                        message = f"{message}; translation failed"
                    elif translation_status == "not_configured":
                        message = f"{message}; translation not configured"
                if translated_value is not None:
                    invoice[f"{k} (English)"] = translated_value
                validation.append({
                    "FileName":        file_name,
                    "Page":           p_no,
                    "Field":           k,
                    "Extracted Value": clean_val,
                    "Confidence":      round(float(conf), 4),
                    "Status":          status,
                    "Message":          message + " (Auto-detected)",
                    "Translated Value": translated_value,
                    "Translation":      translation_status,
                    "Translation Error": translation_error,
                })

        # 2. Key-value pairs detected by Azure
        for pair in data.get("key_value_pairs", []):
            k_obj = pair.get("key", {})
            v_obj = pair.get("value", {})
            k_text = (k_obj.get("content", "") if k_obj else "").strip().rstrip(":")
            v_text = (v_obj.get("content", "") if v_obj else "").strip()
            conf = pair.get("confidence", 0.0)
            p_no = pair.get("page_number", 1)

            if k_text and v_text and k_text.lower() not in extracted_keys:
                invoice[k_text] = v_text
                extracted_keys.add(k_text.lower())
                status, message = _validation_status(v_text, conf)
                translated_value = None
                translation_status = "not_requested"
                translation_error = None
                if _translation_requested(k_text, {}, v_text):
                    translated_value, translation_status, translation_error = (
                        translate_arabic_to_english(v_text)
                    )
                    if translation_status == "failed":
                        message = f"{message}; translation failed"
                    elif translation_status == "not_configured":
                        message = f"{message}; translation not configured"
                if translated_value is not None:
                    invoice[f"{k_text} (English)"] = translated_value
                validation.append({
                    "FileName":        file_name,
                    "Page":           p_no,
                    "Field":           k_text,
                    "Extracted Value": v_text,
                    "Confidence":      round(float(conf), 4),
                    "Status":          status,
                    "Message":          message + " (Auto-detected KV)",
                    "Translated Value": translated_value,
                    "Translation":      translation_status,
                    "Translation Error": translation_error,
                })

    if "Items" in fields:
        for idx, item in enumerate(fields["Items"].get("value", [])):
            row = {"SNO": idx + 1, "FileName": file_name}
            for k in item.get("value", {}):
                raw = item["value"][k].get("value")
                row[k] = raw if not isinstance(raw, dict) else str(raw)
            items.append(row)

    return invoice, items, validation


def process(json_dir, output_dir, rules):
    outputs        = []
    all_validation = []

    for file in os.listdir(json_dir):
        if not file.endswith(".json"):
            continue
        json_path = os.path.join(json_dir, file)
        invoice, items, validation = extract_data(json_path, rules)
        if validation is None:
            continue
        all_validation.extend(validation)
        file_name   = os.path.splitext(file)[0]
        output_file = os.path.join(output_dir, f"{file_name}.xlsx")
        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            pd.DataFrame([invoice]).to_excel(writer, sheet_name="Summary",     index=False)
            if items:
                pd.DataFrame(items).to_excel(writer, sheet_name="Items",       index=False)
            pd.DataFrame(validation).to_excel(writer, sheet_name="Validation", index=False)
        outputs.append(output_file)

    return outputs, all_validation