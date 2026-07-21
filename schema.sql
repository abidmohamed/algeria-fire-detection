-- Enable PostGIS extension for spatial queries (already available on Supabase)
CREATE EXTENSION IF NOT EXISTS postgis;

-- Create fires table
CREATE TABLE IF NOT EXISTS fires (
    id BIGSERIAL PRIMARY KEY,
    latitude DOUBLE PRECISION NOT NULL CONSTRAINT chk_latitude CHECK (latitude >= -90.0 AND latitude <= 90.0),
    longitude DOUBLE PRECISION NOT NULL CONSTRAINT chk_longitude CHECK (longitude >= -180.0 AND longitude <= 180.0),
    geom GEOGRAPHY(Point, 4326),  -- Spatial geography column
    frp DOUBLE PRECISION NOT NULL,  -- Fire Radiative Power (MW)
    confidence INTEGER NOT NULL,  -- Confidence percentage
    acquisition_time TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(50) DEFAULT 'PENDING' CONSTRAINT chk_status CHECK (status IN ('PENDING', 'CONFIRMED', 'FALSE_POSITIVE')),
    source VARCHAR(20) DEFAULT 'VIIRS_SNPP_NRT' NOT NULL,
    temp DOUBLE PRECISION,  -- Local temperature at detection time
    humidity DOUBLE PRECISION,  -- Local relative humidity at detection time
    wind_speed DOUBLE PRECISION,  -- Wind speed (km/h)
    wind_direction DOUBLE PRECISION,  -- Wind direction (degrees)
    risk_score DOUBLE PRECISION,  -- Custom Calculated Fire Weather Risk Index (0-100)
    product_id VARCHAR(255),  -- Sentinel-2/Landsat product ID
    quicklook_url VARCHAR(1024),  -- Link to Sentinel-2/Landsat quicklook preview
    telegram_message_id VARCHAR(50),  -- ID of sent alert for thread updates
    brightness DOUBLE PRECISION,  -- Brightness temperature Band I4 (3.74µm)
    bright_t31 DOUBLE PRECISION,  -- Brightness temperature Band I5 (11.45µm)
    daynight CHAR(1),  -- D=Day, N=Night detection
    scan_pixel DOUBLE PRECISION,  -- Pixel size across-track (km)
    cluster_id INTEGER,  -- DBSCAN cluster ID (-1 = isolated)
    cluster_size INTEGER,  -- Number of hotspots in cluster
    composite_score DOUBLE PRECISION,  -- Weighted composite fire confidence (0-100)
    days_since_rain DOUBLE PRECISION,  -- Days since last precipitation
    last_notified_at TIMESTAMP WITH TIME ZONE,  -- Last Telegram notification sent time
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_latitude_longitude_acquisition UNIQUE (latitude, longitude, acquisition_time)
);

-- Index for spatial queries
CREATE INDEX IF NOT EXISTS idx_fires_geom ON fires USING gist(geom);

-- Index for dashboard sorting
CREATE INDEX IF NOT EXISTS idx_fires_acquisition_time ON fires(acquisition_time DESC);

-- Index for status and detection time queries
CREATE INDEX IF NOT EXISTS idx_fires_status_time ON fires(status, acquisition_time DESC);

-- Trigger to automatically populate the geom column based on latitude/longitude
CREATE OR REPLACE FUNCTION update_fires_geom()
RETURNS TRIGGER AS $$
BEGIN
    NEW.geom := ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326)::geography;
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_fires_geom ON fires;
CREATE TRIGGER trg_update_fires_geom
BEFORE INSERT OR UPDATE ON fires
FOR EACH ROW EXECUTE FUNCTION update_fires_geom();
