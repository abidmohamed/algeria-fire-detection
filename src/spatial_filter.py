import math
from src.config import GEOJSON_PATH

try:
    import geopandas as gpd
    from shapely.geometry import Point
    GEOPANDAS_AVAILABLE = True
except ImportError:
    gpd = None
    Point = None
    GEOPANDAS_AVAILABLE = False


class SpatialFilter:
    def __init__(self, geojson_path=GEOJSON_PATH):
        self.geojson_path = geojson_path
        self.gdf = None
        self._load_boundary()
        
    def _load_boundary(self):
        """Loads the simplified northern forest risk zone GeoJSON."""
        if not GEOPANDAS_AVAILABLE:
            print("[WARNING] geopandas/shapely is not installed. SpatialFilter using fallback bounding box.")
            self.gdf = None
            return

        try:
            if not self.geojson_path.exists():
                raise FileNotFoundError(f"GeoJSON file not found at {self.geojson_path}")
            self.gdf = gpd.read_file(self.geojson_path)
            
            # Ensure CRS is EPSG:4326 (WGS84) in a robust way
            if self.gdf.crs is None:
                self.gdf.set_crs(epsg=4326, inplace=True)
            elif not self.gdf.crs.equals("EPSG:4326"):
                try:
                    self.gdf = self.gdf.to_crs(epsg=4326)
                except Exception as proj_err:
                    print(f"[WARNING] CRS re-projection failed, falling back: {proj_err}")
        except Exception as e:
            print(f"[ERROR] Failed to load GeoJSON boundary: {e}")
            self.gdf = None
            
    def is_in_forest_zone(self, lat, lon):
        """
        Check if a given latitude and longitude is inside the northern forest/park zone.
        Filters out southern Sahara gas flaring points.
        """
        # Coordinate type & NaN validation
        if lat is None or lon is None:
            return False
        try:
            lat = float(lat)
            lon = float(lon)
        except (ValueError, TypeError):
            return False
        if math.isnan(lat) or math.isnan(lon):
            return False
            
        # Hard check: All Algerian forest zones and national parks are strictly north of 32.0° N.
        # This quickly eliminates Hassi Messaoud (31.68° N), In Amenas (28.03° N).
        # Hassi R'Mel (32.93° N) is handled by the precise GeoJSON polygon exclusion.
        if lat < 32.0:
            return False
            
        if self.gdf is None:
            # Fallback: simple bounding box if GeoJSON loading failed
            print("[WARNING] GeoJSON boundary not loaded. Using fallback bounding box.")
            return 32.0 <= lat <= 37.5 and -2.5 <= lon <= 9.0
            
        try:
            point = Point(lon, lat)  # Shapely Point uses (longitude, latitude) / (x, y)
            # Check if any geometry in GDF contains the point
            return self.gdf.contains(point).any()
        except Exception as e:
            print(f"[ERROR] Spatial lookup failed for point ({lat}, {lon}): {e}")
            return False
