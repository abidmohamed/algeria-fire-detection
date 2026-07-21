import math
import pytest
from datetime import datetime, timezone, timedelta
from src.spatial_filter import SpatialFilter
from src.weather_client import WeatherClient
from src.social_verifier import SocialVerifier
from src.telegram_notifier import TelegramNotifier
from pipeline import compute_composite_score, parse_firms_time, parse_confidence, cluster_hotspots

def test_spatial_filter_invalid_coordinates():
    sf = SpatialFilter()
    
    # Test None inputs
    assert sf.is_in_forest_zone(None, 4.0) is False
    assert sf.is_in_forest_zone(36.0, None) is False
    
    # Test string inputs that cannot be parsed
    assert sf.is_in_forest_zone("invalid", 4.0) is False
    assert sf.is_in_forest_zone(36.0, "invalid") is False
    
    # Test NaN inputs
    assert sf.is_in_forest_zone(float('nan'), 4.0) is False
    assert sf.is_in_forest_zone(36.0, float('nan')) is False
    
    # Test points clearly below the 32.0 degree latitude cutoff (Sahara exclusion)
    assert sf.is_in_forest_zone(31.5, 3.0) is False
    assert sf.is_in_forest_zone(28.0, 1.5) is False

def test_spatial_filter_valid_string_coercion():
    sf = SpatialFilter()
    assert sf.is_in_forest_zone("31.0", "4.0") is False

def test_fire_risk_calculations():
    wc = WeatherClient()
    
    # Test fallback values on missing/None metrics
    assert wc.calculate_fire_risk(None, 15.0, 10.0, 180.0) == 50.0
    assert wc.calculate_fire_risk(40.0, None, 10.0, 180.0) == 50.0
    
    risk = wc.calculate_fire_risk(25.0, 50.0, 15.0, 90.0)
    assert 20.0 <= risk <= 50.0

def test_sirocco_multiplier_boost():
    wc = WeatherClient()
    risk_sirocco = wc.calculate_fire_risk(40.0, 15.0, 20.0, 180.0)
    risk_normal = wc.calculate_fire_risk(40.0, 15.0, 20.0, 90.0)
    
    assert risk_sirocco > risk_normal
    assert math.isclose(risk_sirocco, min(100.0, risk_normal * 1.3), rel_tol=1e-2)

def test_social_verifier_keyword_matching():
    sv = SocialVerifier()
    assert sv.is_fire_related_text("اندلاع حريق مهول بجيجل") is True
    assert sv.is_fire_related_text("Un incendie de forêt s'est déclaré près de Tizi Ouzou") is True
    assert sv.is_fire_related_text("Weather report for Algiers today") is False

def test_social_verifier_spatial_temporal_matching():
    sv = SocialVerifier()
    now = datetime.now(timezone.utc)
    
    hotspot_lat, hotspot_lon = 36.712, 4.045
    hotspot_time = now
    
    reports = [
        # Match within 2km and 1 hour
        {"id": 1, "latitude": 36.715, "longitude": 4.048, "reporter_type": "Citizen", "created_at": now - timedelta(minutes=30)},
        # Too far (> 20km)
        {"id": 2, "latitude": 37.000, "longitude": 4.500, "reporter_type": "Citizen", "created_at": now},
        # Too old (> 5 hours)
        {"id": 3, "latitude": 36.712, "longitude": 4.045, "reporter_type": "Forest Ranger", "created_at": now - timedelta(hours=6)},
    ]
    
    matches = sv.match_reports_with_hotspot(hotspot_lat, hotspot_lon, hotspot_time, reports, max_dist_km=10.0, max_hours=3.0)
    assert len(matches) == 1
    assert matches[0]["report_id"] == 1

def test_social_verifier_score_bonus():
    sv = SocialVerifier()
    assert sv.calculate_score_bonus([]) == 0.0
    assert sv.calculate_score_bonus([{"reporter_type": "Citizen"}]) == 10.0
    assert sv.calculate_score_bonus([{"reporter_type": "Forest Ranger"}]) == 15.0
    assert sv.calculate_score_bonus([{"reporter_type": "Citizen"}, {"reporter_type": "Citizen"}]) == 15.0

def test_composite_score_computation():
    # FRP=50 MW (25 pts), VIIRS=90% (13.5 pts), Cluster=Yes (9 pts), Risk=80 (12 pts), Smoke=Yes (15 pts), Night=Yes (+5 pts), Multi-Sat=2 (+10 pts), Social=+10 pts
    score = compute_composite_score(
        frp=50.0, viirs_confidence=90, cluster_id=0, cluster_size=2,
        risk_score=80.0, smoke_detected=True, smoke_confidence=1.0,
        is_nighttime=True, multi_sensor_count=2, social_bonus=10.0
    )
    assert score == 99.5 or score == 100.0

def test_firms_time_and_confidence_parsing():
    parsed_time = parse_firms_time("2026-07-21", "1430")
    assert parsed_time.hour == 14 and parsed_time.minute == 30
    
    assert parse_confidence("95") == 95
    assert parse_confidence("h") == 95
    assert parse_confidence("l") == 30
    assert parse_confidence("n") == 70

def test_telegram_notifier_cardinals():
    tn = TelegramNotifier()
    assert tn.get_wind_direction_cardinal(0) == "N"
    assert tn.get_wind_direction_cardinal(180) == "S"
    assert tn.get_wind_direction_cardinal(90) == "E"
    assert tn.get_wind_direction_cardinal(270) == "W"

def test_dbscan_clustering():
    hotspots = [
        {"latitude": 36.712, "longitude": 4.045, "acq_date": "2026-07-21", "acq_time": "1200"},
        {"latitude": 36.715, "longitude": 4.048, "acq_date": "2026-07-21", "acq_time": "1205"}, # Close
        {"latitude": 37.500, "longitude": 6.000, "acq_date": "2026-07-21", "acq_time": "1200"}, # Far
    ]
    clusters = cluster_hotspots(hotspots)
    assert clusters[0][0] == clusters[1][0]  # Same cluster
    assert clusters[2][0] == -1  # Noise

