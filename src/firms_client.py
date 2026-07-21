import io
import logging
import pandas as pd
import requests
from src.config import NASA_FIRMS_KEY

logger = logging.getLogger("firms_client")

class FirmsClient:
    def __init__(self, api_key=NASA_FIRMS_KEY):
        self.api_key = api_key
        self.base_url = "https://firms.modaps.eosdis.nasa.gov/api"
        # All available VIIRS sensor sources for multi-sensor fusion
        self.viirs_sources = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]
        # Bounding box for Algeria
        self.algeria_bbox = {
            "west": -8.6,
            "south": 18.9,
            "east": 11.9,
            "north": 37.1
        }
        
    def fetch_active_fires(self, country_code="DZA", source="VIIRS_SNPP_NRT", day_range=1):
        """
        Status wrapper to fetch active fires for Algeria.
        Uses the bounding box endpoint internally since the country endpoint is deprecated/disabled by NASA.
        """
        if country_code == "DZA":
            return self.fetch_active_fires_bbox(
                west=self.algeria_bbox["west"],
                south=self.algeria_bbox["south"],
                east=self.algeria_bbox["east"],
                north=self.algeria_bbox["north"],
                source=source,
                day_range=day_range
            )
        else:
            logger.warning(f"Country code {country_code} requested. Attempting default bounding box search.")
            return self.fetch_active_fires_bbox(source=source, day_range=day_range)

    def fetch_active_fires_multi_sensor(self, country_code="DZA", day_range=1):
        """
        Fetches active fires from ALL available VIIRS sensors (SNPP, NOAA-20, NOAA-21)
        and merges results with source tagging. Multi-sensor detections of the same fire
        provide cross-validation and higher confidence.
        """
        import pandas as pd
        all_dfs = []
        
        for source in self.viirs_sources:
            try:
                logger.info(f"Querying {source}...")
                df = self.fetch_active_fires(country_code=country_code, source=source, day_range=day_range)
                if df is not None and not df.empty:
                    df["source"] = source
                    # Tag satellite name for readability
                    satellite_map = {
                        "VIIRS_SNPP_NRT": "Suomi-NPP",
                        "VIIRS_NOAA20_NRT": "NOAA-20",
                        "VIIRS_NOAA21_NRT": "NOAA-21"
                    }
                    df["satellite_name"] = satellite_map.get(source, source)
                    all_dfs.append(df)
                    logger.info(f"{source}: {len(df)} hotspots found.")
                else:
                    logger.info(f"{source}: No hotspots or fetch failed.")
            except Exception as e:
                logger.warning(f"Failed to fetch from {source}: {e}. Continuing with other sensors.")
        
        if not all_dfs:
            logger.warning("No hotspots retrieved from any VIIRS sensor.")
            return pd.DataFrame()
        
        combined = pd.concat(all_dfs, ignore_index=True)
        logger.info(f"Multi-sensor fusion complete: {len(combined)} total hotspots from {len(all_dfs)} sensor(s).")
        return combined
            
    def fetch_active_fires_bbox(self, west=-8.6, south=18.9, east=11.9, north=37.1, source="VIIRS_SNPP_NRT", day_range=1):
        """
        Fetches active fires using a spatial bounding box.
        """
        if not self.api_key:
            logger.error("NASA FIRMS API key is missing. Cannot fetch fires.")
            return None
            
        # Endpoint: https://firms.modaps.eosdis.nasa.gov/api/area/csv/{map_key}/{source}/{west},{south},{east},{north}/{day_range}
        bbox_str = f"{west},{south},{east},{north}"
        url = f"{self.base_url}/area/csv/{self.api_key}/{source}/{bbox_str}/{day_range}"
        
        try:
            logger.info(f"Fetching active fires from NASA FIRMS API in BBox {bbox_str} using source: {source}...")
            response = requests.get(url, timeout=15)
            
            # NASA FIRMS returns HTTP 200 even on authentication failure, with "Invalid MAP_KEY" text in the body.
            if response.status_code == 200:
                csv_data = response.text
                
                # Check for invalid map key error in body
                if "Invalid MAP_KEY" in csv_data:
                    logger.error("NASA FIRMS API returned 'Invalid MAP_KEY'. Please verify your configuration.")
                    return None
                
                if not csv_data.strip() or csv_data.strip() == "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight":
                    logger.info("No active fires returned (empty response).")
                    return pd.DataFrame()
                
                df = pd.read_csv(io.StringIO(csv_data))
                logger.info(f"Successfully fetched {len(df)} active fire records from {source}.")
                return df
            elif response.status_code == 429:
                logger.error("Rate limit exceeded (429) on NASA FIRMS API.")
                return None
            else:
                logger.error(f"FIRMS API request failed with status code {response.status_code}. Response: {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error connecting to FIRMS API: {e}")
            return None
