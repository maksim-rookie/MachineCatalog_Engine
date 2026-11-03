-- PostgreSQL 15+. Reviewable initial schema; not applied automatically.
BEGIN;
CREATE SCHEMA IF NOT EXISTS machinecatalog;
SET LOCAL search_path TO machinecatalog, public;

CREATE TABLE manufacturers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL CHECK (btrim(name) <> ''),
    canonical_key text NOT NULL UNIQUE CHECK (btrim(canonical_key) <> '')
);
CREATE TABLE documents (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sha256 text NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    media_type text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
-- Identical bytes may arrive under different names or from different URLs.
CREATE TABLE document_sources (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL REFERENCES documents(id),
    source_uri text NOT NULL,
    original_name text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, source_uri)
);
CREATE TABLE processing_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL REFERENCES documents(id),
    engine_version text NOT NULL,
    config_sha256 text NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    ocr_config_sha256 text NOT NULL CHECK (ocr_config_sha256 ~ '^[0-9a-f]{64}$'),
    contract_version integer NOT NULL CHECK (contract_version > 0),
    state text NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued','running','succeeded','needs_review','failed')),
    started_at timestamptz,
    finished_at timestamptz,
    metrics jsonb NOT NULL DEFAULT '{}',
    UNIQUE (document_id, engine_version, config_sha256, ocr_config_sha256)
);
CREATE TABLE equipment (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    manufacturer_id bigint REFERENCES manufacturers(id),
    model_name text NOT NULL CHECK (btrim(model_name) <> ''),
    series text,
    machine_class text,
    machine_type text,
    model_year integer CHECK (model_year BETWEEN 1800 AND 9999),
    identity_status text NOT NULL DEFAULT 'unresolved'
        CHECK (identity_status IN ('unresolved','proposed','confirmed')),
    canonical_key text,
    CHECK (identity_status <> 'confirmed' OR
        (manufacturer_id IS NOT NULL AND canonical_key IS NOT NULL))
);
-- Unresolved identities deliberately have no name-based global uniqueness.
CREATE UNIQUE INDEX equipment_confirmed_identity
    ON equipment (manufacturer_id, canonical_key)
    WHERE identity_status = 'confirmed';
CREATE TABLE equipment_variants (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    equipment_id bigint NOT NULL REFERENCES equipment(id),
    configuration_key text NOT NULL,
    label text NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}',
    UNIQUE (equipment_id, configuration_key)
);
CREATE TABLE equipment_document_links (
    variant_id bigint NOT NULL REFERENCES equipment_variants(id),
    document_id bigint NOT NULL REFERENCES documents(id),
    source_model_name text NOT NULL,
    PRIMARY KEY (variant_id, document_id, source_model_name)
);
CREATE TABLE units (
    code text PRIMARY KEY,
    dimension text NOT NULL,
    definition jsonb NOT NULL
);
CREATE TABLE parameter_definitions (
    id text NOT NULL,
    schema_version text NOT NULL,
    category text NOT NULL,
    value_type text NOT NULL CHECK (value_type IN ('number','range','enum','text')),
    unit_code text REFERENCES units(code),
    descriptor jsonb NOT NULL,
    PRIMARY KEY (id, schema_version)
);
CREATE TABLE measurements (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES processing_runs(id),
    variant_id bigint NOT NULL REFERENCES equipment_variants(id),
    parameter_id text NOT NULL,
    schema_version text NOT NULL,
    source_fact_key text NOT NULL,
    value_kind text NOT NULL CHECK (value_kind IN ('number','range','enum','text','missing')),
    numeric_value numeric,
    range_min numeric,
    range_max numeric,
    text_value text,
    unit_code text REFERENCES units(code),
    axis text CHECK (axis IN ('X','Y','Z','A','B','C','W','U','V')),
    conditions jsonb NOT NULL DEFAULT '{}',
    extraction_status text NOT NULL
        CHECK (extraction_status IN ('extracted','absent','ambiguous','failed','conflict')),
    verification_status text NOT NULL DEFAULT 'unverified'
        CHECK (verification_status IN ('unverified','confirmed','rejected')),
    raw_record jsonb NOT NULL,
    FOREIGN KEY (parameter_id, schema_version)
        REFERENCES parameter_definitions(id, schema_version),
    UNIQUE (run_id, source_fact_key),
    CHECK (range_min IS NULL OR range_max IS NULL OR range_min <= range_max),
    CHECK (
        (value_kind = 'number' AND numeric_value IS NOT NULL AND
            range_min IS NULL AND range_max IS NULL AND text_value IS NULL) OR
        (value_kind = 'range' AND numeric_value IS NULL AND
            range_min IS NOT NULL AND range_max IS NOT NULL AND text_value IS NULL) OR
        (value_kind IN ('enum','text') AND text_value IS NOT NULL AND
            numeric_value IS NULL AND range_min IS NULL AND range_max IS NULL) OR
        (value_kind = 'missing' AND numeric_value IS NULL AND
            range_min IS NULL AND range_max IS NULL AND text_value IS NULL)
    ),
    CHECK (extraction_status <> 'absent' OR value_kind = 'missing')
);
CREATE TABLE measurement_evidence (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    measurement_id bigint NOT NULL REFERENCES measurements(id),
    source_id bigint NOT NULL REFERENCES document_sources(id),
    page_number integer CHECK (page_number > 0),
    bbox jsonb,
    source_label text,
    value_raw text,
    unit_raw text,
    method text NOT NULL,
    CHECK (bbox IS NULL OR (jsonb_typeof(bbox) = 'array' AND jsonb_array_length(bbox) = 4))
);
CREATE TABLE measurement_issues (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    measurement_id bigint NOT NULL REFERENCES measurements(id),
    reason text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('info','warning','error')),
    suggested_fix text,
    confidence numeric CHECK (confidence BETWEEN 0 AND 1),
    details jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX measurements_numeric_lookup
    ON measurements (parameter_id, unit_code, numeric_value, variant_id)
    WHERE value_kind = 'number' AND extraction_status = 'extracted'
        AND verification_status = 'confirmed';
CREATE INDEX measurements_by_variant ON measurements (variant_id, parameter_id);
CREATE INDEX issues_by_measurement ON measurement_issues (measurement_id);
CREATE INDEX runs_by_document ON processing_runs (document_id, state);
COMMIT;
