try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import requests

class WeatherClient:
    def __init__(self):
        self.base_url = "https://api.open-meteo.com/v1/forecast"
        
    def fetch_weather(self, lat, lon):
        """
        Fetches current weather parameters for a given coordinate.
        Returns a dict containing temp, humidity, wind_speed, wind_direction,
        precipitation, days_since_rain, and precip_7day.
        """
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": [
                "temperature_2m", 
                "relative_humidity_2m", 
                "wind_speed_10m", 
                "wind_direction_10m",
                "precipitation"
            ],
            "daily": "precipitation_sum",
            "past_days": 7,
            "timezone": "auto"
        }
        
        try:
            print(f"[INFO] Fetching weather from Open-Meteo for coordinates ({lat:.4f}, {lon:.4f})...")
            response = requests.get(self.base_url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                current = data.get("current", {})
                daily = data.get("daily", {})
                
                # Calculate precipitation totals and days since last rain
                daily_precip = daily.get("precipitation_sum", [])
                precip_7day = sum(p for p in daily_precip if p is not None)
                
                days_since_rain = 0
                if daily_precip:
                    # Walk backwards through the daily values to find last rainy day
                    for p in reversed(daily_precip):
                        if p is not None and p > 0.0:
                            break
                        days_since_rain += 1
                    else:
                        # No rain found in any of the past days
                        days_since_rain = len(daily_precip)
                
                metrics = {
                    "temp": current.get("temperature_2m"),
                    "humidity": current.get("relative_humidity_2m"),
                    "wind_speed": current.get("wind_speed_10m"),
                    "wind_direction": current.get("wind_direction_10m"),
                    "precipitation": current.get("precipitation"),
                    "days_since_rain": days_since_rain,
                    "precip_7day": round(precip_7day, 2)
                }
                print(f"[INFO] Weather fetched: Temp={metrics['temp']}°C, Humidity={metrics['humidity']}%, "
                      f"Wind={metrics['wind_speed']} km/h, Precip={metrics['precipitation']} mm, "
                      f"Days since rain={metrics['days_since_rain']}, 7-day precip={metrics['precip_7day']} mm")
                return metrics
            else:
                print(f"[ERROR] Failed to fetch weather from Open-Meteo: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            print(f"[ERROR] Exception during Open-Meteo call: {e}")
            return None
            
    def calculate_fire_risk(self, temp, humidity, wind_speed, wind_direction,
                            precipitation=None, days_since_rain=None):
        """
        Calculates a custom Fire Weather Risk Index (0-100) based on local conditions.
        Uses non-linear wind scaling, drought awareness, and Sirocco detection.
        """
        if temp is None or humidity is None or wind_speed is None:
            return 50.0  # Return average risk if weather fetch failed
            
        # 1. Temperature Risk Contribution (0 at 20°C, 100 at 40°C)
        temp_score = min(100.0, max(0.0, (temp - 20.0) * 5.0))
        
        # 2. Humidity Risk Contribution (0 at 80% RH, 100 at 20% RH)
        rh_score = min(100.0, max(0.0, (80.0 - humidity) * 1.67))
        
        # 3. Wind Speed Risk Contribution — power-law scaling (meaningful up to 100 km/h)
        wind_score = min(100.0, 100.0 * (wind_speed / 80.0) ** 1.5)
        
        # Base Index Calculation
        # Rebalanced weights: temp=0.35, humidity=0.35, wind=0.30
        base_index = (temp_score * 0.35) + (rh_score * 0.35) + (wind_score * 0.30)
        
        # 4. Drought modifier: no rain in 5+ days AND humidity < 30% → 1.15x multiplier
        drought_active = False
        if days_since_rain is not None and days_since_rain >= 5 and humidity < 30.0:
            drought_active = True
            base_index = min(100.0, base_index * 1.15)
            print("[WARNING] DROUGHT MODIFIER ACTIVE: No rain in 5+ days with low humidity. Fire risk boosted.")
        
        # 5. Sirocco Wind Check (Sirocco blows from the South/South-East, e.g., 135° to 225°)
        is_sirocco = False
        if temp > 38.0 and humidity < 25.0:
            if wind_direction is not None and (135.0 <= wind_direction <= 225.0):
                is_sirocco = True
                
        # Apply Sirocco multiplier if active (stacks with drought modifier)
        if is_sirocco:
            # Boost fire risk by 30% due to highly flammable wind conditions
            risk_index = min(100.0, base_index * 1.30)
            if drought_active:
                print("[WARNING] SIROCCO EFFECT ACTIVE (stacked with drought): Extreme fire conditions.")
            else:
                print("[WARNING] SIROCCO EFFECT ACTIVE: Hot, dry winds from the desert. Fire risk boosted.")
        else:
            risk_index = base_index
            
        return round(risk_index, 1)
