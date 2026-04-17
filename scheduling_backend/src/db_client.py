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
    meeting_id = str(uuid.uuid4())
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
