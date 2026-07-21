"""Database migration script — adds new detection columns to the fires table."""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("ERROR: DATABASE_URL not set.")
    exit(1)

migration_sql = """
ALTER TABLE fires ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'VIIRS_SNPP_NRT';
ALTER TABLE fires ADD COLUMN IF NOT EXISTS brightness DOUBLE PRECISION;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS bright_t31 DOUBLE PRECISION;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS daynight CHAR(1);
ALTER TABLE fires ADD COLUMN IF NOT EXISTS scan_pixel DOUBLE PRECISION;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS cluster_id INTEGER;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS cluster_size INTEGER;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS composite_score DOUBLE PRECISION;
ALTER TABLE fires ADD COLUMN IF NOT EXISTS days_since_rain DOUBLE PRECISION;
"""

conn = psycopg2.connect(db_url, connect_timeout=10)
cur = conn.cursor()
cur.execute(migration_sql)
conn.commit()
print("Migration successful - 9 columns added to fires table.")
cur.close()
conn.close()
