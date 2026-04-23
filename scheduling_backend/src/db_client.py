"""
PostgreSQL client — logs every meeting created by the scheduling agent
into the `meetings` table managed by Liquibase.

Environment variables (loaded via .env or OS):
  DB_HOST     (default: localhost)
  DB_PORT     (default: 5432)
  DB_NAME     (default: executive_assistant)
  DB_USER     (default: postgres)
  DB_PASSWORD (default: postgres)
"""

import os
import json
import uuid
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime


def _get_connection():
    """Return a fresh psycopg2 connection using env vars."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "ea_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "Admin123"),
    )


def insert_meeting(
    meeting_id: str,
    organiser_name: str,
    start_date: str,
    end_date: str,
    meeting_title: str,
    meeting_agenda: str = "",
    participants: list[str] | None = None,
) -> str | None:
    """
    Insert a row into the `meetings` table.

    Parameters
    ----------
    organiser_name : str   — display name of the organiser
    start_date     : str   — ISO-8601 UTC timestamp  (e.g. "2026-04-11T09:00:00Z")
    end_date       : str   — ISO-8601 UTC timestamp
    meeting_title  : str   — subject / title of the meeting
    meeting_agenda : str   — body / agenda text
    participants   : list  — list of attendee display names

    Returns
    -------
    str | None — the generated UUID (as string) on success, None on failure.
    """
    participants_json = json.dumps(participants or [])

    sql = """
        INSERT INTO meetings
            (id, organiser_name, start_date, end_date,
             meeting_title, meeting_agenda, participants)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s)
    """

    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute(sql, (
            meeting_id,
            organiser_name,
            start_date,
            end_date,
            meeting_title,
            meeting_agenda,
            participants_json,
        ))
        conn.commit()
        cur.close()
        conn.close()
        print(f"[DB] Meeting logged → {meeting_id}  ({meeting_title})")
        return meeting_id
    except Exception as e:
        print(f"[DB ERROR] Failed to log meeting: {e}")
        return None


def update_meeting_db(
    meeting_id: str,
    meeting_title: str | None = None,
    meeting_agenda: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    participants: list[str] | None = None,
) -> bool:
    """Update existing meeting in the PostgreSQL DB."""
    fields = []
    params = []

    if meeting_title is not None:
        fields.append("meeting_title = %s")
        params.append(meeting_title)
    if meeting_agenda is not None:
        fields.append("meeting_agenda = %s")
        params.append(meeting_agenda)
    if start_date is not None:
        fields.append("start_date = %s")
        params.append(start_date)
    if end_date is not None:
        fields.append("end_date = %s")
        params.append(end_date)
    if participants is not None:
        fields.append("participants = %s")
        params.append(json.dumps(participants))

    if not fields:
        return True

    sql = f"UPDATE meetings SET {', '.join(fields)} WHERE id = %s"
    params.append(meeting_id)

    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        conn.commit()
        cur.close()
        conn.close()
        print(f"[DB] Meeting updated → {meeting_id}")
        return True
    except Exception as e:
        print(f"[DB ERROR] Failed to update meeting: {e}")
        return False


def delete_meeting_db(meeting_id: str) -> bool:
    """Delete meeting from the PostgreSQL DB."""
    sql = "DELETE FROM meetings WHERE id = %s"
    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute(sql, (meeting_id,))
        conn.commit()
        cur.close()
        conn.close()
        print(f"[DB] Meeting deleted → {meeting_id}")
        return True
    except Exception as e:
        print(f"[DB ERROR] Failed to delete meeting: {e}")
        return False

def get_user_schedule_db(user_name: str, target_date: str) -> list[dict]:
    """Fetch meetings from PostgreSQL for a specific user on a specific date."""
    sql = "SELECT id, meeting_title, start_date, end_date, participants, organiser_name FROM meetings"
    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        results = []
        for r in rows:
            m_id, title, start, end, parts_json, org = r
            if not start.startswith(target_date):
                continue
            try:
                parts = json.loads(parts_json) if isinstance(parts_json, str) else list(parts_json)
            except Exception:
                parts = []
            
            # Filter solely for the requested user
            if user_name == org or user_name in parts:
                results.append({
                    "id": m_id,
                    "subject": title,
                    "start": start,
                    "end": end,
                    "location": "Virtual",
                    "participants": parts,
                    "organiser": org,
                    "is_db": True
                })
        return results
    except Exception as e:
        print(f"[DB ERROR] Failed to read schedule: {e}")
        return []


def get_frequent_contacts_db(organiser_name: str) -> list[str]:
    """
    Return top 5 names who appear most frequently in the participants list 
    for meetings organised by organiser_name.
    """
    sql = "SELECT participants FROM meetings WHERE organiser_name = %s"
    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute(sql, (organiser_name,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        counts = {}
        for (parts_json,) in rows:
            try:
                parts = json.loads(parts_json) if isinstance(parts_json, str) else list(parts_json)
                for p in parts:
                    counts[p] = counts.get(p, 0) + 1
            except Exception:
                continue

        # Sort by frequency
        sorted_contacts = sorted(counts.items(), key=lambda x: -x[1])
        return [c[0] for c in sorted_contacts[:5]]
    except Exception as e:
        print(f"[DB ERROR] Failed to get frequent contacts: {e}")
        return []
