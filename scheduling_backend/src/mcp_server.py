"""
MCP Server — exposes scheduling tools via FastMCP (SSE endpoint: /mcp).
search_users and get_users_by_team are wrapped with Redis cache (1h TTL)
so repeated lookups never hit the DB or LLM tool-call loop again.
"""

from mcp.server.fastmcp import FastMCP
from .dependencies import get_repo, get_session_mgr
from .models import User, Event, EventTime, AttendeeEntry, EmailAddress, OnlineMeeting
from .session_manager import SessionManager
from .db_client import insert_meeting
import uuid
from datetime import datetime, timedelta

mcp = FastMCP("Scheduling Assistant")

def _get_organiser():
    return get_repo().get_organiser()


# ---------------------------------------------------------------------------
# Tool: search_users  (Redis-cached)
# ---------------------------------------------------------------------------
@mcp.tool()
def search_users(query: str) -> list[dict]:
    """Fuzzy search users by name, department, email, or job title.
    Use this to find EIDs (user IDs) for the attendees list.
    Returns: list of {"id": "eid_here", "name": "full_name", "email": "email_here", ...}
    """
    cached = get_session_mgr().get_cached_search(query)
    if cached is not None:
        print(f"[CACHE HIT] search_users({query!r})", flush=True)
        return cached

    results = get_repo().search_users(query)
    formatted = [
        {
            "id": u.id,
            "name": u.displayName,
            "email": u.mail,
            "eid": u.id,
            "jobTitle": u.jobTitle,
            "department": u.department,
            "timeZone": u.timeZone,
        }
        for u in results
        if u.id != _get_organiser().id
    ]
    get_session_mgr().cache_search(query, formatted, ttl=3600)
    return formatted


# ---------------------------------------------------------------------------
# Tool: get_users_by_team  (Redis-cached)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_users_by_team(team_name: str) -> list[dict]:
    """Get all members of a department or designation/job title.
    Use when user says 'the engineering team' or 'all accountants'.
    Results are cached in Redis for 1 hour.
    """
    cache_key = f"team:{team_name}"
    cached = get_session_mgr().get_cached_search(cache_key)
    if cached is not None:
        print(f"[CACHE HIT] get_users_by_team({team_name!r})", flush=True)
        return cached

    by_dept = get_repo().get_users_by_department(team_name)
    by_title = get_repo().get_users_by_job_title(team_name)
    combined = {u.id: u for u in by_dept + by_title if u.id != _get_organiser().id}
    formatted = [
        {
            "id": u.id,
            "name": u.displayName,
            "email": u.mail,
            "eid": u.id,
            "jobTitle": u.jobTitle,
            "department": u.department,
            "timeZone": u.timeZone,
        }
        for u in combined.values()
    ]
    get_session_mgr().cache_search(cache_key, formatted, ttl=3600)
    return formatted


# ---------------------------------------------------------------------------
# Tool: get_user_schedule
# ---------------------------------------------------------------------------
@mcp.tool()
def get_user_schedule(user_id: str, date: str) -> list[dict]:
    """Get all calendar events for a user on a given date (YYYY-MM-DD UTC)."""
    events = get_repo().get_events_on_date(user_id, date)
    return [
        {
            "id": e.id,
            "subject": e.subject,
            "start": e.start.dateTime,
            "end": e.end.dateTime,
            "location": e.location,
        }
        for e in events
    ]


# ---------------------------------------------------------------------------
# Tool: get_mutual_free_slots
# ---------------------------------------------------------------------------
@mcp.tool()
def get_mutual_free_slots(user_ids: list[str], date: str, duration_mins: int = 60) -> list[dict]:
    """Find up to 3 mutual free time slots for all given users on a date (UTC).
    Always includes organiser in the check.
    """
    all_ids = list(set(user_ids + [_get_organiser().id]))
    return get_repo().get_free_slots(all_ids, date, duration_mins)


# ---------------------------------------------------------------------------
# Tool: check_conflict_detail
# ---------------------------------------------------------------------------
@mcp.tool()
def check_conflict_detail(user_id: str, start: str, end: str, buffer_mins: int = 15) -> dict:
    """Check if a user has a conflict with the proposed slot.
    Returns conflict type: 'none' | 'full_overlap' | 'partial_overlap' | 'buffer'.
    """
    events = get_repo().get_events_for_user(user_id)
    def _parse(iso_s: str):
        # Ensure aware UTC
        dt = datetime.fromisoformat(iso_s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    req_s = _parse(start)
    req_e = _parse(end)

    for ev in events:
        ev_s = _parse(ev.start.dateTime)
        ev_e = _parse(ev.end.dateTime)

        if ev_s < req_e and ev_e > req_s:
            conflict_type = "full_overlap" if (ev_s <= req_s and ev_e >= req_e) else "partial_overlap"
            return {
                "conflict": True, "type": conflict_type,
                "event_id": ev.id, "subject": ev.subject,
                "event_start": ev.start.dateTime, "event_end": ev.end.dateTime,
            }

        gap = (req_s - ev_e).total_seconds() / 60
        if 0 <= gap <= buffer_mins:
            return {
                "conflict": True, "type": "buffer",
                "event_id": ev.id, "subject": ev.subject,
                "event_start": ev.start.dateTime, "event_end": ev.end.dateTime,
                "buffer_gap_mins": round(gap),
            }

    return {"conflict": False, "type": "none"}


def _resolve_attendees(raw_attendees: list) -> list[dict]:
    """Helper to convert a list of strings (names) or dicts into proper attendee dicts.
    Prevents 'string indices must be integers' if LLM sends strings.
    """
    resolved = []
    for a in raw_attendees:
        if isinstance(a, str):
            # If LLM sent a name string, try to find the user to get their ID
            results = get_repo().search_users(a)
            if results:
                user = results[0]  # Take the best match
                resolved.append({"id": user.id, "name": user.displayName, "type": "required"})
            else:
                continue
        elif isinstance(a, dict) and "id" in a:
            resolved.append(a)
    return resolved

# ---------------------------------------------------------------------------
# Tool: create_meeting
# ---------------------------------------------------------------------------
@mcp.tool()
def create_meeting(
    subject: str,
    agenda: str,
    location: str,
    start: str,
    end: str,
    attendees: list = [],  # List of {"id": "eid_here", "type": "required|optional"}
    recurrence: str = "none",
    presenter: str = "",
) -> dict:
    """Book a new meeting. Poojitha Reddy is always the organiser.
    
    Args:
        subject: Clear meeting title.
        agenda: Meeting goals/topics.
        location: e.g. 'Virtual' or a Room Name.
        start: ISO 8601 start time (UTC).
        end: ISO 8601 end time (UTC).
        attendees: IMPORTANT: List of objects e.g. [{"id": "eid_123", "type": "required"}]. 
                  Search users first to get their IDs.
        recurrence: 'none' | 'daily' | 'weekly' | 'biweekly' | 'monthly'
        presenter: Name of lead (empty = organiser).
    """
    attendees = _resolve_attendees(attendees)
    event_id = f"e_{uuid.uuid4().hex[:8]}"
    join_url = get_repo().make_join_url(event_id)

    attendee_entries = []
    for a in attendees:
        user = get_repo().get_user_by_id(a["id"])
        if user:
            attendee_entries.append(AttendeeEntry(
                emailAddress=EmailAddress(address=user.mail, name=user.displayName),
                type=a.get("type", "optional"),
                userId=user.id,
            ))

    event = Event(**{
        "id": event_id,
        "subject": subject,
        "bodyPreview": agenda,
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
        "location": location,
        "attendees": attendee_entries,
        "organizer": {"address": _get_organiser().mail, "name": _get_organiser().displayName},
        "onlineMeeting": {"joinUrl": join_url},
        "isOnlineMeeting": True,
    })
    get_repo().create_event(event)

    attendee_ids = [a["id"] for a in attendees if "id" in a]
    fingerprint = SessionManager.make_fingerprint(attendee_ids, subject)

    # Save to Redis for future duplicate detection
    get_session_mgr().save_meeting(fingerprint, {
        "event_id": event_id,
        "subject": subject,
        "agenda": agenda,
        "start": start,
        "end": end,
        "location": location,
        "join_url": join_url,
        "recurrence": recurrence,
        "presenter": presenter or _get_organiser().displayName,
        "organizer": _get_organiser().displayName,
        "attendees": [a.emailAddress.name for a in attendee_entries],
        "fingerprint": fingerprint,
    })

    # ── Persist to PostgreSQL (meetings table) ──────────────────────
    insert_meeting(
        organiser_name=_get_organiser().displayName,
        start_date=start,
        end_date=end,
        meeting_title=subject,
        meeting_agenda=agenda,
        participants=[a.emailAddress.name for a in attendee_entries],
    )

    for ae in attendee_entries:
        if ae.userId:
            get_repo().send_notification(
                ae.userId,
                subject=f"Meeting Invite: {subject}",
                body=(
                    f"You have been invited to '{subject}'.\n"
                    f"Agenda: {agenda}\n"
                    f"Presenter: {presenter or _get_organiser().displayName}\n"
                    f"Recurrence: {recurrence}\n"
                    f"When: {start} – {end} UTC\n"
                    f"Location: {location}\n"
                    f"Join: {join_url}"
                ),
            )

    return {
        "status": "booked",
        "event_id": event_id,
        "fingerprint": fingerprint,
        "subject": subject,
        "start": start,
        "end": end,
        "location": location,
        "recurrence": recurrence,
        "presenter": presenter or _get_organiser().displayName,
        "join_url": join_url,
        "organizer": _get_organiser().displayName,
        "attendees": [a.emailAddress.name for a in attendee_entries],
    }


# ---------------------------------------------------------------------------
# Tool: update_meeting  (full update — time, attendees, subject, agenda, all)
# ---------------------------------------------------------------------------
@mcp.tool()
def update_meeting(
    event_id: str,
    fingerprint: str = "",
    new_start: str = "",
    new_end: str = "",
    new_subject: str = "",
    new_agenda: str = "",
    new_location: str = "",
    new_attendees: list = [],   # [{id, name, email, type}]
    new_recurrence: str = "",
    new_presenter: str = "",
) -> dict:
    """Update any fields of an existing meeting.
    Only provide the fields that are changing.
    
    Args:
        event_id: ID of the meeting to update.
        fingerprint: (Optional) The Redis fingerprint of the meeting.
        new_attendees: List of objects e.g. [{"id": "eid_123", "type": "required"}].
                      To remove someone, use {"id": "eid_123", "type": "remove"}.
    """
    new_attendees = _resolve_attendees(new_attendees)
    # 1. Time update
    if new_start and new_end:
        get_repo().update_event(event_id, new_start, new_end)

    # 2. Pull existing Redis data to merge
    old_data: dict = {}
    if fingerprint:
        old_data = get_session_mgr().get_meeting(fingerprint) or {}

    subject   = new_subject   or old_data.get("subject", "Meeting")
    agenda    = new_agenda    or old_data.get("agenda", "")
    location  = new_location  or old_data.get("location", "Virtual")
    recurrence = new_recurrence or old_data.get("recurrence", "none")
    presenter  = new_presenter  or old_data.get("presenter", _get_organiser().displayName)
    start     = new_start or old_data.get("start", "")
    end       = new_end   or old_data.get("end", "")
    join_url  = old_data.get("join_url", get_repo().make_join_url(event_id))

    # 3. Merge attendees — resolve new ones, keep existing names
    existing_names: list[str] = old_data.get("attendees", [])
    new_entries: list[AttendeeEntry] = []
    if new_attendees:
        for a in new_attendees:
            if a.get("type") == "remove":
                continue
            user = get_repo().get_user_by_id(a["id"])
            if user:
                new_entries.append(AttendeeEntry(
                    emailAddress=EmailAddress(address=user.mail, name=user.displayName),
                    type=a.get("type", "optional"),
                    userId=user.id,
                ))

    all_attendee_names = list(set(existing_names + [ae.emailAddress.name for ae in new_entries]))

    # 4a. Notify EXISTING attendees if time changed (their calendar already updated via repo.update_event)
    if new_start and new_end:
        for existing_event in get_repo().get_events_for_user(_get_organiser().id):
            if existing_event.id == event_id:
                for ae in existing_event.attendees:
                    if ae.userId:
                        get_repo().send_notification(
                            ae.userId,
                            subject=f"Meeting Rescheduled: {subject}",
                            body=(
                                f"'{subject}' has been rescheduled.\n"
                                f"New time: {start} – {end} UTC\n"
                                f"Presenter: {presenter}\n"
                                f"Location: {location}\n"
                                f"Join: {join_url}"
                            ),
                        )
                break

    # 4b. Notify NEWLY ADDED attendees with full invite
    for ae in new_entries:
        if ae.userId:
            get_repo().send_notification(
                ae.userId,
                subject=f"Meeting Invite: {subject}",
                body=(
                    f"You have been added to '{subject}'.\n"
                    f"Agenda: {agenda}\n"
                    f"Presenter: {presenter}\n"
                    f"Recurrence: {recurrence}\n"
                    f"Time: {start} – {end} UTC\n"
                    f"Location: {location}\n"
                    f"Join: {join_url}"
                ),
            )

    # 5. Update Redis fingerprint (delete old, save new)
    if fingerprint:
        get_session_mgr().delete_meeting(fingerprint)

    attendee_ids = [a["id"] for a in (new_attendees or []) if a.get("type") != "remove"]
    new_fingerprint = SessionManager.make_fingerprint(attendee_ids or ["preserved"], subject)
    get_session_mgr().save_meeting(new_fingerprint, {
        "event_id": event_id,
        "subject": subject,
        "agenda": agenda,
        "start": start,
        "end": end,
        "location": location,
        "join_url": join_url,
        "recurrence": recurrence,
        "presenter": presenter,
        "organizer": _get_organiser().displayName,
        "attendees": all_attendee_names,
        "fingerprint": new_fingerprint,
    })

    return {
        "status": "updated",
        "event_id": event_id,
        "new_fingerprint": new_fingerprint,
        "subject": subject,
        "start": start,
        "end": end,
        "recurrence": recurrence,
        "presenter": presenter,
        "attendees": all_attendee_names,
        "join_url": join_url,
    }


# ---------------------------------------------------------------------------
# Tool: reschedule_meeting  (kept for backward compat — time-only shortcut)
# ---------------------------------------------------------------------------
@mcp.tool()
def reschedule_meeting(event_id: str, new_start: str, new_end: str) -> dict:
    """Reschedule an existing meeting to a new slot. Notifies all attendees.
    For a full update (attendees, agenda, etc.) use update_meeting instead.
    """
    updated = get_repo().update_event(event_id, new_start, new_end)
    if not updated:
        return {"status": "error", "message": f"Event {event_id} not found."}

    for ae in updated.attendees:
        if ae.userId:
            get_repo().send_notification(
                ae.userId,
                subject=f"Meeting Rescheduled: {updated.subject}",
                body=(
                    f"'{updated.subject}' has been rescheduled.\n"
                    f"New time: {new_start} – {new_end} UTC\n"
                    f"Join: {updated.onlineMeeting.joinUrl if updated.onlineMeeting else 'N/A'}"
                ),
            )

    return {"status": "rescheduled", "event_id": event_id, "new_start": new_start, "new_end": new_end}


# ---------------------------------------------------------------------------
# Tool: notify_user
# ---------------------------------------------------------------------------
@mcp.tool()
def notify_user(user_id: str, subject: str, body: str) -> dict:
    """Send a notification to a user."""
    return get_repo().send_notification(user_id, subject, body)


if __name__ == "__main__":
    mcp.run()
