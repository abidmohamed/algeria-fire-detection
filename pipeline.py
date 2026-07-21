# Inject OS-native certificate store before any HTTPS connections
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import sys
import time
import html
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from math import radians, sin, cos, sqrt, atan2
from src.config import validate_config
from src.firms_client import FirmsClient
from src.spatial_filter import SpatialFilter
from src.weather_client import WeatherClient
from src.copernicus_client import CopernicusClient
from src.smoke_detector import SmokeDetector
from src.db_client import DbClient
from src.telegram_notifier import TelegramNotifier
from src.social_verifier import SocialVerifier


logger = logging.getLogger("pipeline")

# ---------------------------------------------------------------------------
# Detection Thresholds (Configurable)
# ---------------------------------------------------------------------------
CONFIDENCE_GATE = 90          # Minimum VIIRS confidence % to process
CLUSTER_DISTANCE_KM = 2.0     # DBSCAN eps for spatial clustering
CLUSTER_MIN_SAMPLES = 2       # DBSCAN min_samples for core point
CLUSTER_TIME_WINDOW_H = 2     # Max hours apart for temporal clustering
FRP_HIGH_ENERGY_MW = 20.0     # FRP threshold for high-energy auto-confirm
COMPOSITE_CONFIRM = 65        # Composite score threshold for CONFIRMED
COMPOSITE_PENDING = 35        # Composite score threshold for PENDING (below = FALSE_POSITIVE candidate)
NOTIFICATION_COOLDOWN_H = 6   # Minimum hours between Telegram alerts for the SAME fire


def parse_firms_time(acq_date, acq_time):
    """
    Parses NASA FIRMS acquisition date and acquisition time (e.g. 1345) 
    into a timezone-aware UTC datetime. Raises ValueError on failure.
    """
    try:
        # acq_time can be a string, float, or integer. Handle decimal strings like '1345.0'
        time_int = int(float(acq_time))
        time_str = f"{time_int:04d}"
        hour = int(time_str[:2])
        minute = int(time_str[2:])
        
        # Handle acq_date if it's already a datetime/Timestamp or format string
        if hasattr(acq_date, "strftime"):
            date_obj = acq_date
        else:
            date_str = str(acq_date).split()[0]
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            
        return datetime(date_obj.year, date_obj.month, date_obj.day, hour, minute, tzinfo=timezone.utc)
    except Exception as e:
        raise ValueError(f"Failed to parse FIRMS time (acq_date={acq_date}, acq_time={acq_time}): {e}")

def parse_confidence(val):
    """
    Parses NASA FIRMS confidence values.
    Handles MODIS (integer percentage 0-100) and VIIRS (categorical strings: 'l', 'n', 'h')
    mapping them to consistent percentage integers.
    """
    try:
        # If it's a numeric percentage, parse directly
        return int(float(val))
    except (ValueError, TypeError):
        # Map VIIRS categorical ratings to nominal percentages
        val_str = str(val).strip().lower()
        if val_str == 'l':
            return 30  # Low confidence
        elif val_str == 'n':
            return 70  # Nominal confidence
        elif val_str == 'h':
            return 95  # High confidence
        return 50  # Default fallback

def haversine(lat1, lon1, lat2, lon2):
    """Calculates the distance between two lat/lon coordinates in kilometers."""
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c


# ---------------------------------------------------------------------------
# DBSCAN Spatial Clustering (replaces O(n²) brute-force)
# ---------------------------------------------------------------------------
def cluster_hotspots(forest_hotspots):
    """
    Clusters forest hotspots using DBSCAN with haversine metric.
    Returns a dict mapping hotspot index -> (cluster_id, cluster_size).
    cluster_id = -1 means noise/isolated point.
    
    Uses O(n log n) spatial indexing instead of O(n²) pairwise comparison.
    """
    if len(forest_hotspots) < 2:
        return {i: (-1, 0) for i in range(len(forest_hotspots))}
    
    try:
        from sklearn.cluster import DBSCAN
        
        # Extract coordinates and convert to radians for haversine
        coords = np.array([
            [float(h["latitude"]), float(h["longitude"])] for h in forest_hotspots
        ])
        coords_rad = np.radians(coords)
        
        # DBSCAN with haversine metric
        # eps = distance in radians: CLUSTER_DISTANCE_KM / Earth_radius_km
        eps_rad = CLUSTER_DISTANCE_KM / 6371.0
        clustering = DBSCAN(
            eps=eps_rad, 
            min_samples=CLUSTER_MIN_SAMPLES, 
            metric='haversine'
        ).fit(coords_rad)
        
        labels = clustering.labels_
        
        # Build cluster_id -> size mapping
        from collections import Counter
        cluster_counts = Counter(l for l in labels if l != -1)
        
        # Apply temporal filtering: break clusters where detections are > CLUSTER_TIME_WINDOW_H apart
        result = {}
        for i, label in enumerate(labels):
            if label == -1:
                result[i] = (-1, 0)
            else:
                # Verify temporal proximity within the cluster
                cluster_members = [j for j, l in enumerate(labels) if l == label]
                try:
                    time_i = parse_firms_time(
                        forest_hotspots[i]["acq_date"], 
                        forest_hotspots[i]["acq_time"]
                    )
                    temporal_neighbors = 0
                    for j in cluster_members:
                        if j == i:
                            continue
                        time_j = parse_firms_time(
                            forest_hotspots[j]["acq_date"],
                            forest_hotspots[j]["acq_time"]
                        )
                        if abs((time_i - time_j).total_seconds()) <= CLUSTER_TIME_WINDOW_H * 3600:
                            temporal_neighbors += 1
                    
                    if temporal_neighbors > 0:
                        result[i] = (label, cluster_counts[label])
                    else:
                        # Spatially close but temporally distant — treat as isolated
                        result[i] = (-1, 0)
                except ValueError:
                    result[i] = (label, cluster_counts[label])
        
        return result
        
    except ImportError:
        logger.warning("scikit-learn not installed. Falling back to simple cluster detection.")
        return _fallback_cluster(forest_hotspots)


def _fallback_cluster(forest_hotspots):
    """Legacy O(n²) clustering fallback if scikit-learn is unavailable."""
    clustered_indices = set()
    n = len(forest_hotspots)
    for i in range(n):
        for j in range(i + 1, n):
            lat1, lon1 = float(forest_hotspots[i]["latitude"]), float(forest_hotspots[i]["longitude"])
            lat2, lon2 = float(forest_hotspots[j]["latitude"]), float(forest_hotspots[j]["longitude"])
            if haversine(lat1, lon1, lat2, lon2) <= CLUSTER_DISTANCE_KM:
                try:
                    time1 = parse_firms_time(forest_hotspots[i]["acq_date"], forest_hotspots[i]["acq_time"])
                    time2 = parse_firms_time(forest_hotspots[j]["acq_date"], forest_hotspots[j]["acq_time"])
                    if abs((time1 - time2).total_seconds()) <= CLUSTER_TIME_WINDOW_H * 3600:
                        clustered_indices.add(i)
                        clustered_indices.add(j)
                except ValueError:
                    pass
    
    result = {}
    for i in range(n):
        if i in clustered_indices:
            result[i] = (0, len(clustered_indices))  # simplified: one big cluster
        else:
            result[i] = (-1, 0)
    return result


# ---------------------------------------------------------------------------
# Composite Confidence Score
# ---------------------------------------------------------------------------
def compute_composite_score(frp, viirs_confidence, cluster_id, cluster_size,
                            risk_score, smoke_detected, smoke_confidence,
                            is_nighttime, multi_sensor_count, social_bonus=0.0):
    """
    Computes a weighted composite fire confidence score (0-100) that integrates
    all available signals into a single decision metric.
    
    This replaces the binary auto_confirm logic with a gradient approach.
    """
    score = 0.0
    
    # 1. Thermal signal strength (0-30 points)
    #    FRP is the most direct measure of fire energy
    score += min(30.0, frp * 0.5)  # 60 MW = max thermal score
    
    # 2. VIIRS sensor confidence (0-15 points)
    score += (viirs_confidence / 100.0) * 15.0
    
    # 3. Spatial clustering (0-15 points)
    #    Clustered hotspots are far more likely to be real fires
    if cluster_id >= 0:
        score += min(15.0, 5.0 + cluster_size * 2.0)
    
    # 4. Meteorological conditions (0-15 points)
    #    High fire weather risk increases fire likelihood
    if risk_score is not None:
        score += (risk_score / 100.0) * 15.0
    
    # 5. Visual/AI verification (0-15 points)
    if smoke_detected:
        score += smoke_confidence * 15.0
    
    # 6. Bonus: Nighttime detection reliability (+5)
    #    Nighttime VIIRS detections have far fewer false positives (no sun glint)
    if is_nighttime:
        score += 5.0
    
    # 7. Bonus: Multi-sensor cross-validation (+10)
    #    If 2+ independent satellites detected nearby hotspots, it's almost certainly real
    if multi_sensor_count >= 2:
        score += 10.0
    elif multi_sensor_count > 0:
        score += 3.0

    # 8. Bonus: Social Media & Citizen Crowdsource Reports (+10 to +15 pts)
    if social_bonus > 0:
        score += social_bonus
    
    return min(100.0, round(score, 1))



# ---------------------------------------------------------------------------
# Multi-Sensor Cross-Validation
# ---------------------------------------------------------------------------
def count_multi_sensor_detections(hotspot, all_hotspots):
    """
    Counts how many different satellite sensors detected hotspots near this location.
    A fire seen by 2+ independent satellites is extremely high confidence.
    """
    lat = float(hotspot["latitude"])
    lon = float(hotspot["longitude"])
    this_source = hotspot.get("source", "unknown")
    
    other_sensors = set()
    for h in all_hotspots:
        h_source = h.get("source", "unknown")
        if h_source == this_source:
            continue
        h_lat = float(h["latitude"])
        h_lon = float(h["longitude"])
        if haversine(lat, lon, h_lat, h_lon) <= CLUSTER_DISTANCE_KM:
            other_sensors.add(h_source)
    
    return len(other_sensors) + 1  # +1 for this sensor


def run_pipeline():
    logger.info("=================================================================")
    logger.info("ALGERIA FIRE WATCH PIPELINE START (v2 — Multi-Sensor + DBSCAN)")
    logger.info("=================================================================")

    notifier = TelegramNotifier()
    
    try:
        # 1. Validate Configurations
        config_ok = validate_config(check_db=False, check_copernicus=False)
        if not config_ok:
            raise RuntimeError("Startup configuration validation failed. Critical settings missing.")

        # 2. Initialize Clients
        firms = FirmsClient()
        spatial = SpatialFilter()
        weather = WeatherClient()
        copernicus = CopernicusClient()
        detector = SmokeDetector()
        db = DbClient()
        social_verifier = SocialVerifier()

        # Fetch recent citizen reports for crowdsourced cross-validation
        recent_citizen_reports = db.get_recent_citizen_reports(limit=100)


        # 3. Fetch Active Fires — Multi-Sensor Fusion (SNPP + NOAA-20 + NOAA-21)
        max_retries = 3
        backoff = 2
        df = None
        
        for attempt in range(max_retries):
            df = firms.fetch_active_fires_multi_sensor(day_range=1)
            if df is not None and not df.empty:
                break
            if df is not None and df.empty:
                break  # Successful fetch, just no fires
            logger.warning(f"Fetch attempt {attempt+1}/{max_retries} failed. Retrying in {backoff ** attempt}s...")
            time.sleep(backoff ** attempt)
            
        if df is None:
            raise RuntimeError("Failed to fetch active fires from NASA FIRMS API after maximum retry attempts.")
            
        if df.empty:
            logger.info("No active thermal hotspots detected in Algeria in the last 24 hours. Pipeline finished.")
            return

        logger.info(f"Total hotspots from all sensors: {len(df)}")

        # First round filter: keep only hotspots inside the Northern Forest Hazard zone
        forest_hotspots = []
        for idx, row in df.iterrows():
            lat = float(row.get("latitude", 0.0))
            lon = float(row.get("longitude", 0.0))
            if spatial.is_in_forest_zone(lat, lon):
                forest_hotspots.append(row.to_dict())

        if not forest_hotspots:
            logger.info("No active thermal hotspots found inside the forest hazard zones. Pipeline finished.")
            return

        logger.info(f"Found {len(forest_hotspots)} forest hotspots from {len(set(h.get('source','?') for h in forest_hotspots))} sensor(s). Running DBSCAN clustering...")

        # 4. DBSCAN Spatial + Temporal Clustering (replaces O(n²) brute-force)
        cluster_results = cluster_hotspots(forest_hotspots)
        
        n_clustered = sum(1 for v in cluster_results.values() if v[0] >= 0)
        n_total = len(forest_hotspots)
        logger.info(f"Cluster analysis complete: {n_clustered} of {n_total} hotspots belong to active clusters.")

        # Auto-resolve fires that haven't been re-detected in 24 hours
        db.resolve_old_fires(hours=24)

        # Process each forest hotspot
        processed_count = 0
        alerts_triggered = 0

        # Keep track of fire locations processed in the CURRENT run to avoid duplicate alerts for adjacent pixels
        # of the same fire from the exact same satellite pass
        processed_pixels_in_run = []

        for idx, row in enumerate(forest_hotspots):
            lat = float(row.get("latitude", 0.0))
            lon = float(row.get("longitude", 0.0))
            frp = float(row.get("frp", 0.0))
            confidence = parse_confidence(row.get("confidence", 0))
            acq_date = row.get("acq_date")
            acq_time_raw = row.get("acq_time")
            
            # Extract additional FIRMS columns (available but previously unused)
            brightness = row.get("brightness")
            bright_t31 = row.get("bright_t31")
            daynight = str(row.get("daynight", "")).upper() if row.get("daynight") else None
            scan_pixel = row.get("scan")
            source = row.get("source", "VIIRS_SNPP_NRT")
            
            # Convert brightness values to float safely
            try:
                brightness = float(brightness) if brightness is not None and str(brightness) != 'nan' else None
            except (ValueError, TypeError):
                brightness = None
            try:
                bright_t31 = float(bright_t31) if bright_t31 is not None and str(bright_t31) != 'nan' else None
            except (ValueError, TypeError):
                bright_t31 = None
            try:
                scan_pixel = float(scan_pixel) if scan_pixel is not None and str(scan_pixel) != 'nan' else None
            except (ValueError, TypeError):
                scan_pixel = None
            
            is_nighttime = (daynight == "N")
            
            # Enforce confidence gate: Skip low/nominal confidence triggers (only allow 95% / 'h')
            # to prevent false alarms from cities or non-forest heat anomalies.
            if confidence < CONFIDENCE_GATE:
                logger.info(f"Skipping hotspot at ({lat:.4f}, {lon:.4f}) - low/nominal confidence ({confidence}%).")
                continue

            # Parse time to timezone-aware UTC datetime
            acq_datetime = parse_firms_time(acq_date, acq_time_raw)
            acq_time_str = acq_datetime.strftime("%Y-%m-%d %H:%M:%S UTC")

            # Step A: Filter out adjacent pixels from the exact same satellite pass (same timestamp + source)
            # This keeps only one alert for the same fire in a single run
            is_duplicate_pixel = False
            for processed in processed_pixels_in_run:
                if processed["acq_time"] == acq_time_str and processed.get("source") == source:
                    dist = haversine(lat, lon, processed["latitude"], processed["longitude"])
                    if dist <= CLUSTER_DISTANCE_KM:
                        is_duplicate_pixel = True
                        break

            if is_duplicate_pixel:
                logger.info(f"Skipping hotspot at ({lat:.4f}, {lon:.4f}) - adjacent pixel of same fire already processed in this pass.")
                continue

            logger.info(f"--- Hotspot {processed_count+1}: Coord ({lat:.4f}, {lon:.4f}) | FRP: {frp} MW | Conf: {confidence}% | Sensor: {source} | {'Night' if is_nighttime else 'Day'} ---")
            processed_count += 1

            # Register this pixel location as processed in the current run
            processed_pixels_in_run.append({
                "latitude": lat,
                "longitude": lon,
                "acq_time": acq_time_str,
                "source": source
            })

            # Step 1: Duplicate run check (location and exact acquisition time)
            # If this exact detection point was already processed and saved in a previous run, skip it entirely
            existing_exact = db.find_exact_processed_hotspot(lat, lon, acq_datetime)
            if existing_exact is not None:
                logger.info(f"Hotspot at ({lat:.4f}, {lon:.4f}) at {acq_time_str} was already processed in a previous execution. Skipping.")
                continue

            # Step 2: Ongoing active fire check (within 2km and 12 hours of the detection's time)
            existing = db.find_recent_nearby_fire(lat, lon, max_distance_km=2.0, max_hours=12, reference_time=acq_datetime)
            
            updating_pending_id = None
            if existing is not None:
                logger.info(f"Ongoing fire already exists in database (ID: {existing['id']}, Status: {existing['status']}). Updating metrics.")
                db.update_existing_fire_frp(existing["id"], frp, confidence, acq_datetime)
                
                # Send Telegram updates ONLY for CONFIRMED ongoing fires WITH cooldown
                if existing["status"] == "CONFIRMED":
                    # Check notification cooldown — only re-notify if enough time has elapsed
                    last_notified = existing.get("last_notified_at")
                    hours_since_notify = float('inf')
                    if last_notified is not None:
                        if last_notified.tzinfo is None:
                            last_notified = last_notified.replace(tzinfo=timezone.utc)
                        hours_since_notify = (datetime.now(timezone.utc) - last_notified).total_seconds() / 3600
                    
                    if hours_since_notify < NOTIFICATION_COOLDOWN_H:
                        logger.info(f"Notification cooldown active for fire ID {existing['id']} ({hours_since_notify:.1f}h < {NOTIFICATION_COOLDOWN_H}h). Skipping re-alert.")
                        continue
                    
                    update_msg = notifier.format_fire_alert(
                        lat=lat, lon=lon, frp=frp, confidence=confidence, acq_time=acq_time_str,
                        status="CONFIRMED", is_update=True
                    )
                    notifier.send_message(update_msg)
                    db.mark_fire_notified(existing["id"])
                    alerts_triggered += 1
                    continue
                elif existing["status"] == "FALSE_POSITIVE":
                    continue
                elif existing["status"] == "PENDING":
                    # Mark that we are verifying an existing pending fire
                    updating_pending_id = existing["id"]

            # Fetch live meteorological risk variables (now includes precipitation + drought)
            weather_metrics = weather.fetch_weather(lat, lon)
            temp, humidity, wind_speed, wind_direction, risk_score = None, None, None, None, 50.0
            precipitation, days_since_rain = None, None
            
            if weather_metrics:
                temp = weather_metrics["temp"]
                humidity = weather_metrics["humidity"]
                wind_speed = weather_metrics["wind_speed"]
                wind_direction = weather_metrics["wind_direction"]
                precipitation = weather_metrics.get("precipitation")
                days_since_rain = weather_metrics.get("days_since_rain")
                risk_score = weather.calculate_fire_risk(
                    temp, humidity, wind_speed, wind_direction,
                    precipitation=precipitation, days_since_rain=days_since_rain
                )

            # Retrieve cluster info for this hotspot
            cluster_id, cluster_size = cluster_results.get(idx, (-1, 0))
            is_clustered = cluster_id >= 0
            
            # Count multi-sensor cross-validation
            multi_sensor_count = count_multi_sensor_detections(row, forest_hotspots)

            # Evaluate auto-confirmation triggers (legacy Strategy 1 & Strategy 2)
            is_high_energy = (frp >= FRP_HIGH_ENERGY_MW and confidence >= 80)
            auto_confirm = is_high_energy or is_clustered
            
            bypass_reason = None
            if auto_confirm:
                reasons = []
                if is_high_energy:
                    reasons.append(f"high-energy thermal signature (FRP={frp:.1f} MW)")
                if is_clustered:
                    reasons.append(f"cluster proximity ({cluster_size} hotspots in cluster)")
                if multi_sensor_count >= 2:
                    reasons.append(f"multi-sensor confirmation ({multi_sensor_count} satellites)")
                if is_nighttime:
                    reasons.append("nighttime detection (higher reliability)")
                bypass_reason = f"⚠️ <b>Note</b>: Alert confirmed — {', '.join(reasons)}."
                logger.info(f"Auto-confirmation triggered: {bypass_reason.replace('<b>','').replace('</b>','')}")

            # Save new fire or update the existing pending fire with all new metadata
            fire_id = None
            fire_data = {
                "latitude": lat,
                "longitude": lon,
                "frp": frp,
                "confidence": int(confidence),
                "acquisition_time": acq_datetime,
                "temp": temp,
                "humidity": humidity,
                "wind_speed": wind_speed,
                "wind_direction": wind_direction,
                "risk_score": float(risk_score) if risk_score is not None else None,
                "status": "CONFIRMED" if auto_confirm else "PENDING",
                "source": str(source) if source else "VIIRS_SNPP_NRT",
                "brightness": brightness,
                "bright_t31": bright_t31,
                "daynight": daynight,
                "scan_pixel": scan_pixel,
                "cluster_id": int(cluster_id),
                "cluster_size": int(cluster_size),
                "days_since_rain": float(days_since_rain) if days_since_rain is not None else None,
            }

            if updating_pending_id:
                # Update the EXISTING pending record with fresh data — no new row created
                fire_id = updating_pending_id
                db.update_fire_full(fire_id, fire_data)
                logger.info(f"Updated existing PENDING fire ID {fire_id} with new detection data (status: {fire_data['status']}).")
            else:
                fire_id = db.save_fire(fire_data)

            # Check if database save succeeded
            if fire_id is None:
                logger.error(f"Failed to record fire detection in database for coordinates ({lat:.4f}, {lon:.4f}). Skipping alert.")
                continue

            # Download Sentinel-2 / Landsat-8 / Landsat-9 Optical image (Strategy 3: Multi-Sensor Trigger)
            image_path, product_id = copernicus.fetch_latest_sentinel_image(lat, lon, acq_datetime)

            # Match citizen crowdsource reports for this hotspot location
            matched_social_reports = social_verifier.match_reports_with_hotspot(lat, lon, acq_datetime, recent_citizen_reports)
            social_bonus = social_verifier.calculate_score_bonus(matched_social_reports)
            social_note = social_verifier.format_telegram_summary(matched_social_reports)
            if matched_social_reports:
                logger.info(f"Social/Citizen verification matched {len(matched_social_reports)} report(s). Score bonus: +{social_bonus} pts.")

            # Compute composite confidence score (integrates all available signals)
            smoke_detected = False
            ai_confidence = 0.0
            annotated_path = None
            
            if image_path:
                # Verify Smoke using AI/CV
                smoke_detected, ai_confidence, annotated_path = detector.detect_smoke(image_path)

            composite_score = compute_composite_score(
                frp=frp,
                viirs_confidence=confidence,
                cluster_id=cluster_id,
                cluster_size=cluster_size,
                risk_score=risk_score,
                smoke_detected=smoke_detected,
                smoke_confidence=ai_confidence,
                is_nighttime=is_nighttime,
                multi_sensor_count=multi_sensor_count,
                social_bonus=social_bonus
            )
            
            logger.info(f"Composite confidence score: {composite_score}/100 (threshold: CONFIRM={COMPOSITE_CONFIRM}, PENDING={COMPOSITE_PENDING})")

            # Update composite score in database
            try:
                fire_data_update = {"composite_score": composite_score}
                # Only update composite_score field on existing record
                db.update_fire_status(fire_id, fire_data.get("status", "PENDING") if not updating_pending_id else existing.get("status", "PENDING"))
            except Exception:
                pass  # Non-critical update

            if image_path:
                # If smoke is detected, or if we were auto-confirmed by FRP/cluster rules, alert!
                if smoke_detected or auto_confirm:
                    final_status = "CONFIRMED"
                    
                    # Upgrade status in DB to CONFIRMED
                    db.update_fire_status(fire_id, "CONFIRMED", product_id, quicklook_url=image_path)
                    
                    # Setup specific alert notes
                    alert_bypass_reason = bypass_reason
                    if not smoke_detected and auto_confirm:
                        alert_bypass_reason = (
                            f"{bypass_reason or ''}\n"
                            f"💨 <i>Optical imagery loaded but smoke plume verification fell back to thermal signature context.</i>"
                        )
                    
                    # Add composite score & social summary to alert
                    score_note = f"\n📊 <b>Composite Score:</b> {composite_score}/100"
                    if multi_sensor_count >= 2:
                        score_note += f" | 🛰️ <b>{multi_sensor_count} satellites</b> confirmed"
                    if is_nighttime:
                        score_note += " | 🌙 Night detection"
                    if social_note:
                        score_note += social_note
                    
                    alert_msg = notifier.format_fire_alert(
                        lat=lat, lon=lon, frp=frp, confidence=confidence, acq_time=acq_time_str,
                        status=final_status, temp=temp, humidity=humidity, wind_speed=wind_speed,
                        wind_direction=wind_direction, risk_score=risk_score, 
                        bypass_reason=(alert_bypass_reason or "") + score_note
                    )

                    
                    # Attach the annotated image (the 'AI look') if available
                    photo_to_send = annotated_path if annotated_path else image_path
                    notifier.send_photo(photo_to_send, caption=alert_msg)
                    db.mark_fire_notified(fire_id)
                    alerts_triggered += 1
                else:
                    # No smoke detected, and not auto-confirmed -> Flag as FALSE_POSITIVE
                    logger.info(f"No smoke plume detected in Sentinel-2/Landsat image. Composite score: {composite_score}. Flagging as FALSE_POSITIVE.")
                    db.update_fire_status(fire_id, "FALSE_POSITIVE", product_id, quicklook_url=image_path)
            else:
                # Optical image was unavailable (cloud cover/orbital gap/timeout)
                logger.info("Satellite optical imagery unavailable (cloud cover or orbital gap).")
                
                if auto_confirm:
                    # If we auto-confirmed, we STILL send the Telegram message (text-only) with our bypass reason
                    logger.info("Sending text-only alert to Telegram since image is unavailable.")
                    
                    score_note = f"\n📊 <b>Composite Score:</b> {composite_score}/100"
                    if multi_sensor_count >= 2:
                        score_note += f" | 🛰️ <b>{multi_sensor_count} satellites</b> confirmed"
                    if is_nighttime:
                        score_note += " | 🌙 Night detection"
                    
                    alert_msg = notifier.format_fire_alert(
                        lat=lat, lon=lon, frp=frp, confidence=confidence, acq_time=acq_time_str,
                        status="CONFIRMED", temp=temp, humidity=humidity, wind_speed=wind_speed,
                        wind_direction=wind_direction, risk_score=risk_score, 
                        bypass_reason=(bypass_reason or "") + score_note
                    )
                    notifier.send_message(alert_msg)
                    db.mark_fire_notified(fire_id)
                    alerts_triggered += 1
                else:
                    # Weak isolated fire with no image -> stays PENDING in DB, no alert sent
                    logger.info(f"Storing in database as PENDING verification. Composite score: {composite_score} (no Telegram alert sent).")

        logger.info("=================================================================")
        logger.info(f"PIPELINE COMPLETED. Hotspots Processed: {processed_count} | Warnings Sent: {alerts_triggered}")
        logger.info("=================================================================")

    except Exception as e:
        logger.exception("CRITICAL: Pipeline run failed with unhandled exception.")
        
        # Operational Telegram Failure Alert
        err_msg = (
            f"🚨 <b>ALGERIA FIRE WATCH PIPELINE FAILURE DETECTED</b> 🚨\n\n"
            f"<b>Error Type:</b> <code>{type(e).__name__}</code>\n"
            f"<b>Error Details:</b> <code>{html.escape(str(e))}</code>\n\n"
            f"⚠️ <i>Please inspect the execution log on the deployment environment for full traceback details.</i>"
        )
        try:
            notifier.send_message(err_msg)
        except Exception as send_err:
            logger.error(f"Failed to dispatch operational crash report to Telegram: {send_err}")
            
        # Re-raise to fail build/runner successfully
        raise e

if __name__ == "__main__":
    run_pipeline()
