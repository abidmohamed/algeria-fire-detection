-- Migration script to upgrade database schema for production readiness
-- Date: 2026-07-18
-- Target: Supabase / PostgreSQL database

BEGIN;

-- 1. Upgrade ID column data type from standard 32-bit integer to 64-bit BIGINT
ALTER TABLE fires ALTER COLUMN id TYPE BIGINT;

-- 2. Add source tracking column for MODIS vs VIIRS satellite records
ALTER TABLE fires ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'VIIRS_SNPP_NRT' NOT NULL;

-- 3. Enforce coordinate and timestamp uniqueness to prevent duplicate records
-- In case duplicates already exist, delete them keeping the earliest occurrence
DELETE FROM fires a USING fires b 
WHERE a.id > b.id 
  AND a.latitude = b.latitude 
  AND a.longitude = b.longitude 
  AND a.acquisition_time = b.acquisition_time;

ALTER TABLE fires ADD CONSTRAINT uq_lat_lon_acq UNIQUE (latitude, longitude, acquisition_time);

-- 4. Restrict status column to only allow defined system states
ALTER TABLE fires ADD CONSTRAINT chk_status CHECK (status IN ('PENDING', 'CONFIRMED', 'FALSE_POSITIVE'));

COMMIT;
