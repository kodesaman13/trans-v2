import json
from db import get_connection
from datetime import datetime


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _clean_extracted_value(raw):
    """Convert any extracted value to a clean plain string for DB storage."""
    if raw is None or str(raw).strip() in ("None", ""):
        return ""
    s = str(raw).strip()
    # Unwrap Azure currency dicts: {'amount': 108450.0, 'symbol': None, 'code': 'USD'}
    if s.startswith("{") and "amount" in s:
        try:
            import ast
            d = ast.literal_eval(s)
            amount   = d.get("amount")
            symbol   = d.get("symbol") or ""
            code     = d.get("code")   or ""
            currency = symbol or code or ""
            if amount is not None:
                return f"{currency}{amount}".strip() if currency else str(amount)
        except Exception:
            pass
    return s


# ─────────────────────────────────────────────
#  document_type_master
#  Columns: document_id(PK AI), document_name, document_category,
#           extraction_rules_json(JSON), extracted_data_location(VARCHAR 500),
#           is_active, created_by, created_at(DATETIME),
#           updated_by, updatedd_at(DATETIME)   ← double-d typo in DB
# ─────────────────────────────────────────────

def get_active_documents():
    conn   = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM document_type_master WHERE is_active='Y'")
    data   = cursor.fetchall()
    cursor.close()
    conn.close()
    return data


def get_document_by_id(document_id):
    conn   = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM document_type_master WHERE document_id=%s",
        (document_id,)
    )
    data = cursor.fetchone()
    cursor.close()
    conn.close()
    return data


def create_document(document_name, category, rules_json):
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO document_type_master
            (document_name, document_category, extraction_rules_json,
             is_active, created_by, created_at)
        VALUES (%s, %s, %s, 'Y', 'admin', NOW())
    """, (document_name, category, json.dumps(rules_json)))
    conn.commit()
    document_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return document_id


def update_document(document_id, payload):
    conn   = get_connection()
    cursor = conn.cursor()

    allowed = {
        "document_name", "document_category", "extraction_rules_json",
        "is_active", "updated_by", "extracted_data_location"
    }
    fields, values = [], []
    for key, value in payload.items():
        if key in allowed:
            fields.append(f"{key}=%s")
            values.append(json.dumps(value) if key == "extraction_rules_json" else value)

    fields.append("updatedd_at=NOW()")      # double-d matches actual DB column
    values.append(document_id)

    cursor.execute(
        f"UPDATE document_type_master SET {', '.join(fields)} WHERE document_id=%s",
        tuple(values)
    )
    conn.commit()
    cursor.close()
    conn.close()


def delete_document(document_id):
    conn   = get_connection()
    cursor = conn.cursor()
    # Delete child rows first
    for tbl in ("document_scores_dbt", "document_scores", "master_csv_config"):
        try:
            cursor.execute(f"DELETE FROM {tbl} WHERE document_id=%s", (document_id,))
        except Exception:
            pass
    cursor.execute(
        "DELETE FROM document_type_master WHERE document_id=%s", (document_id,)
    )
    conn.commit()
    cursor.close()
    conn.close()


def update_extracted_data_location(document_id, extraction_info):
    """
    Store the latest extraction metadata in extracted_data_location.
    Column is VARCHAR(500) — store only the most recent run as compact JSON
    to guarantee it always fits within 500 chars.
    """
    conn   = get_connection()
    cursor = conn.cursor()

    # Build compact summary — only essential fields, no long paths
    compact = {
        "id":        extraction_info.get("extraction_id", ""),
        "ts":        extraction_info.get("timestamp", "")[:19],   # trim ms
        "file":      extraction_info.get("file_name", "")[:50],   # cap filename
        "pages":     extraction_info.get("pages_processed", "all"),
        "total":     extraction_info.get("total_fields", 0),
        "pass":      extraction_info.get("passed", 0),
        "warn":      extraction_info.get("warnings", 0),
        "fail":      extraction_info.get("failed", 0),
    }

    import json as _json
    value = _json.dumps(compact)

    # Safety net — hard truncate if somehow still >490 chars
    if len(value) > 490:
        value = value[:490]

    cursor.execute("""
        UPDATE document_type_master
        SET extracted_data_location=%s, updatedd_at=NOW()
        WHERE document_id=%s
    """, (value, document_id))

    conn.commit()
    cursor.close()
    conn.close()


# ─────────────────────────────────────────────
#  document_scores_dbt
#  Columns: document_id, page, field, extracted_values(TEXT),
#           confidence(VARCHAR 100), status(VARCHAR 100),
#           message(TEXT), filename(VARCHAR 255)
#  NOTE: no id / no auto-increment PK in this table
# ─────────────────────────────────────────────

def save_document_scores(document_id, validation_data):
    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM document_scores_dbt WHERE document_id=%s", (document_id,)
    )

    for row in validation_data:
        field_name = row.get("Field", "").rstrip(":")
        confidence = row.get("Confidence", "")
        if isinstance(confidence, (int, float)):
            confidence = str(round(float(confidence), 4))

        cursor.execute("""
            INSERT INTO document_scores_dbt
                (document_id, page, field, extracted_values,
                 confidence, status, message, filename)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            document_id,
            int(row.get("Page") or 1),
            field_name,
            _clean_extracted_value(row.get("Extracted Value")),
            confidence,
            row.get("Status", ""),
            row.get("Message", ""),
            row.get("FileName", "")
        ))

    conn.commit()
    cursor.close()
    conn.close()
    print(f"✅ Saved {len(validation_data)} rows → document_scores_dbt (doc {document_id})")


def get_document_scores(document_id):
    conn   = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT * FROM document_scores_dbt
        WHERE document_id=%s
        ORDER BY page, field
    """, (document_id,))
    data = cursor.fetchall()
    cursor.close()
    conn.close()
    return data


# ─────────────────────────────────────────────
#  document_scores
#  Columns: document_id, page, field, extracted_value(TEXT),
#           confidence(DECIMAL 12,2), status, message,
#           file_name, modified_values(TEXT),
#           created_timestamp(DATETIME), updated_timestamp(DATETIME),
#           approved_by(VARCHAR 255)
# ─────────────────────────────────────────────

def save_to_document_scores_table(document_id, validation_data, approved_by=None):
    """Save to document_scores table (has approval workflow columns)."""
    conn   = get_connection()
    cursor = conn.cursor()

    # Verify the table exists
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'document_scores'
    """)
    if cursor.fetchone()[0] == 0:
        cursor.close()
        conn.close()
        print("⚠️  document_scores table not found — skipping")
        return

    cursor.execute(
        "DELETE FROM document_scores WHERE document_id=%s", (document_id,)
    )

    for row in validation_data:
        field_name = row.get("Field", "").rstrip(":")
        try:
            conf = round(float(row.get("Confidence", 0) or 0), 2)
        except (ValueError, TypeError):
            conf = 0.00

        cursor.execute("""
            INSERT INTO document_scores
                (document_id, page, field, extracted_value,
                 confidence, status, message, file_name,
                 modified_values, created_timestamp, updated_timestamp, approved_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s)
        """, (
            document_id,
            int(row.get("Page") or 1),
            field_name,
            _clean_extracted_value(row.get("Extracted Value")),
            conf,
            row.get("Status", ""),
            row.get("Message", ""),
            row.get("FileName", ""),
            None,           # modified_values — filled when user corrects a value
            approved_by
        ))

    conn.commit()
    cursor.close()
    conn.close()
    print(f"✅ Saved {len(validation_data)} rows → document_scores (doc {document_id})")


def get_document_scores_with_approval(document_id, approved_only=False):
    conn   = get_connection()
    cursor = conn.cursor(dictionary=True)
    query  = "SELECT * FROM document_scores WHERE document_id=%s"
    params = [document_id]
    if approved_only:
        query += " AND approved_by IS NOT NULL"
    query += " ORDER BY page, field"
    cursor.execute(query, params)
    data = cursor.fetchall()
    cursor.close()
    conn.close()
    return data


def approve_extraction_value(score_id, approved_value, approved_by):
    """Correct / approve an extracted value in document_scores."""
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE document_scores
        SET modified_values=%s, approved_by=%s, updated_timestamp=NOW()
        WHERE id=%s
    """, (approved_value, approved_by, score_id))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "approved", "score_id": score_id}


# ─────────────────────────────────────────────
#  master_csv_config
#  Columns: id(PK AI), document_id, csv_filename,
#           primary_key(VARCHAR 255), columns_json(TEXT),
#           csv_data_json(TEXT), uploaded_at(DATETIME)
#  UNIQUE KEY uq_doc (document_id)
# ─────────────────────────────────────────────

def save_to_master_csv_config(document_id, csv_filename, columns_json,
                               csv_data_json, primary_key=None):
    """
    Save extracted data as JSON to master_csv_config.
    Always DELETE first (unique constraint on document_id), then INSERT.
    """
    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM master_csv_config WHERE document_id=%s", (document_id,)
    )
    cursor.execute("""
        INSERT INTO master_csv_config
            (document_id, csv_filename, primary_key,
             columns_json, csv_data_json, uploaded_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
    """, (document_id, csv_filename, primary_key, columns_json, csv_data_json))

    conn.commit()
    cursor.close()
    conn.close()
    print(f"✅ Saved → master_csv_config for {csv_filename} (doc {document_id})")


def get_csv_config_by_document(document_id):
    conn   = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT * FROM master_csv_config
        WHERE document_id=%s ORDER BY uploaded_at DESC
    """, (document_id,))
    data = cursor.fetchall()
    for row in data:
        for col in ("columns_json", "csv_data_json"):
            if row.get(col):
                try:
                    row[col] = json.loads(row[col])
                except Exception:
                    pass
    cursor.close()
    conn.close()
    return data


# ─────────────────────────────────────────────
#  Cross-table helpers
# ─────────────────────────────────────────────

def get_extraction_summary(document_id):
    conn   = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            COUNT(*)  AS total_fields,
            SUM(CASE WHEN status='Pass'    THEN 1 ELSE 0 END) AS passed,
            SUM(CASE WHEN status='Warning' THEN 1 ELSE 0 END) AS warnings,
            SUM(CASE WHEN status='Fail'    THEN 1 ELSE 0 END) AS failed,
            AVG(CAST(confidence AS DECIMAL(10,4)))             AS avg_confidence
        FROM document_scores_dbt
        WHERE document_id=%s
    """, (document_id,))
    scores_summary = cursor.fetchone()

    cursor.execute("""
        SELECT COUNT(*) AS csv_count,
               GROUP_CONCAT(DISTINCT csv_filename) AS csv_files
        FROM master_csv_config WHERE document_id=%s
    """, (document_id,))
    csv_summary = cursor.fetchone()

    cursor.close()
    conn.close()
    return {
        "document_id":      document_id,
        "extraction_stats": scores_summary,
        "csv_files":        csv_summary
    }
