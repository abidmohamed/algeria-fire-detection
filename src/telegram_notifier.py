import os
import html
import time
import requests
from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

class TelegramNotifier:
    def __init__(self, token=TELEGRAM_BOT_TOKEN, chat_id=TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        
    def send_message(self, text, parse_mode="HTML"):
        """Sends a text message to the configured Telegram chat/channel with 429 rate limit handling."""
        if not self.token or "change-me" in str(self.token).lower() or not self.chat_id or "change-me" in str(self.chat_id).lower():
            print("[WARNING] Telegram credentials not configured. Skipping message notification.")
            return False
            
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False
        }
        
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, timeout=15)
                    
                if response.status_code == 200:
                    print("[INFO] Telegram text alert sent successfully.")
                    return True
                elif response.status_code == 429:
                    res_json = response.json()
                    retry_after = int(res_json.get("parameters", {}).get("retry_after", 5))
                    print(f"[WARNING] Telegram rate limit hit. Sleeping for {retry_after}s (Attempt {attempt+1}/3)...")
                    time.sleep(retry_after)
                else:
                    print(f"[ERROR] Failed to send Telegram message: {response.status_code} - {response.text}")
                    return False
            except Exception as e:
                print(f"[ERROR] Exception sending Telegram message: {e}")
                time.sleep(2)
                
        return False
            
    def send_photo(self, photo_path, caption="", parse_mode="HTML"):
        """Sends a photo with a caption to the configured Telegram chat/channel with rate limit handling."""
        if not self.token or "change-me" in str(self.token).lower() or not self.chat_id or "change-me" in str(self.chat_id).lower():
            print("[WARNING] Telegram credentials not configured. Skipping photo notification.")
            return False
            
        if not os.path.exists(photo_path):
            print(f"[ERROR] Photo file not found at {photo_path}. Sending text alert instead.")
            return self.send_message(caption, parse_mode=parse_mode)
            
        url = f"{self.base_url}/sendPhoto"
        
        for attempt in range(3):
            try:
                with open(photo_path, "rb") as photo_file:
                    files = {"photo": photo_file}
                    data = {
                        "chat_id": self.chat_id,
                        "caption": caption,
                        "parse_mode": parse_mode
                    }
                    
                    print(f"[INFO] Uploading and sending photo {photo_path} to Telegram...")
                    response = requests.post(url, files=files, data=data, timeout=30)
                    
                    if response.status_code == 200:
                        print("[INFO] Telegram photo alert sent successfully.")
                        return True
                    elif response.status_code == 429:
                        res_json = response.json()
                        retry_after = int(res_json.get("parameters", {}).get("retry_after", 5))
                        print(f"[WARNING] Telegram rate limit hit on photo upload. Sleeping for {retry_after}s...")
                        time.sleep(retry_after)
                    else:
                        print(f"[ERROR] Failed to send Telegram photo: {response.status_code} - {response.text}")
                        return self.send_message(caption, parse_mode=parse_mode)
            except Exception as e:
                print(f"[ERROR] Exception sending Telegram photo: {e}")
                time.sleep(2)
                
        return self.send_message(caption, parse_mode=parse_mode)
            
    @staticmethod
    def get_wind_direction_cardinal(deg):
        """Converts wind degrees to cardinal directions."""
        if deg is None:
            return "N/A"
        deg = deg % 360
        val = int((deg / 22.5) + 0.5)
        arr = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        return arr[val % 16]

    def format_fire_alert(self, lat, lon, frp, confidence, acq_time, status, 
                           temp=None, humidity=None, wind_speed=None, wind_direction=None, 
                           risk_score=None, is_update=False, bypass_reason=None):
        """Formats the fire metrics into a beautiful HTML string for Telegram."""
        
        # Escape dynamic string parameters to avoid HTML entity parsing errors in Telegram API
        status = html.escape(str(status))
        acq_time = html.escape(str(acq_time))
        
        emoji_status = "🔥" if status == "CONFIRMED" else "⚠️"
        title = "ONGOING ACTIVE FIRE UPDATE" if is_update else "NEW FOREST FIRE DETECTED"
        
        status_text = f"<b>{status}</b>"
        if status == "CONFIRMED":
            if bypass_reason:
                status_text += " (Auto-Confirmed by FRP/Cluster ⚡)"
            else:
                status_text += " (Smoke Plume Verified 💨)"
        elif status == "PENDING":
            status_text += " (Awaiting Satellite Verification ⏳)"
        elif status == "FALSE_POSITIVE":
            status_text += " (False Alarm Filtered 🛡️)"
            
        msg = [
            f"{emoji_status} <b>{title} - ALGERIA</b> {emoji_status}\n",
            f"<b>Status:</b> {status_text}",
            f"<b>Location:</b> Latitude {lat:.4f}, Longitude {lon:.4f}",
            f"<b>Fire Radiative Power (FRP):</b> {frp:.1f} MW",
            f"<b>Satellite Confidence:</b> {confidence}%",
            f"<b>Detection Time:</b> {acq_time}\n"
        ]
        
        # Add Weather parameters if available
        if temp is not None or humidity is not None or wind_speed is not None:
            msg.append("🌤️ <b>Local Weather & Risk Assessment:</b>")
            if temp is not None:
                msg.append(f"• <b>Temperature:</b> {temp:.1f}°C")
            if humidity is not None:
                msg.append(f"• <b>Relative Humidity:</b> {humidity:.1f}%")
            if wind_speed is not None:
                cardinal = self.get_wind_direction_cardinal(wind_direction)
                deg_lbl = f"{wind_direction:.1f}°" if wind_direction is not None else "N/A"
                msg.append(f"• <b>Wind:</b> {wind_speed:.1f} km/h from {cardinal} ({deg_lbl})")
            
            # Sirocco Wind check: South winds are roughly 135° to 225°
            is_sirocco = False
            if temp is not None and temp > 38 and humidity is not None and humidity < 25:
                if wind_direction is not None and (135 <= wind_direction <= 225):
                    is_sirocco = True
            
            if is_sirocco:
                msg.append("⚠️ <b>Sirocco wind effect active (Extremely dry, hot southern winds)</b>")
                
            if risk_score is not None:
                risk_level = "LOW"
                if risk_score >= 80:
                    risk_level = "EXTREME 🚨"
                elif risk_score >= 50:
                    risk_level = "HIGH ⚠️"
                elif risk_score >= 25:
                    risk_level = "MODERATE"
                msg.append(f"• <b>Fire Weather Risk:</b> {risk_score:.0f}/100 ({risk_level})")
            
            msg.append("") # newline
            
        if bypass_reason:
            msg.append(f"{bypass_reason}\n")
            
        msg.append(f"📍 <a href='https://www.google.com/maps/search/?api=1&query={lat},{lon}'>Open in Google Maps</a>")
        
        return "\n".join(msg)
