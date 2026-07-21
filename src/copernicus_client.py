import os
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
import requests
from src.config import CDSE_USERNAME, CDSE_PASSWORD

logger = logging.getLogger("copernicus_client")

class CopernicusClient:
    def __init__(self, username=CDSE_USERNAME, password=CDSE_PASSWORD):
        self.username = username
        self.password = password
        self.auth_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        self.odata_url = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
        self.token = None
        self.token_expiry = None

    def get_access_token(self):
        """Authenticates with CDSE Keycloak endpoint and retrieves an access token."""
        if not self.username or not self.password:
            logger.error("Copernicus CDSE credentials are not set. Cannot authenticate.")
            return None

        # Check if cached token is still valid (with a 30s buffer)
        if self.token and self.token_expiry and datetime.now(timezone.utc) < self.token_expiry:
            return self.token

        data = {
            "client_id": "cdse-public",
            "username": self.username,
            "password": self.password,
            "grant_type": "password",
        }

        try:
            logger.info("Authenticating with Copernicus Data Space Ecosystem...")
            response = requests.post(self.auth_url, data=data, timeout=15)
            response.raise_for_status()
            
            res_json = response.json()
            self.token = res_json.get("access_token")
            expires_in = res_json.get("expires_in", 600)  # default to 10 min
            self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 30)
            
            logger.info("Copernicus authentication successful.")
            return self.token
        except Exception as e:
            logger.error(f"Copernicus CDSE authentication failed: {e}")
            self.token = None
            return None

    def fetch_latest_sentinel_image(self, lat, lon, acquisition_time, save_dir="quicklooks"):
        """
        Queries OData for the latest Sentinel-2 image covering the coordinate.
        Downloads the quicklook preview image and returns its local file path and the product ID.
        """
        token = self.get_access_token()
        if not token:
            logger.warning("Skipping Sentinel-2 download due to authentication failure.")
            return None, None

        # Create save directory
        os.makedirs(save_dir, exist_ok=True)

        # 1. Define bounding box polygon (approx 2.2km buffer around coordinates)
        buffer = 0.01
        lon_min, lat_min = lon - buffer, lat - buffer
        lon_max, lat_max = lon + buffer, lat + buffer
        polygon_str = f"POLYGON(({lon_min} {lat_min}, {lon_max} {lat_min}, {lon_max} {lat_max}, {lon_min} {lat_max}, {lon_min} {lat_min}))"

        # 2. Time interval (past 7 days relative to detection time to ensure coverage)
        if isinstance(acquisition_time, str):
            try:
                # Handle common datetime string formats
                dt = datetime.fromisoformat(acquisition_time.replace("Z", "+00:00"))
            except ValueError:
                dt = datetime.now(timezone.utc)
        else:
            dt = acquisition_time

        start_dt = dt - timedelta(days=7)
        end_dt = dt + timedelta(days=1)
        
        start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        # 3. Construct OData Filter
        # Select SENTINEL-2, LANDSAT-8, or LANDSAT-9, intersecting our coordinate polygon, and within time frame
        filter_query = (
            f"(Collection/Name eq 'SENTINEL-2' or Collection/Name eq 'LANDSAT-8' or Collection/Name eq 'LANDSAT-9') and "
            f"OData.CSC.Intersects(area=geography'SRID=4326;{polygon_str}') and "
            f"ContentDate/Start gt {start_str} and "
            f"ContentDate/Start lt {end_str}"
        )

        params = {
            "$filter": filter_query,
            "$orderby": "ContentDate/Start desc",
            "$top": 1
        }

        try:
            logger.info(f"Querying Copernicus catalog for Sentinel-2 scene around ({lat:.4f}, {lon:.4f})...")
            response = requests.get(self.odata_url, params=params, timeout=20)
            
            if response.status_code != 200:
                logger.error(f"Copernicus OData query failed: {response.status_code} - {response.text}")
                return None, None
                
            data = response.json()
            products = data.get("value", [])
            
            if not products:
                logger.info("No Sentinel-2 products found intersecting these coordinates in the last 7 days.")
                return None, None
                
            latest_product = products[0]
            product_id = latest_product.get("Id")
            product_name = latest_product.get("Name")
            logger.info(f"Found product ID: {product_id} (Name: {product_name})")

            # 4. Fetch the Assets of the Product by expanding the Assets relation
            # Note: Do not wrap the product_id UUID in single quotes
            expand_url = f"{self.odata_url}({product_id})?$expand=Assets"
            headers = {"Authorization": f"Bearer {token}"}
            
            logger.info(f"Fetching assets list for product {product_id}...")
            asset_response = requests.get(expand_url, headers=headers, timeout=20)
            
            if asset_response.status_code != 200:
                logger.error(f"Failed to fetch assets metadata: {asset_response.status_code} - {asset_response.text}")
                return None, None
                
            product_details = asset_response.json()
            assets = product_details.get("Assets", [])
            
            # Find the Asset ID of type "QUICKLOOK"
            quicklook_asset_id = None
            for asset in assets:
                if str(asset.get("Type")).upper() == "QUICKLOOK":
                    quicklook_asset_id = asset.get("Id")
                    break
                    
            if not quicklook_asset_id:
                logger.warning("No QUICKLOOK asset found in the Sentinel-2 product assets list.")
                return None, None
                
            logger.info(f"Found Quicklook Asset ID: {quicklook_asset_id}")

            # 5. Download the Quicklook Asset using the Asset ID
            # Note: Do not wrap the quicklook_asset_id UUID in single quotes
            download_url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Assets({quicklook_asset_id})/$value"
            
            # Use requests.Session and handle redirects manually to prevent Authorization header stripping
            session = requests.Session()
            session.headers.update({"Authorization": f"Bearer {token}"})
            
            logger.info(f"Downloading quicklook asset {quicklook_asset_id} (handling redirects manually)...")
            ql_response = session.get(download_url, allow_redirects=False, timeout=25)
            
            # Follow redirects manually to keep Authorization header on internal hosts
            redirect_count = 0
            max_redirects = 5
            
            while ql_response.status_code in (301, 302, 303, 307, 308):
                if redirect_count >= max_redirects:
                    logger.error("Maximum redirect limit (5) exceeded.")
                    break
                
                # Resolve relative redirects
                location = ql_response.headers.get("Location")
                if not location:
                    logger.error("Redirect response is missing Location header.")
                    break
                    
                new_url = urljoin(ql_response.url, location)
                
                # Security check: Strip token if redirecting to a different/untrusted domain
                parsed_url = urlparse(new_url)
                is_internal = parsed_url.netloc == "dataspace.copernicus.eu" or parsed_url.netloc.endswith(".dataspace.copernicus.eu")
                if not is_internal:
                    logger.warning(f"Redirecting to external host: {parsed_url.netloc}. Stripping Bearer Token.")
                    session.headers.pop("Authorization", None)
                
                ql_response = session.get(new_url, allow_redirects=False, timeout=25)
                redirect_count += 1
            
            if ql_response.status_code == 200:
                # Save quicklook
                file_path = os.path.join(save_dir, f"{product_id}.png")
                with open(file_path, "wb") as f:
                    f.write(ql_response.content)
                logger.info(f"Saved Sentinel-2 quicklook to: {file_path}")
                return file_path, product_id
            else:
                logger.error(f"Failed to download quicklook: {ql_response.status_code} - {ql_response.text}")
                return None, None

        except Exception as e:
            logger.error(f"Exception fetching Sentinel-2 image: {e}")
            return None, None
