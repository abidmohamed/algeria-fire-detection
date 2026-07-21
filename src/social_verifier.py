import math
import logging
from datetime import datetime, timezone

logger = logging.getLogger("social_verifier")

class SocialVerifier:
    """
    Verification Engine for Social Media & Citizen Crowdsource Reports.
    Cross-references ground reports, public RSS/social posts, and news feeds
    against satellite thermal fire hotspots.
    """
    def __init__(self):
        self.keywords_ar = [
            "حريق", "حرائق", "غابة", "ألسنة النيران", "دخان",
            "الحماية المدنية", "تيزي وزو", "بجاية", "جيجل", "سكيكدة", "البويرة", "سطيف"
        ]
        self.keywords_fr = [
            "incendie", "feu", "forêt", "fumée", "protection civile",
            "tizi ouzou", "bejaia", "jijel", "skikda", "bouira", "setif"
        ]

    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        """Calculates distance between two coordinates in kilometers."""
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def is_fire_related_text(self, text):
        """Checks if a text string contains Arabic or French fire-related keywords."""
        if not text:
            return False
        text_lower = text.lower()
        has_ar = any(kw in text for kw in self.keywords_ar)
        has_fr = any(kw in text_lower for kw in self.keywords_fr)
        return has_ar or has_fr

    def match_reports_with_hotspot(self, hotspot_lat, hotspot_lon, hotspot_time, reports, max_dist_km=10.0, max_hours=3.0):
        """
        Finds all citizen or social reports matching a satellite hotspot coordinate within
        max_dist_km (default 10km) and max_hours (default 3 hours).
        """
        matches = []
        if not reports:
            return matches

        # Ensure hotspot_time is timezone-aware UTC
        if hotspot_time is not None and getattr(hotspot_time, "tzinfo", None) is None:
            hotspot_time = hotspot_time.replace(tzinfo=timezone.utc)

        for rep in reports:
            rep_lat = rep.get("latitude")
            rep_lon = rep.get("longitude")
            if rep_lat is None or rep_lon is None:
                continue

            dist_km = self.haversine(float(hotspot_lat), float(hotspot_lon), float(rep_lat), float(rep_lon))
            if dist_km > max_dist_km:
                continue

            rep_time = rep.get("created_at") or rep.get("acquisition_time")
            hours_diff = 0.0
            if rep_time is not None and hotspot_time is not None:
                if getattr(rep_time, "tzinfo", None) is None:
                    rep_time = rep_time.replace(tzinfo=timezone.utc)
                hours_diff = abs((hotspot_time - rep_time).total_seconds()) / 3600.0

            if hours_diff <= max_hours:
                matches.append({
                    "report_id": rep.get("id"),
                    "reporter_type": rep.get("reporter_type", "Citizen"),
                    "distance_km": round(dist_km, 2),
                    "hours_diff": round(hours_diff, 1),
                    "description": rep.get("description", ""),
                    "image_url": rep.get("image_url") or rep.get("photo_b64")
                })

        return matches

    def calculate_score_bonus(self, matched_reports):
        """
        Computes composite confidence score bonus based on matched citizen/social reports.
        - 1 matched report: +10 points
        - 2+ matched reports or Ranger confirmation: +15 points
        """
        if not matched_reports:
            return 0.0

        ranger_confirm = any(r.get("reporter_type") in ("Forest Ranger", "Civil Protection") for r in matched_reports)
        if ranger_confirm or len(matched_reports) >= 2:
            return 15.0
        return 10.0

    def format_telegram_summary(self, matched_reports):
        """Formats a human-readable summary string for Telegram alert messages."""
        if not matched_reports:
            return ""

        count = len(matched_reports)
        ranger_count = sum(1 for r in matched_reports if r.get("reporter_type") in ("Forest Ranger", "Civil Protection"))
        
        summary = f"\n👥 <b>Crowdsource Verification:</b> {count} report(s) matched within 10 km"
        if ranger_count > 0:
            summary += f" (including {ranger_count} Ranger/Civil Protection confirmation)"
        return summary
