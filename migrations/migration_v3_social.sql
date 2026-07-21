-- Migration v3: Add social verification & citizen reports table
ALTER TABLE fires ADD COLUMN IF NOT EXISTS social_reports_count INTEGER DEFAULT 0;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS social_sources TEXT;

CREATE TABLE IF NOT EXISTS citizen_reports (
    id BIGSERIAL PRIMARY KEY,
    fire_id BIGINT REFERENCES fires(id) ON DELETE SET NULL,
    latitude DOUBLE PRECISION NOT NULL CONSTRAINT chk_cit_latitude CHECK (latitude >= -90.0 AND latitude <= 90.0),
    longitude DOUBLE PRECISION NOT NULL CONSTRAINT chk_cit_longitude CHECK (longitude >= -180.0 AND longitude <= 180.0),
    reporter_type VARCHAR(50) DEFAULT 'Citizen',
    reporter_name VARCHAR(100),
    wilaya VARCHAR(100),
    severity VARCHAR(50),
    description TEXT,
    photo_b64 TEXT NOT NULL,
    verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_citizen_reports_coords ON citizen_reports(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_citizen_reports_created ON citizen_reports(created_at DESC);
