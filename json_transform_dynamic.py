import json
import pandas as pd
import os


def normalize(s):
    return str(s).lower().replace("_", "").replace(" ", "").replace("-", "")


def find_field(fields, key):
    """
    FIXED: Strict exact match first, then normalized match.
    Previously, fuzzy/semantic matching caused 'invoice_number' to match
    'InvoiceId' (Azure's internal name), returning wrong data.
    Now we try exact field name matching using normalization only,
    and only fall back to Azure built-in field name mapping as a last resort.
    """

    # 1. Exact match (case-sensitive) - highest priority
    if key in fields:
        return fields[key]

    key_norm = normalize(key)

    # 2. Case-insensitive / underscore-insensitive exact match
    for k in fields:
        if key_norm == normalize(k):
            return fields[k]

    # 3. Azure built-in field alias map:
    # Azure Form Recognizer uses its own internal field names.
    # When user specifies a field name that matches the LABEL on the document
    # (e.g. "Invoice Number"), we must search the raw OCR key-value pairs,
    # not just the structured fields dict.
    # This map is a fallback ONLY when no direct match found.
    AZURE_ALIAS = {
        "invoicenumber":    "InvoiceId",
        "invoiceno":        "InvoiceId",
        "invoiceid":        "InvoiceId",
        "vendorname":       "VendorName",
        "suppliername":     "VendorName",
        "customername":     "CustomerName",
        "billto":           "CustomerName",
        "invoicedate":      "InvoiceDate",
        "date":             "InvoiceDate",
        "invoicetotal":     "InvoiceTotal",
        "totalamount":      "InvoiceTotal",
        "total":            "InvoiceTotal",
        "subtotal":         "SubTotal",
        "ponumber":         "PurchaseOrder",
        "purchaseorder":    "PurchaseOrder",
        "ordernumber":      "PurchaseOrder",
        "duedate":          "DueDate",
        "paymentdue":       "DueDate",
        "remittanceto":     "RemittanceAddress",
        "shippingto":       "ShippingAddress",
        "billingaddress":   "BillingAddress",
        "vendoraddress":    "VendorAddress",
        "customeraddress":  "CustomerAddress",
    }

    alias_target = AZURE_ALIAS.get(key_norm)
    if alias_target and alias_target in fields:
        return fields[alias_target]

    # 4. Substring match (partial) - lowest priority, only when nothing else matches
    for k in fields:
        k_norm = normalize(k)
        if key_norm in k_norm or k_norm in key_norm:
            return fields[k]

    return None


def search_raw_kv(json_data, key):
    key_norm = normalize(key)

    kv_pairs = json_data.get("key_value_pairs", [])

    best_value = None
    best_score = 0

    for pair in kv_pairs:
        kv_key = pair.get("key", {})
        kv_val = pair.get("value", {})

        label = kv_key.get("content", "") if kv_key else ""
        value = kv_val.get("content", "") if kv_val else ""
        confidence = pair.get("confidence", 0)

        if not label or not value:
            continue

        label_norm = normalize(label)

        score = 0
        if key_norm == label_norm:
            score = 100          # exact match — highest priority
        elif key_norm in label_norm and label_norm in key_norm:
            score = 90
        elif key_norm == label_norm[:len(key_norm)]:
            score = 80           # label starts with key
        elif key_norm in label_norm:
            # FIXED: penalise if label is longer than key
            # e.g. "invoicedetails" contains "invoicedate" but shouldn't win
            length_penalty = len(label_norm) - len(key_norm)
            score = max(0, 60 - length_penalty * 5)
        elif label_norm in key_norm:
            score = 50

        if score > best_score and score >= 40:
            best_score = score
            best_value = {
                "value": value,
                "content": value,
                "confidence": confidence
            }

    return best_value

def extract_data(json_path, rules):
    with open(json_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)

    if not json_data:
        return None, None, None

    if isinstance(json_data, str):
        json_data = json.loads(json_data)

    data = json_data[0] if isinstance(json_data, list) else json_data

    file_name = os.path.splitext(os.path.basename(json_path))[0]

    fields = data.get("fields", {})

    invoice = {"FileName": file_name}
    items = []
    validation = []

    pages_rules = rules.get("pages", {})
    rule_keys = []
    for _, keys in pages_rules.items():
        rule_keys.extend(keys)

    if not rule_keys:
        return invoice, items, validation

    for key in rule_keys:
        field = find_field(fields, key)

        # FIXED: If not found in structured fields, search raw key-value pairs
        if not field:
            field = search_raw_kv(data, key)

        value = None
        confidence = 0

        if field:
            if "value" in field:
                raw_value = field.get("value")
                # Handle Azure's complex value objects (e.g. currency, address dicts)
                if isinstance(raw_value, dict):
                    amount = raw_value.get("amount")
                    symbol = raw_value.get("symbol", "")
                    code = raw_value.get("code", "")
                    if amount is not None:
                        currency = symbol or code or ""
                        value = f"{currency}{amount}" if currency else str(amount)
                    else:
                        value = ", ".join(str(v) for v in raw_value.values() if v is not None)
                elif isinstance(raw_value, list):
                    value = "; ".join(str(i) for i in raw_value)
                else:
                    value = raw_value
            elif "content" in field:
                value = field.get("content")

            confidence = field.get("confidence", 0)

        # Determine status
        if value in [None, "", "blank"]:
            status = "Fail"
            message = "Required field missing"
        elif isinstance(confidence, (int, float)) and confidence < 0.7:
            status = "Warning"
            message = "Low confidence"
        else:
            status = "Pass"
            message = "Valid"

        invoice[key] = value

        validation.append({
            "FileName": file_name,
            "Page": 1,
            "Field": key,              # FIXED: Always use the user-specified field name
            "Extracted Value": value,
            "Confidence": round(float(confidence), 4) if confidence else 0,
            "Status": status,
            "Message": message
        })

    # Extract line items if present
    if "Items" in fields:
        for idx, item in enumerate(fields["Items"].get("value", [])):
            row = {"SNO": idx + 1, "FileName": file_name}
            for k in item.get("value", {}):
                raw = item["value"][k].get("value")
                row[k] = raw if not isinstance(raw, dict) else str(raw)
            items.append(row)

    return invoice, items, validation


def process(json_dir, output_dir, rules):
    outputs = []
    all_validation = []

    for file in os.listdir(json_dir):
        if not file.endswith(".json"):
            continue

        json_path = os.path.join(json_dir, file)

        invoice, items, validation = extract_data(json_path, rules)

        if validation is None:
            continue

        all_validation.extend(validation)

        file_name = os.path.splitext(file)[0]
        output_file = os.path.join(output_dir, f"{file_name}.xlsx")

        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:

            df_invoice = pd.DataFrame([invoice])
            df_invoice.to_excel(writer, sheet_name="Summary", index=False)

            if items:
                df_items = pd.DataFrame(items)
                df_items.to_excel(writer, sheet_name="Items", index=False)

            df_validation = pd.DataFrame(validation)
            df_validation.to_excel(writer, sheet_name="Validation", index=False)

        outputs.append(output_file)

    return outputs, all_validation