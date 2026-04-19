"""Quick test: connect to PostgreSQL, ensure table exists, insert a test row, read it back."""
import os, sys, json, uuid
from dotenv import load_dotenv
load_dotenv()

import psycopg2

conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", 5432)),
    dbname=os.getenv("DB_NAME", "ea_db"),
    user=os.getenv("DB_USER", "postgres"),
    password=os.getenv("DB_PASSWORD", "postgres"),
)
cur = conn.cursor()

# 1. Check if meetings table exists
cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'meetings')")
exists = cur.fetchone()[0]
print(f"meetings table exists: {exists}")

if not exists:
    print("Creating meetings table...")
    cur.execute("""
        CREATE TABLE meetings (
            id UUID PRIMARY KEY,
            organiser_name VARCHAR(255) NOT NULL,
            start_date TIMESTAMPTZ NOT NULL,
            end_date TIMESTAMPTZ NOT NULL,
            meeting_title VARCHAR(500) NOT NULL,
            meeting_agenda TEXT,
            participants TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
        )
    """)
    conn.commit()
    print("Table created!")

# 2. Insert a test meeting
test_id = str(uuid.uuid4())
cur.execute(
    """INSERT INTO meetings (id, organiser_name, start_date, end_date, meeting_title, meeting_agenda, participants)
       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
    (
        test_id,
        "Poojitha Reddy",
        "2026-04-16T10:00:00Z",
        "2026-04-16T11:00:00Z",
        "DB Integration Test Meeting",
        "Testing automatic meeting logging to PostgreSQL",
        json.dumps(["Rithwika Singh", "Rahul Sharma"]),
    ),
)
conn.commit()
print(f"Inserted test meeting: {test_id}")

# 3. Read back all meetings
cur.execute("SELECT id, organiser_name, meeting_title, participants, created_at FROM meetings ORDER BY created_at DESC")
rows = cur.fetchall()
print(f"\n--- All meetings in DB ({len(rows)} rows) ---")
for row in rows:
    print(f"  ID: {row[0]}")
    print(f"  Organiser: {row[1]}")
    print(f"  Title: {row[2]}")
    print(f"  Participants: {row[3]}")
    print(f"  Created: {row[4]}")
    print()

cur.close()
conn.close()
print("DB test PASSED!")
