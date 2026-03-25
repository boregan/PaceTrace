-- Enrichment columns: weather, air quality, shoe, daylight context
-- Run manually in Supabase SQL editor

ALTER TABLE activities
  ADD COLUMN IF NOT EXISTS start_lat          FLOAT,
  ADD COLUMN IF NOT EXISTS start_lng          FLOAT,
  ADD COLUMN IF NOT EXISTS weather_temp_c     FLOAT,
  ADD COLUMN IF NOT EXISTS weather_feels_c    FLOAT,
  ADD COLUMN IF NOT EXISTS weather_humidity   INTEGER,
  ADD COLUMN IF NOT EXISTS weather_wind_kmh   FLOAT,
  ADD COLUMN IF NOT EXISTS weather_precip_mm  FLOAT,
  ADD COLUMN IF NOT EXISTS weather_desc       TEXT,
  ADD COLUMN IF NOT EXISTS aqi                INTEGER,
  ADD COLUMN IF NOT EXISTS aqi_desc           TEXT,
  ADD COLUMN IF NOT EXISTS shoe_name          TEXT,
  ADD COLUMN IF NOT EXISTS shoe_km_at_run     FLOAT,
  ADD COLUMN IF NOT EXISTS daylight_phase     TEXT;
