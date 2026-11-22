CREATE TABLE IF NOT EXISTS videos (
    id                  SERIAL PRIMARY KEY,
    company_name        TEXT NOT NULL UNIQUE,
    file_path           TEXT NOT NULL,
    video_url           TEXT NOT NULL,
    sidecar_path        TEXT,
    processed_file_path TEXT,
    processed_video_url TEXT,
    processing_status   TEXT NOT NULL DEFAULT 'none',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE videos ADD COLUMN IF NOT EXISTS sidecar_path TEXT;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS processed_file_path TEXT;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS processed_video_url TEXT;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS processing_status TEXT NOT NULL DEFAULT 'none';

CREATE TABLE IF NOT EXISTS jobs (
    id                  SERIAL PRIMARY KEY,
    video_id            INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    company_name        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    input_path          TEXT NOT NULL,
    sidecar_path        TEXT,
    output_path         TEXT,
    processed_video_url TEXT,
    base_url            TEXT,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS companies (
    id                  SERIAL PRIMARY KEY,
    company_name        TEXT NOT NULL UNIQUE,
    language            TEXT,
    country             TEXT,
    postcode            TEXT,
    city                TEXT,
    street              TEXT,
    house_no            TEXT,
    web                 TEXT,
    status              INTEGER,
    impressum_url       TEXT,
    legal_name          TEXT,
    postal_address      TEXT,
    handelsregister     TEXT,
    vat_number          TEXT,
    phone               TEXT,
    email               TEXT,
    responsible_person  TEXT,
    raw_json            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
