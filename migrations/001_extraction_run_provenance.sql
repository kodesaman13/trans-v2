-- Optional, additive migration. Review with the DBA before execution.
-- Existing score tables remain unchanged and continue to serve V1 consumers.

CREATE TABLE IF NOT EXISTS extraction_run (
    run_id BIGINT NOT NULL AUTO_INCREMENT,
    document_id INT NOT NULL,
    filename VARCHAR(255) NOT NULL,
    pages_json JSON NOT NULL,
    rules_json JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id),
    KEY ix_extraction_run_document (document_id),
    CONSTRAINT fk_extraction_run_document
        FOREIGN KEY (document_id) REFERENCES document_type_master(document_id)
);

CREATE TABLE IF NOT EXISTS extraction_field_provenance (
    run_id BIGINT NOT NULL,
    page INT NOT NULL,
    field VARCHAR(255) NOT NULL,
    match_label VARCHAR(255) NULL,
    match_method VARCHAR(80) NOT NULL,
    validation VARCHAR(80) NOT NULL,
    azure_confidence DECIMAL(12,4) NULL,
    translation_status VARCHAR(40) NOT NULL DEFAULT 'not_requested',
    PRIMARY KEY (run_id, page, field),
    CONSTRAINT fk_provenance_run
        FOREIGN KEY (run_id) REFERENCES extraction_run(run_id)
);
