-- Add improved classification metrics to run_profiles
-- Based on sports science research for accurate session type detection

-- Average stop duration (key discriminator: traffic <15s vs interval rest 30-90s)
ALTER TABLE run_profiles ADD COLUMN IF NOT EXISTS avg_stop_duration_secs REAL;

-- Stop regularity: CV of stop durations (low = structured intervals, high = random/traffic)
ALTER TABLE run_profiles ADD COLUMN IF NOT EXISTS stop_regularity REAL;

-- Mean HR at stop onset as %HRmax (high = interval rest, low = traffic stop)
ALTER TABLE run_profiles ADD COLUMN IF NOT EXISTS hr_at_stop_onset_pct REAL;

-- Pace CV excluding stopped time (more accurate than raw pace_cv for urban runners)
ALTER TABLE run_profiles ADD COLUMN IF NOT EXISTS pace_cv_moving REAL;

-- Intensity Factor: avg_pace / threshold_pace (most validated single intensity metric)
-- IF < 0.75 = recovery, 0.75-0.85 = easy, 0.85-0.95 = tempo, 0.95-1.05 = threshold
ALTER TABLE run_profiles ADD COLUMN IF NOT EXISTS intensity_factor REAL;

-- Number of high-intensity bouts (segments where HR > 88% HRmax for > 30s)
ALTER TABLE run_profiles ADD COLUMN IF NOT EXISTS high_intensity_bouts INTEGER DEFAULT 0;

-- Classified run type and confidence
ALTER TABLE run_profiles ADD COLUMN IF NOT EXISTS run_type TEXT;
ALTER TABLE run_profiles ADD COLUMN IF NOT EXISTS run_type_confidence REAL;
