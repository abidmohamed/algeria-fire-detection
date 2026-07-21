import math
import logging
from datetime import datetime, timezone
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
from src.config import DATABASE_URL

logger = logging.getLogger("db_client")

class DbClient:
    _pools = {}  # Map: db_url -> ThreadedConnectionPool
    
    def __init__(self, db_url=DATABASE_URL):
        self.db_url = db_url
        self._init_pool()
        
    def _init_pool(self):
        """Initializes a thread-safe connection pool if not already initialized."""
        if self.db_url and "change-me" not in self.db_url:
            if self.db_url not in DbClient._pools:
                try:
                    # Initialize pool with a minimum of 1 and maximum of 15 connections
                    # Configure connection timeout (10s) and TCP keepalive parameters
                    DbClient._pools[self.db_url] = ThreadedConnectionPool(
                        1, 15, self.db_url, connect_timeout=10,
                        keepalives=1, keepalives_idle=30, keepalives_interval=5, keepalives_count=3
                    )
                    logger.info("Database connection pool initialized successfully.")
                except Exception as e:
                    logger.warning(f"Failed to initialize connection pool: {e}. Direct connections will be used.")

    def _get_connection(self):
        """Retrieves a connection from the pool or creates a new one if pool is unavailable."""
        if not self.db_url or "change-me" in self.db_url:
            raise ValueError("DATABASE_URL is not set or unconfigured. Cannot connect to database.")
            
        pool = DbClient._pools.get(self.db_url)
        if pool:
            try:
                return pool.getconn()
            except Exception as e:
                logger.warning(f"Connection pool retrieval failed: {e}. Falling back to direct connection.")
                
        # Direct fallback connection if pool fails or is uninitialized
        return psycopg2.connect(
            self.db_url, connect_timeout=10,
            keepalives=1, keepalives_idle=30, keepalives_interval=5, keepalives_count=3
        )

    def _release_connection(self, conn):
        """
        Releases a connection. Attempts to return it to the ThreadedConnectionPool first.
        If it throws an exception (e.g. if the connection did not originate from the pool or the
        pool is exhausted), it falls back to closing the connection directly.
        """
        if conn is None:
            return
            
        pool = DbClient._pools.get(self.db_url)
        if pool:
            try:
                pool.putconn(conn)
                return
            except Exception:
                pass
                
        try:
            conn.close()
        except Exception:
            pass

    @staticmethod
    def haversine_distance(lat1, lon1, lat2, lon2):
        """Calculate the great circle distance between two points on the earth in km."""
        R = 6371.0  # Radius of Earth in kilometers
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def find_exact_processed_hotspot(self, lat, lon, acq_time):
        """
        Checks if this exact satellite detection (same location within 200m and same timestamp) 
        has already been processed in a prior execution. Prevents redundant work on duplicate runs.
        """
        if not self.db_url or "change-me" in self.db_url:
            return None
            
        # Ensure acq_time is timezone-aware (assume UTC if naive)
        if acq_time is not None and (acq_time.tzinfo is None or acq_time.tzinfo.utcoffset(acq_time) is None):
            acq_time = acq_time.replace(tzinfo=timezone.utc)
            
        conn = None
        try:
            conn = self._get_connection()
            
            # PostGIS query (using geom as geography natively)
            try:
                query = """
                    SELECT id 
                    FROM fires 
                    WHERE ST_DWithin(
                        geom, 
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 
                        200
                    ) AND ABS(EXTRACT(EPOCH FROM (acquisition_time - %s::timestamp with time zone))) < 60
                    LIMIT 1;
                """
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(query, (lon, lat, acq_time))
                    return cur.fetchone()
            except Exception as e:
                if conn:
                    conn.rollback()
                # Fallback check in Python
                query = """
                    SELECT id, latitude, longitude, acquisition_time 
                    FROM fires 
                    WHERE acquisition_time >= %s::timestamp with time zone - INTERVAL '60 seconds'
                      AND acquisition_time <= %s::timestamp with time zone + INTERVAL '60 seconds';
                """
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(query, (acq_time, acq_time))
                    candidates = cur.fetchall()
                
                for cand in candidates:
                    dist = self.haversine_distance(lat, lon, cand["latitude"], cand["longitude"])
                    if dist <= 0.2:  # 200 meters
                        return cand
                return None
        except Exception as e:
            logger.error(f"Database error during exact hotspot check: {e}")
            return None
        finally:
            self._release_connection(conn)

    def find_recent_nearby_fire(self, lat, lon, max_distance_km=2.0, max_hours=12, reference_time=None):
        """
        Looks for a fire in the database within max_distance_km and within max_hours of reference_time.
        If reference_time is None, defaults to the current database time (NOW()).
        """
        if not self.db_url or "change-me" in self.db_url:
            logger.warning("Database not configured. Skipping duplicate check.")
            return None
            
        conn = None
        ref_time = reference_time if reference_time is not None else datetime.now(timezone.utc)
        # Ensure ref_time is timezone-aware (assume UTC if naive)
        if ref_time.tzinfo is None or ref_time.tzinfo.utcoffset(ref_time) is None:
            ref_time = ref_time.replace(tzinfo=timezone.utc)
            
        try:
            conn = self._get_connection()
            distance_meters = max_distance_km * 1000
            
            # 1. Attempt PostGIS spatial query (using geom as geography natively)
            try:
                query = """
                    SELECT id, latitude, longitude, frp, confidence, acquisition_time, status, telegram_message_id, last_notified_at
                    FROM fires
                    WHERE ST_DWithin(
                        geom, 
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 
                        %s
                    ) AND acquisition_time >= %s::timestamp with time zone - %s::interval
                      AND acquisition_time <= %s::timestamp with time zone
                    ORDER BY acquisition_time DESC
                    LIMIT 1;
                """
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(query, (lon, lat, distance_meters, ref_time, f"{max_hours} hours", ref_time))
                    result = cur.fetchone()
                return result
            except Exception as e:
                # If PostGIS extension fails or is missing, rollback transaction and try fallback
                if conn:
                    conn.rollback()
                logger.warning(f"PostGIS spatial query failed: {e}. Falling back to Haversine check in Python.")
                
                query = """
                    SELECT id, latitude, longitude, frp, confidence, acquisition_time, status, telegram_message_id, last_notified_at
                    FROM fires
                    WHERE acquisition_time >= %s::timestamp with time zone - %s::interval
                      AND acquisition_time <= %s::timestamp with time zone
                    ORDER BY acquisition_time DESC;
                """
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(query, (ref_time, f"{max_hours} hours", ref_time))
                    candidates = cur.fetchall()
                
                # Check distances in Python
                for cand in candidates:
                    dist = self.haversine_distance(lat, lon, cand["latitude"], cand["longitude"])
                    if dist <= max_distance_km:
                        return cand
                return None
                
        except Exception as e:
            logger.error(f"Database error during duplicate check: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return None
        finally:
            self._release_connection(conn)
 
    def save_fire(self, fire_data):
        """
        Inserts a new fire detection record.
        """
        if not self.db_url or "change-me" in self.db_url:
            logger.warning("Database not configured. Skipping save.")
            return None
            
        conn = None
        try:
            conn = self._get_connection()
            query = """
                INSERT INTO fires (
                    latitude, longitude, frp, confidence, acquisition_time, status, source,
                    temp, humidity, wind_speed, wind_direction, risk_score,
                    brightness, bright_t31, daynight, scan_pixel,
                    cluster_id, cluster_size, composite_score, days_since_rain
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """
            with conn.cursor() as cur:
                cur.execute(query, (
                    fire_data["latitude"],
                    fire_data["longitude"],
                    fire_data["frp"],
                    fire_data["confidence"],
                    fire_data["acquisition_time"],
                    fire_data.get("status", "PENDING"),
                    fire_data.get("source", "VIIRS_SNPP_NRT"),
                    fire_data.get("temp"),
                    fire_data.get("humidity"),
                    fire_data.get("wind_speed"),
                    fire_data.get("wind_direction"),
                    fire_data.get("risk_score"),
                    fire_data.get("brightness"),
                    fire_data.get("bright_t31"),
                    fire_data.get("daynight"),
                    fire_data.get("scan_pixel"),
                    fire_data.get("cluster_id"),
                    fire_data.get("cluster_size"),
                    fire_data.get("composite_score"),
                    fire_data.get("days_since_rain"),
                ))
                fire_id = cur.fetchone()[0]
            conn.commit()
            logger.info(f"Saved fire detection to database with ID: {fire_id}")
            return fire_id
        except Exception as e:
            logger.error(f"Failed to save fire to database: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return None
        finally:
            self._release_connection(conn)
 
    def update_fire_status(self, fire_id, status, product_id=None, quicklook_url=None, telegram_message_id=None):
        """Updates status and verified data of an existing fire."""
        if fire_id is None:
            logger.warning("Cannot update fire status: fire_id is None.")
            return False
            
        if not self.db_url or "change-me" in self.db_url:
            return False
            
        conn = None
        try:
            conn = self._get_connection()
            
            fields = ["status = %s"]
            params = [status]
            
            if product_id is not None:
                fields.append("product_id = %s")
                params.append(product_id)
            if quicklook_url is not None:
                fields.append("quicklook_url = %s")
                params.append(quicklook_url)
            if telegram_message_id is not None:
                fields.append("telegram_message_id = %s")
                params.append(telegram_message_id)
                
            params.append(fire_id)
            query = f"UPDATE fires SET {', '.join(fields)} WHERE id = %s;"
            
            with conn.cursor() as cur:
                cur.execute(query, params)
            conn.commit()
            logger.info(f"Updated fire ID {fire_id} to status: {status}")
            return True
        except Exception as e:
            logger.error(f"Failed to update fire status: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return False
        finally:
            self._release_connection(conn)

    def update_fire_full(self, fire_id, fire_data):
        """
        Comprehensive update of an existing fire record with all new metadata.
        Used when upgrading a PENDING fire with fresh detection data instead of creating a duplicate.
        """
        if fire_id is None or not self.db_url or "change-me" in self.db_url:
            return False

        conn = None
        try:
            conn = self._get_connection()
            query = """
                UPDATE fires SET
                    frp = GREATEST(frp, %s),
                    confidence = GREATEST(confidence, %s),
                    acquisition_time = %s,
                    status = %s,
                    source = %s,
                    temp = COALESCE(%s, temp),
                    humidity = COALESCE(%s, humidity),
                    wind_speed = COALESCE(%s, wind_speed),
                    wind_direction = COALESCE(%s, wind_direction),
                    risk_score = COALESCE(%s, risk_score),
                    brightness = COALESCE(%s, brightness),
                    bright_t31 = COALESCE(%s, bright_t31),
                    daynight = COALESCE(%s, daynight),
                    scan_pixel = COALESCE(%s, scan_pixel),
                    cluster_id = %s,
                    cluster_size = %s,
                    days_since_rain = COALESCE(%s, days_since_rain),
                    updated_at = NOW()
                WHERE id = %s;
            """
            with conn.cursor() as cur:
                cur.execute(query, (
                    fire_data.get("frp"),
                    fire_data.get("confidence"),
                    fire_data.get("acquisition_time"),
                    fire_data.get("status", "PENDING"),
                    fire_data.get("source", "VIIRS_SNPP_NRT"),
                    fire_data.get("temp"),
                    fire_data.get("humidity"),
                    fire_data.get("wind_speed"),
                    fire_data.get("wind_direction"),
                    fire_data.get("risk_score"),
                    fire_data.get("brightness"),
                    fire_data.get("bright_t31"),
                    fire_data.get("daynight"),
                    fire_data.get("scan_pixel"),
                    fire_data.get("cluster_id"),
                    fire_data.get("cluster_size"),
                    fire_data.get("days_since_rain"),
                    fire_id,
                ))
            conn.commit()
            logger.info(f"Updated existing fire ID {fire_id} with fresh detection metadata.")
            return True
        except Exception as e:
            logger.error(f"Failed to update fire metadata for ID {fire_id}: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return False
        finally:
            self._release_connection(conn)
 
    def update_existing_fire_frp(self, fire_id, frp, confidence, acquisition_time):
        """Updates fire radiative power (FRP) and detection time for active ongoing fires."""
        if fire_id is None:
            logger.warning("Cannot update ongoing fire FRP: fire_id is None.")
            return False
            
        if not self.db_url or "change-me" in self.db_url:
            return False
            
        conn = None
        try:
            conn = self._get_connection()
            query = """
                UPDATE fires 
                SET frp = GREATEST(frp, %s), 
                    confidence = GREATEST(confidence, %s),
                    acquisition_time = %s,
                    updated_at = NOW() 
                WHERE id = %s;
            """
            with conn.cursor() as cur:
                cur.execute(query, (frp, confidence, acquisition_time, fire_id))
            conn.commit()
            logger.info(f"Updated ongoing fire ID {fire_id} with new FRP: {frp} MW.")
            return True
        except Exception as e:
            logger.error(f"Failed to update ongoing fire FRP: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return False
        finally:
            self._release_connection(conn)

    def mark_fire_notified(self, fire_id):
        """Records the current time as the last notification time for a fire."""
        if fire_id is None or not self.db_url or "change-me" in self.db_url:
            return False

        conn = None
        try:
            conn = self._get_connection()
            query = "UPDATE fires SET last_notified_at = NOW() WHERE id = %s;"
            with conn.cursor() as cur:
                cur.execute(query, (fire_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to mark fire {fire_id} as notified: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return False
        finally:
            self._release_connection(conn)
 
    def resolve_old_fires(self, hours=24):
        """Marks CONFIRMED/PENDING fires as RESOLVED if not re-detected within the given hours."""
        if not self.db_url or "change-me" in self.db_url:
            return 0

        conn = None
        try:
            conn = self._get_connection()
            query = """
                UPDATE fires
                SET status = 'RESOLVED', updated_at = NOW()
                WHERE status IN ('CONFIRMED', 'PENDING')
                  AND acquisition_time < NOW() - %s::interval
                RETURNING id;
            """
            with conn.cursor() as cur:
                cur.execute(query, (f"{hours} hours",))
                resolved_ids = cur.fetchall()
            conn.commit()
            count = len(resolved_ids)
            if count > 0:
                logger.info(f"Auto-resolved {count} old fires (not re-detected in {hours}h).")
            return count
        except Exception as e:
            logger.error(f"Failed to resolve old fires: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return 0
        finally:
            self._release_connection(conn)

    def get_all_fires(self, limit=200):
        """
        Fetches fire data for dashboard display.
        Returns None on connection or query errors (so calling code can distinguish from 0 rows).
        """
        if not self.db_url or "change-me" in self.db_url:
            return None
            
        conn = None
        try:
            conn = self._get_connection()
            query = """
                SELECT id, latitude, longitude, frp, confidence, acquisition_time, 
                       status, source, temp, humidity, wind_speed, wind_direction, risk_score, 
                       product_id, quicklook_url, telegram_message_id,
                       brightness, bright_t31, daynight, scan_pixel,
                       cluster_id, cluster_size, composite_score, days_since_rain,
                       created_at, updated_at
                FROM fires
                ORDER BY acquisition_time DESC
                LIMIT %s;
            """
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (limit,))
                result = cur.fetchall()
            return result
        except Exception as e:
            logger.error(f"Failed to query fires from database: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return None
        finally:
            self._release_connection(conn)

    def save_citizen_report(self, report_data):
        """Saves a crowdsourced citizen / ranger report to the citizen_reports table."""
        if not self.db_url or "change-me" in self.db_url:
            return None
            
        conn = None
        try:
            conn = self._get_connection()
            query = """
                INSERT INTO citizen_reports (
                    fire_id, latitude, longitude, reporter_type, reporter_name,
                    wilaya, severity, description, photo_b64, verified
                ) VALUES (
                    %(fire_id)s, %(latitude)s, %(longitude)s, %(reporter_type)s, %(reporter_name)s,
                    %(wilaya)s, %(severity)s, %(description)s, %(photo_b64)s, %(verified)s
                ) RETURNING id;
            """
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, {
                    "fire_id": report_data.get("fire_id"),
                    "latitude": report_data["latitude"],
                    "longitude": report_data["longitude"],
                    "reporter_type": report_data.get("reporter_type", "Citizen"),
                    "reporter_name": report_data.get("reporter_name", "Anonymous"),
                    "wilaya": report_data.get("wilaya"),
                    "severity": report_data.get("severity", "Active Smoke"),
                    "description": report_data.get("description", ""),
                    "photo_b64": report_data["photo_b64"],  # Obligatory photo
                    "verified": report_data.get("verified", False)
                })
                res = cur.fetchone()
            conn.commit()
            return res["id"] if res else None
        except Exception as e:
            logger.error(f"Failed to save citizen report: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return None
        finally:
            self._release_connection(conn)

    def get_recent_citizen_reports(self, limit=100):
        """Retrieves recent citizen reports."""
        if not self.db_url or "change-me" in self.db_url:
            return []
            
        conn = None
        try:
            conn = self._get_connection()
            query = """
                SELECT id, fire_id, latitude, longitude, reporter_type, reporter_name,
                       wilaya, severity, description, photo_b64, verified, created_at
                FROM citizen_reports
                ORDER BY created_at DESC
                LIMIT %s;
            """
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (limit,))
                return cur.fetchall()
        except Exception as e:
            logger.error(f"Failed to query citizen reports: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return []
        finally:
            self._release_connection(conn)

