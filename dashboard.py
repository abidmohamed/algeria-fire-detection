import os
import re
import json
import base64
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import streamlit as st
import folium
import streamlit.components.v1 as components
from src.config import GEOJSON_PATH
from src.db_client import DbClient

# Page configuration
st.set_page_config(
    page_title="Algeria Fire Watch - Early Warning Platform",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Premium Custom CSS Injection for Dark/Glassmorphism Theme
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');

/* Main app background */
.stApp {
    background-color: #0b0d10;
    background-image: radial-gradient(circle at 10% 20%, rgba(244, 63, 94, 0.06) 0%, rgba(0, 0, 0, 0) 90%), 
                      radial-gradient(circle at 90% 80%, rgba(245, 158, 11, 0.03) 0%, rgba(0, 0, 0, 0) 90%);
    color: #e2e8f0;
    font-family: 'Outfit', sans-serif;
}

/* Sidebar styling */
section[data-testid="stSidebar"] {
    background-color: #0e1217 !important;
    border-right: 1px solid rgba(255, 255, 255, 0.05) !important;
}

/* Custom cards styling */
.glass-card {
    background: rgba(18, 24, 32, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 16px;
    padding: 20px;
    backdrop-filter: blur(12px);
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.5);
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    margin-bottom: 20px;
}
.glass-card:hover {
    transform: translateY(-4px);
    border-color: rgba(244, 63, 94, 0.3);
    box-shadow: 0 12px 40px 0 rgba(244, 63, 94, 0.1);
}

/* Stat Text Styling */
.stat-title {
    font-size: 14px;
    font-weight: 500;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.stat-value {
    font-size: 38px;
    font-weight: 700;
    margin-top: 8px;
    background: linear-gradient(135deg, #f43f5e 0%, #f59e0b 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.stat-value-green {
    font-size: 38px;
    font-weight: 700;
    margin-top: 8px;
    background: linear-gradient(135deg, #10b981 0%, #3b82f6 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.stat-value-gray {
    font-size: 38px;
    font-weight: 700;
    margin-top: 8px;
    color: #64748b;
}

/* Subtitle and header formatting */
h1, h2, h3 {
    font-family: 'Outfit', sans-serif !important;
    font-weight: 700 !important;
}

/* Streamlit defaults replacement */
.stAlert {
    background: rgba(220, 38, 38, 0.1) !important;
    border: 1px solid rgba(220, 38, 38, 0.3) !important;
    color: #fca5a5 !important;
    border-radius: 12px !important;
}

.stProgress > div > div > div > div {
    background: linear-gradient(90deg, #f43f5e, #f59e0b) !important;
}
</style>
""", unsafe_allow_html=True)

# Add zero-dependency HTML auto-refresh (refreshes the page every 120 seconds)
st.markdown(
    '<meta http-equiv="refresh" content="120">',
    unsafe_allow_html=True
)

# ── Wilaya lookup based on approximate coordinate ranges for fire-prone provinces ──
WILAYA_BOUNDS = {
    "Tizi Ouzou": {"lat": (36.55, 36.95), "lon": (3.70, 4.35)},
    "Bejaia": {"lat": (36.45, 36.85), "lon": (4.70, 5.50)},
    "Jijel": {"lat": (36.55, 36.90), "lon": (5.50, 6.10)},
    "Bouira": {"lat": (36.15, 36.60), "lon": (3.40, 4.10)},
    "Setif": {"lat": (35.80, 36.50), "lon": (5.00, 5.90)},
    "Skikda": {"lat": (36.60, 37.10), "lon": (6.50, 7.30)},
    "Annaba": {"lat": (36.60, 37.10), "lon": (7.40, 8.00)},
    "El Tarf": {"lat": (36.50, 37.10), "lon": (8.00, 8.70)},
    "Medea": {"lat": (35.90, 36.45), "lon": (2.50, 3.50)},
    "Blida": {"lat": (36.30, 36.60), "lon": (2.60, 3.20)},
    "Tipaza": {"lat": (36.40, 36.70), "lon": (1.90, 2.60)},
    "Khenchela": {"lat": (35.00, 35.60), "lon": (6.90, 7.60)},
    "Guelma": {"lat": (36.20, 36.65), "lon": (7.00, 7.70)},
    "Constantine": {"lat": (36.20, 36.55), "lon": (6.40, 7.00)},
    "Batna": {"lat": (35.30, 35.80), "lon": (5.80, 6.60)},
    "Tlemcen": {"lat": (34.60, 35.20), "lon": (-1.80, -1.00)},
    "Chlef": {"lat": (36.00, 36.50), "lon": (0.90, 1.70)},
}

def get_wilaya(lat, lon):
    """Reverse-geocode coordinates to an approximate Algerian wilaya."""
    for name, bounds in WILAYA_BOUNDS.items():
        if bounds["lat"][0] <= lat <= bounds["lat"][1] and bounds["lon"][0] <= lon <= bounds["lon"][1]:
            return name
    if lat > 36.0:
        return "Northern Tell Atlas"
    elif lat > 34.0:
        return "Hauts Plateaux"
    else:
        return "Saharan Atlas"

# ── Generate mock data for visualization if database is not configured ──
def get_mock_data():
    now = datetime.now(timezone.utc)
    return [
        {
            "id": 1, "latitude": 36.712, "longitude": 4.045,
            "frp": 124.5, "confidence": 92,
            "acquisition_time": now - timedelta(hours=2),
            "status": "CONFIRMED", "temp": 41.2, "humidity": 14.5,
            "wind_speed": 32.4, "wind_direction": 185.0,
            "risk_score": 94.0, "product_id": "S2A_MSIL2A_20260717T101221",
            "quicklook_url": None, "telegram_message_id": "12345"
        },
        {
            "id": 2, "latitude": 36.758, "longitude": 5.081,
            "frp": 68.2, "confidence": 78,
            "acquisition_time": now - timedelta(hours=4),
            "status": "CONFIRMED", "temp": 39.5, "humidity": 18.0,
            "wind_speed": 22.0, "wind_direction": 170.0,
            "risk_score": 82.0, "product_id": "S2B_MSIL2A_20260717T100803",
            "quicklook_url": None, "telegram_message_id": "12346"
        },
        {
            "id": 3, "latitude": 36.802, "longitude": 5.761,
            "frp": 25.1, "confidence": 62,
            "acquisition_time": now - timedelta(minutes=45),
            "status": "PENDING", "temp": 38.0, "humidity": 21.0,
            "wind_speed": 18.5, "wind_direction": 110.0,
            "risk_score": 45.0, "product_id": None,
            "quicklook_url": None, "telegram_message_id": None
        },
        {
            "id": 4, "latitude": 32.894, "longitude": -0.492,
            "frp": 45.8, "confidence": 85,
            "acquisition_time": now - timedelta(hours=8),
            "status": "CONFIRMED", "temp": 42.0, "humidity": 12.0,
            "wind_speed": 28.0, "wind_direction": 200.0,
            "risk_score": 96.0, "product_id": "S2A_MSIL2A_20260717T101221",
            "quicklook_url": None, "telegram_message_id": "12347"
        },
        {
            "id": 5, "latitude": 36.425, "longitude": 2.871,
            "frp": 12.4, "confidence": 55,
            "acquisition_time": now - timedelta(hours=10),
            "status": "FALSE_POSITIVE", "temp": 36.8, "humidity": 24.5,
            "wind_speed": 12.0, "wind_direction": 90.0,
            "risk_score": 28.0, "product_id": None,
            "quicklook_url": None, "telegram_message_id": None
        }
    ]

# Cached function for database queries to prevent slamming Supabase on every interaction
@st.cache_data(ttl=30)
def fetch_fires_from_db(db_url):
    try:
        client = DbClient(db_url)
        return client.get_all_fires(limit=300)
    except Exception as e:
        logging.getLogger("dashboard").error(f"Failed to fetch fires from DB: {e}", exc_info=True)
        return None

# ── Fetch data from DB or fallback ──
db_configured = False
fires = []

db_client = DbClient()
if db_client.db_url and "change-me" not in db_client.db_url:
    try:
        res = fetch_fires_from_db(db_client.db_url)
        if res is not None:
            fires = res
            db_configured = True
        else:
            db_configured = False
    except Exception as e:
        logging.getLogger("dashboard").error(f"Database connection error: {e}", exc_info=True)
        db_configured = False

if not db_configured:
    fires = get_mock_data()

df = pd.DataFrame(fires)

# Ensure acquisition_time is in datetime format
if not df.empty and "acquisition_time" in df.columns:
    df["acquisition_time"] = pd.to_datetime(df["acquisition_time"], utc=True)

# Add wilaya column
if not df.empty:
    df["wilaya"] = df.apply(lambda r: get_wilaya(float(r["latitude"]), float(r["longitude"])), axis=1)

# ── Sidebar ──
st.sidebar.markdown("<h2 style='text-align: center;'>🔥 Algeria Fire Watch</h2>", unsafe_allow_html=True)
st.sidebar.markdown("---")

if db_configured:
    st.sidebar.success("Connected to Supabase.")
else:
    st.sidebar.warning("Using simulated demo data.")

st.sidebar.markdown("---")
st.sidebar.markdown("""
<div style="font-size: 12px; color: #64748b;">
    <b>Data Sources</b><br/>
    NASA FIRMS (VIIRS 3-sat)<br/>
    Copernicus Sentinel-2<br/>
    Open-Meteo Weather API<br/><br/>
    <b>Pipeline v2</b><br/>
    Multi-sensor fusion<br/>
    DBSCAN clustering<br/>
    Composite scoring (0-100)
</div>
""", unsafe_allow_html=True)

# ── Header ──
st.title("🇩🇿 ALGERIA FOREST FIRE DETECTION PLATFORM")
st.markdown("##### Real-Time Satellite Active Fire Trigger & AI Verification Early Warning System")
st.markdown("---")

# ── Stats Row ──
col1, col2, col3, col4 = st.columns(4)

total_fires = len(df[df["status"] == "CONFIRMED"]) if not df.empty else 0
pending_fires = len(df[df["status"] == "PENDING"]) if not df.empty else 0
false_positives = len(df[df["status"] == "FALSE_POSITIVE"]) if not df.empty else 0

sirocco_regions = 0
if not df.empty and "temp" in df.columns and "wind_direction" in df.columns:
    sirocco_regions = len(df[
        (df["temp"] > 38) & 
        (df["wind_direction"] >= 135) & 
        (df["wind_direction"] <= 225)
    ])

with col1:
    st.markdown(f"""
    <div class="glass-card">
        <div class="stat-title">Confirmed Forest Fires</div>
        <div class="stat-value">{total_fires}</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="glass-card">
        <div class="stat-title">Awaiting Verification</div>
        <div class="stat-value-green" style="background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">{pending_fires}</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown(f"""
    <div class="glass-card">
        <div class="stat-title">Saharan False Alarms Filtered</div>
        <div class="stat-value-gray">{false_positives}</div>
    </div>
    """, unsafe_allow_html=True)

with col4:
    st.markdown(f"""
    <div class="glass-card">
        <div class="stat-title">Active Sirocco Fire Risks</div>
        <div class="stat-value" style="background: linear-gradient(135deg, #ef4444 0%, #b91c1c 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">{sirocco_regions}</div>
    </div>
    """, unsafe_allow_html=True)

# ── Map Filters (inline row above the map) ──
st.subheader("🔥 Active Fire Location Map")

fcol1, fcol2, fcol3 = st.columns([2, 2, 1])

with fcol1:
    status_filter = st.multiselect(
        "Alert Status",
        options=["CONFIRMED", "PENDING", "FALSE_POSITIVE"],
        default=["CONFIRMED", "PENDING"],
        key="map_status"
    )

with fcol2:
    available_wilayas = sorted(df["wilaya"].unique().tolist()) if not df.empty and "wilaya" in df.columns else []
    wilaya_map_filter = st.multiselect(
        "Wilaya (Province)",
        options=available_wilayas,
        default=[],
        key="map_wilaya",
        placeholder="All wilayas"
    )

with fcol3:
    min_frp = st.slider("Min FRP (MW)", 0.0, 300.0, 0.0, 10.0, key="map_frp")

# Apply map filters
if not df.empty:
    df_filtered = df[df["status"].isin(status_filter)]
    df_filtered = df_filtered[df_filtered["frp"] >= min_frp]
    if wilaya_map_filter:
        df_filtered = df_filtered[df_filtered["wilaya"].isin(wilaya_map_filter)]
else:
    df_filtered = pd.DataFrame()

# ── Full-Width Map ──
map_center = [35.5, 4.0]
zoom_start = 7
if not df_filtered.empty:
    df_sorted = df_filtered.sort_values(by="acquisition_time", ascending=False)
    latest_row = df_sorted.iloc[0]
    if pd.notna(latest_row["latitude"]) and pd.notna(latest_row["longitude"]):
        map_center = [float(latest_row["latitude"]), float(latest_row["longitude"])]
        zoom_start = 9

m = folium.Map(location=map_center, zoom_start=zoom_start, tiles="cartodbpositron")

# Forest boundary GeoJSON overlay
if GEOJSON_PATH.exists():
    try:
        with open(GEOJSON_PATH, "r") as f:
            geojson_data = json.load(f)
        folium.GeoJson(
            geojson_data,
            name="Forest Hazard Risk Zone",
            style_function=lambda x: {
                "fillColor": "#10b981",
                "color": "#059669",
                "weight": 2,
                "fillOpacity": 0.05
            }
        ).add_to(m)
    except Exception as e:
        st.error(f"Error drawing boundary: {e}")

# Plot fire markers
if not df_filtered.empty:
    for idx, row in df_filtered.iterrows():
        if row["status"] == "CONFIRMED":
            color = "#ef4444"
            status_lbl = "Confirmed Forest Fire"
        elif row["status"] == "PENDING":
            color = "#f59e0b"
            status_lbl = "Pending Sentinel Verification"
        else:
            color = "#64748b"
            status_lbl = "False Positive Filtered"

        formatted_time = row["acquisition_time"].strftime("%Y-%m-%d %H:%M UTC") if pd.notna(row["acquisition_time"]) else "N/A"
        wilaya_name = row.get("wilaya", "Unknown")

        popup_html = f"""
        <div style="font-family: 'Outfit', sans-serif; width: 230px; color:#1e293b;">
            <h4 style="margin: 0 0 6px 0; color:#b91c1c;">{status_lbl}</h4>
            <hr style="margin: 4px 0 6px 0; border: 0; border-top:1px solid #cbd5e1;"/>
            <b>Wilaya:</b> {wilaya_name}<br/>
            <b>Coords:</b> {row['latitude']:.4f}, {row['longitude']:.4f}<br/>
            <b>FRP:</b> {row['frp']:.1f} MW<br/>
            <b>Confidence:</b> {row['confidence']}%<br/>
            <b>Time:</b> {formatted_time}<br/>
        """

        # Weather block
        temp_str = f"{row['temp']:.1f} C" if pd.notna(row.get('temp')) else "N/A"
        humidity_str = f"{row['humidity']:.1f}%" if pd.notna(row.get('humidity')) else "N/A"
        wind_str = f"{row['wind_speed']:.1f} km/h" if pd.notna(row.get('wind_speed')) else "N/A"
        risk_str = f"{row['risk_score']:.0f}/100" if pd.notna(row.get('risk_score')) else "N/A"

        if temp_str != "N/A" or humidity_str != "N/A" or wind_str != "N/A":
            popup_html += f"""
            <hr style="margin: 6px 0 6px 0; border: 0; border-top:1px dashed #cbd5e1;"/>
            <b>Temp:</b> {temp_str}<br/>
            <b>Humidity:</b> {humidity_str}<br/>
            <b>Wind:</b> {wind_str}<br/>
            <b>Risk:</b> {risk_str}<br/>
            """

        # Quicklook image (base64 for local files)
        if "quicklook_url" in row and pd.notna(row["quicklook_url"]) and row["quicklook_url"] is not None:
            quicklook_path = str(row["quicklook_url"])
            img_data_uri = None

            if not quicklook_path.startswith("http"):
                local_path = Path(quicklook_path)
                if local_path.exists():
                    try:
                        with open(local_path, "rb") as img_file:
                            b64 = base64.b64encode(img_file.read()).decode("utf-8")
                            ext = local_path.suffix.lower().lstrip(".")
                            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/png")
                            img_data_uri = f"data:{mime};base64,{b64}"
                    except Exception:
                        pass
            else:
                img_data_uri = quicklook_path

            if img_data_uri:
                popup_html += f"""
                <hr style="margin: 6px 0 6px 0; border: 0; border-top:1px solid #cbd5e1;"/>
                <b>Sentinel-2 Quicklook:</b><br/>
                <img src="{img_data_uri}" style="width:100%; border-radius:6px; margin-top:4px; border:1px solid #94a3b8;"/>
                """

        popup_html += "</div>"

        # Sanitize for Folium/JS
        clean_popup_html = popup_html.replace("\n", "").replace("\r", "").replace("'", "&#39;")

        if pd.isna(row["latitude"]) or pd.isna(row["longitude"]):
            continue

        folium.CircleMarker(
            location=[float(row["latitude"]), float(row["longitude"])],
            radius=8 if row["status"] == "CONFIRMED" else 6,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.6,
            popup=folium.Popup(clean_popup_html, max_width=260)
        ).add_to(m)

# Render map with JS escape sanitization
map_html = m._repr_html_()
map_html = re.sub(r'(?<!\\)\\([0-9])', r'\\\\\1', map_html)
components.html(map_html, height=700, scrolling=False)

# ── Real-Time Warnings Section ──
st.markdown("---")

wcol_header, wcol_date, wcol_wilaya = st.columns([2, 2, 2])

with wcol_header:
    st.subheader("🚨 Real-Time Warnings")

with wcol_date:
    if not df_filtered.empty and "acquisition_time" in df_filtered.columns:
        min_date = df_filtered["acquisition_time"].min().date()
        max_date = df_filtered["acquisition_time"].max().date()
        date_range = st.date_input(
            "Date Range",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
            key="warn_date"
        )
    else:
        date_range = None

with wcol_wilaya:
    warn_wilayas = sorted(df_filtered["wilaya"].unique().tolist()) if not df_filtered.empty and "wilaya" in df_filtered.columns else []
    warn_wilaya_sel = st.multiselect(
        "Filter by Wilaya",
        options=warn_wilayas,
        default=[],
        key="warn_wilaya",
        placeholder="All wilayas"
    )

# Apply warning-level filters
df_warnings = df_filtered.copy() if not df_filtered.empty else pd.DataFrame()

if not df_warnings.empty and date_range and len(date_range) == 2:
    from datetime import time as dt_time
    start_dt = pd.Timestamp.combine(date_range[0], dt_time.min).tz_localize("UTC")
    end_dt = pd.Timestamp.combine(date_range[1], dt_time.max).tz_localize("UTC")
    df_warnings = df_warnings[
        (df_warnings["acquisition_time"] >= start_dt) &
        (df_warnings["acquisition_time"] <= end_dt)
    ]

if not df_warnings.empty and warn_wilaya_sel:
    df_warnings = df_warnings[df_warnings["wilaya"].isin(warn_wilaya_sel)]

# Display warning cards in 2-column grid
if df_warnings.empty:
    st.info("No active fire triggers found for selected criteria.")
else:
    df_display = df_warnings.sort_values(by="acquisition_time", ascending=False)
    warn_cols = st.columns(2)

    for card_idx, (idx, row) in enumerate(df_display.iterrows()):
        if row["status"] == "CONFIRMED":
            bg_color = "rgba(239, 68, 68, 0.08)"
            border_color = "rgba(239, 68, 68, 0.25)"
            title = f"🔴 Fire Confirmed - FRP {row['frp']:.1f} MW"
        elif row["status"] == "PENDING":
            bg_color = "rgba(245, 158, 11, 0.08)"
            border_color = "rgba(245, 158, 11, 0.25)"
            title = "🟡 Thermal Anomaly Awaiting Verification"
        else:
            bg_color = "rgba(100, 116, 139, 0.08)"
            border_color = "rgba(100, 116, 139, 0.25)"
            title = "⚪ False Alarm Filtered"

        formatted_time = row["acquisition_time"].strftime("%Y-%m-%d %H:%M UTC") if pd.notna(row["acquisition_time"]) else "N/A"
        risk_val = f"{row['risk_score']:.0f}/100" if pd.notna(row.get('risk_score')) else "N/A"
        wilaya_name = row.get("wilaya", "Unknown")

        with warn_cols[card_idx % 2]:
            st.markdown(f"""
            <div style="background-color: {bg_color}; border: 1px solid {border_color}; border-radius: 12px; padding: 14px; margin-bottom: 12px;">
                <div style="font-weight: 600; font-size: 15px; margin-bottom: 4px;">{title}</div>
                <div style="font-size: 13px; color: #94a3b8;">
                    <b>Wilaya:</b> {wilaya_name}<br/>
                    <b>Coordinates:</b> {row['latitude']:.4f}, {row['longitude']:.4f}<br/>
                    <b>Detection:</b> {formatted_time}<br/>
                    <b>Risk Score:</b> {risk_val}
                </div>
            </div>
            """, unsafe_allow_html=True)

# Footer
st.markdown("---")
st.markdown(
    "<p style='text-align: center; color: #64748b; font-size: 12px;'>"
    "Algeria Forest Fire detection and early warning platform. "
    "Data Source: NASA FIRMS (VIIRS/MODIS) | Copernicus Sentinel-2 | Open-Meteo. "
    "</p>",
    unsafe_allow_html=True
)
