import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Get the base directory of the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(dotenv_path=BASE_DIR / ".env")

# Centralized Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE_DIR / "pipeline.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("config")

def get_secret(key, default=""):
    """
    Attempts to retrieve a secret from Streamlit secrets (for cloud deployment),
    falling back to standard environment variables.
    """
    try:
        import streamlit as st
        # Check if the secret exists in Streamlit secrets
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    
    return os.getenv(key, default)

# NASA FIRMS API Configurations
NASA_FIRMS_KEY = get_secret("NASA_FIRMS_KEY", "")

# Telegram Bot Alert Configurations
TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = get_secret("TELEGRAM_CHAT_ID", "")

# Copernicus Data Space Ecosystem (CDSE) Credentials
CDSE_USERNAME = get_secret("CDSE_USERNAME", "")
CDSE_PASSWORD = get_secret("CDSE_PASSWORD", "")

# Supabase / PostgreSQL Connection String
DATABASE_URL = get_secret("DATABASE_URL", "")

# Path to the geojson boundary file
GEOJSON_PATH = BASE_DIR / "algeria_forest_zone.geojson"

def validate_config(check_db=True, check_copernicus=True):
    """Validates that crucial environment variables are loaded."""
    missing = []
    if not NASA_FIRMS_KEY:
        missing.append("NASA_FIRMS_KEY")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
        
    if check_db:
        if not DATABASE_URL or "change-me" in DATABASE_URL:
            missing.append("DATABASE_URL")
            
    if check_copernicus:
        if not CDSE_USERNAME:
            missing.append("CDSE_USERNAME")
        if not CDSE_PASSWORD:
            missing.append("CDSE_PASSWORD")
            
    if missing:
        logger.error(f"CRITICAL CONFIGURATION FAILURE: Missing parameters: {', '.join(missing)}")
        logger.error("Please configure these in your .env file, Streamlit Secrets, or GitHub Secrets environment.")
        return False
        
    logger.info("Configuration validated successfully.")
    return True
