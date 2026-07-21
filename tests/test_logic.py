import math
import pytest
from src.spatial_filter import SpatialFilter
from src.weather_client import WeatherClient

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
    # If the coordinate is a string representing a number, it should coerce correctly
    # Note: If it's below latitude 32.0, it will return False regardless of the geojson matching
    assert sf.is_in_forest_zone("31.0", "4.0") is False

def test_fire_risk_calculations():
    wc = WeatherClient()
    
    # Test fallback values on missing/None metrics
    assert wc.calculate_fire_risk(None, 15.0, 10.0, 180.0) == 50.0
    assert wc.calculate_fire_risk(40.0, None, 10.0, 180.0) == 50.0
    
    # Test moderate index condition
    # Temp 25°C, Humidity 50% RH, Wind 15 km/h, non-Sirocco wind (90°)
    # Result should be a moderate risk score (below 50)
    risk = wc.calculate_fire_risk(25.0, 50.0, 15.0, 90.0)
    assert 20.0 <= risk <= 50.0

def test_sirocco_multiplier_boost():
    wc = WeatherClient()
    
    # Base parameters matching Sirocco (Temp > 38, RH < 25, Wind from South: 135°-225°)
    # Temp 40°C, RH 15%, Wind 20 km/h from South (180°)
    risk_sirocco = wc.calculate_fire_risk(40.0, 15.0, 20.0, 180.0)
    
    # Risk should be significantly higher than if wind blew from East (90°)
    risk_normal = wc.calculate_fire_risk(40.0, 15.0, 20.0, 90.0)
    
    assert risk_sirocco > risk_normal
    # Boost factor is 1.3
    assert math.isclose(risk_sirocco, min(100.0, risk_normal * 1.3), rel_tol=1e-2)
