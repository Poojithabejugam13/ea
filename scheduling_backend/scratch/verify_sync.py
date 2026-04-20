import sys
import os
import uuid
import datetime
from datetime import timedelta

# Setup paths
sys.path.append(os.getcwd())

from src.db_client import _get_connection
from src.mcp_server import create_meeting, update_meeting, delete_meeting

def check_db(event_id):
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("SELECT meeting_title, start_date FROM meetings WHERE id = %s", (event_id,))
    res = cur.fetchone()
    cur.close()
    conn.close()
    return res

def test_full_cycle():
    print("Starting Sync Verification...")
    
    # 1. Create
    subject = f"Verification Sync Test {uuid.uuid4().hex[:4]}"
    start = "2026-04-18T10:00:00Z"
    end = "2026-04-18T11:00:00Z"
    
    print(f"Propagating Create for: {subject}")
    result = create_meeting(subject, "Test Agenda", "Test Room", start, end, [])
    event_id = result["event_id"]
    fingerprint = result["fingerprint"]
    
    db_row = check_db(event_id)
    print(f"DB Entry after Create: {db_row}")
    assert db_row is not None
    assert db_row[0] == subject

    # 2. Update
    new_start = "2026-04-18T12:00:00Z"
    new_end = "2026-04-18T13:00:00Z"
    print(f"Propagating Update (Reschedule) to: {new_start}")
    update_result = update_meeting(event_id, fingerprint=fingerprint, new_start=new_start, new_end=new_end)
    
    # Postgres returns aware datetime (likely in local IST +5:30). 
    # Convert to UTC for comparison.
    utc_hour = db_row[1].astimezone(datetime.timezone.utc).hour
    print(f"DB Entry after Update: {db_row} (UTC Hour: {utc_hour})")
    assert db_row is not None
    assert utc_hour == 12

    # 3. Delete
    print("Propagating Delete...")
    delete_result = delete_meeting(event_id, fingerprint=update_result["new_fingerprint"])
    
    db_row = check_db(event_id)
    print(f"DB Entry after Delete: {db_row}")
    assert db_row is None
    
    print("✅ Sync Verification Successful!")

if __name__ == "__main__":
    try:
        test_full_cycle()
    except Exception as e:
        print(f"❌ Verification Failed: {e}")
        import traceback
        traceback.print_exc()
