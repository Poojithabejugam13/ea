"""
MCP Server — exposes scheduling tools via FastMCP (SSE endpoint: /mcp).
search_users and get_users_by_team are wrapped with Redis cache (1h TTL)
so repeated lookups never hit the DB or LLM tool-call loop again.
"""

from mcp.server.fastmcp import FastMCP
from .dependencies import get_repo, get_session_mgr
from .session_manager import SessionManager
from .models import User, Event, EventTime, AttendeeEntry, EmailAddress, OnlineMeeting
from .session_manager import SessionManager
from .db_client import insert_meeting, update_meeting_db, delete_meeting_db
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re
from contextvars import ContextVar

mcp = FastMCP("Scheduling Assistant")
CURRENT_SESSION_ID: ContextVar[str] = ContextVar("current_session_id", default="")


def set_current_session_id(session_id: str):
    return CURRENT_SESSION_ID.set(session_id)


def reset_current_session_id(token):
    CURRENT_SESSION_ID.reset(token)


def _parse_iso(iso_s: str) -> datetime:
    if not iso_s:
        return datetime.now(timezone.utc)
    
    # Handle custom UI format: "20 Apr 2026, 04:00 PM Asia/Kolkata"
    if "," in iso_s and ("AM" in iso_s or "PM" in iso_s):
        try:
            # Strip timezone name at the end if present
            clean_s = iso_s
            tz_part = "UTC"
            if "Asia/Kolkata" in iso_s:
                clean_s = iso_s.replace("Asia/Kolkata", "").strip()
                tz_part = "Asia/Kolkata"
            
            dt = datetime.strptime(clean_s, "%d %b %Y, %I:%M %p")
            return dt.replace(tzinfo=ZoneInfo(tz_part)).astimezone(timezone.utc)
        except Exception as e:
            print(f"DEBUG: Failed to parse custom format '{iso_s}': {e}")

    # Standard ISO parsing
    try:
        dt = datetime.fromisoformat(iso_s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as e:
        print(f"DEBUG: Failed to parse ISO format '{iso_s}': {e}")
        return datetime.now(timezone.utc)


def _set_live_status(message: str):
    session_id = CURRENT_SESSION_ID.get()
    if session_id:
        get_session_mgr().set_status(session_id, message)

def _get_organiser():
    return get_repo().get_organiser()

def _coerce_attendees(attendees) -> list[dict]:
    """Best-effort coercion to a list of {id, type} objects.

    ADK/tool calls sometimes send attendees as strings (e.g. "Alice - EID: 101")
    instead of structured dicts. This prevents crashes and lets the tool proceed.
    """
    if attendees is None:
        return []
    if not isinstance(attendees, list):
        attendees = [attendees]

    coerced: list[dict] = []
    for a in attendees:
        if isinstance(a, dict):
            # Normalize id field if model uses alternate keys
            attendee_id = a.get("id") or a.get("eid") or a.get("userId") or a.get("user_id")
            if attendee_id is None:
                continue
            coerced.append({
                **a,
                "id": str(attendee_id),
                "type": a.get("type", "required"),
            })
            continue

        if isinstance(a, str):
            s = a.strip()
            # Try to extract an EID-like numeric id.
            m = re.search(r"\bEID\b\s*[:#-]?\s*(\d+)\b", s, flags=re.IGNORECASE)
            if not m:
                m = re.search(r"\b(\d{2,})\b", s)  # fallback: any 2+ digit token
            if not m:
                continue
            coerced.append({"id": m.group(1), "type": "required"})
            continue

    return coerced


# ---------------------------------------------------------------------------
# Tool: search_users  (Redis-cached)
# ---------------------------------------------------------------------------
@mcp.tool()
def search_users(query: str) -> list[dict]:
    """Fuzzy search users by name, department, email, or job title.
    Use this to find EIDs (user IDs) for the attendees list.
    Returns: list of {"id": "eid_here", "name": "full_name", "email": "email_here", ...}
    """
    _set_live_status("Searching users...")
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
    _set_live_status("Getting team members...")
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
# Tool: get_frequent_contacts
# ---------------------------------------------------------------------------
@mcp.tool()
def get_frequent_contacts() -> list[dict]:
    """Get people Poojitha Reddy connects with most often.
    Use this to suggest attendees in Phase 1 if she says 'schedule a meet'.
    """
    _set_live_status("Fetching frequent contacts...")
    org = _get_organiser()
    results = get_repo().get_frequent_contacts(org.displayName)
    return [
        {
            "id": u.id,
            "name": u.displayName,
            "email": u.mail,
            "jobTitle": u.jobTitle,
            "department": u.department,
        }
        for u in results
    ]


# ---------------------------------------------------------------------------
# Tool: get_user_schedule
# ---------------------------------------------------------------------------
@mcp.tool()
def get_user_schedule(user_id: str, date: str) -> list[dict]:
    """Get all calendar events for a user on a given date (YYYY-MM-DD UTC)."""
    _set_live_status("Reading user schedule...")
    from .dependencies import CALLER_USER_ID
    from .db_client import get_user_schedule_db
    caller_id = CALLER_USER_ID.get()
    caller_is_owner = (caller_id == user_id)
    
    caller_user = get_repo().get_user_by_id(caller_id)
    caller_name = caller_user.displayName if caller_user else ""
    caller_email = caller_user.mail if caller_user else ""

    target_user = get_repo().get_user_by_id(user_id)
    if not target_user:
        return []

    # First try PostgreSQL Database
    db_events = get_user_schedule_db(target_user.displayName, date)
    
    events_to_process = []
    if db_events:
        for ev in db_events:
            events_to_process.append(ev)
    else:
        # Fallback to Mock Repo
        events = get_repo().get_events_on_date(user_id, date)
        for e in events:
            events_to_process.append({
                "id": e.id,
                "subject": e.subject,
                "start": e.start.dateTime,
                "end": e.end.dateTime,
                "location": e.location,
                "organiser": getattr(e.organizer, 'address', ''),
                "participants": [a.userId for a in e.attendees],
                "is_db": False
            })

    # Apply privacy mask
    scrubbed = []
    for ev in events_to_process:
        is_authorized = caller_is_owner
        if not is_authorized:
            if ev.get("is_db"):
                # DB stores participants via names
                is_authorized = (caller_name == ev.get("organiser")) or (caller_name in ev.get("participants", []))
            else:
                # Mock Repo stores attendees via user ids in our adaptation
                is_authorized = (caller_email == ev.get("organiser")) or (caller_id in ev.get("participants", []))
        
        if is_authorized:
            scrubbed.append({
                "id": ev["id"],
                "subject": ev["subject"],
                "start": ev["start"],
                "end": ev["end"],
                "location": ev["location"]
            })
        else:
            scrubbed.append({
                "id": ev["id"],
                "subject": "Busy",
                "start": ev["start"],
                "end": ev["end"],
                "location": "Private"
            })
            
    return scrubbed


# ---------------------------------------------------------------------------
# Tool: get_mutual_free_slots
# ---------------------------------------------------------------------------
@mcp.tool()
def get_mutual_free_slots(user_ids: list[str], date: str, duration_mins: int = 60) -> list[dict]:
    """Find up to 3 mutual free time slots for all given users on a date (UTC).
    Always includes organiser in the check.
    """
    _set_live_status("Finding mutual free slots...")
    all_ids = list(set(user_ids + [_get_organiser().id]))
    return get_repo().get_free_slots(all_ids, date, duration_mins)

# ---------------------------------------------------------------------------
# Tool: find_available_room
# ---------------------------------------------------------------------------
@mcp.tool()
def find_available_room(participant_count: int, start: str, end: str) -> str:
    """Find an available room for the given time slot that fits the number of participants.
    Returns the room name (e.g. 'Krishna') or 'Virtual' if no room is available.
    """
    _set_live_status("Finding available room...")
    available_room_strs = get_repo().get_room_suggestions("", start, end)
    
    # Sort rooms by capacity to find the smallest one that fits
    parsed_rooms = []
    for r_str in available_room_strs:
        m = re.search(r'Capacity:\s*(\d+)', r_str)
        if m:
            parsed_rooms.append({
                "name": r_str.split(' (')[0],
                "capacity": int(m.group(1))
            })
            
    parsed_rooms.sort(key=lambda x: x["capacity"])
    
    for r in parsed_rooms:
        if r["capacity"] >= participant_count:
            return r["name"]
            
    return "Virtual"


# ---------------------------------------------------------------------------
# Tool: check_conflict_detail
# ---------------------------------------------------------------------------
@mcp.tool()
def check_conflict_detail(user_id: str, start: str, end: str, buffer_mins: int = 15) -> dict:
    """Check if a user has a conflict with the proposed slot.
    CRITICAL: DO NOT call this if the user didn't explicitly give a time. Do not guess a time!
    Returns conflict type: 'none' | 'full_overlap' | 'partial_overlap' | 'buffer'.
    """
    events = get_repo().get_events_for_user(user_id)
    req_s = _parse_iso(start)
    req_e = _parse_iso(end)

    for ev in events:
        ev_s = _parse_iso(ev.start.dateTime)
        ev_e = _parse_iso(ev.end.dateTime)

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



# ---------------------------------------------------------------------------
# Tool: get_room_suggestions  — auto-pick a room by capacity + availability
# ---------------------------------------------------------------------------
@mcp.tool()
def get_room_suggestions(start: str, end: str, participant_count: int = 2) -> list[dict]:
    """Find available physical meeting rooms for a time window, ranked by best fit for the group size.

    Call this whenever you need to auto-assign a room for a physical meeting.
    Pick the first room whose capacity >= participant_count AND is available.
    If no room qualifies, return 'Virtual' as the location.

    Args:
        start: ISO 8601 UTC start time of the meeting.
        end: ISO 8601 UTC end time of the meeting.
        participant_count: Total number of attendees including organiser.

    Returns:
        list of {name, capacity, available} sorted by best size fit.
        If empty, use 'Virtual'.
    """
    _set_live_status("Checking room availability...")
    from datetime import datetime, timezone as dt_tz

    s_dt = _parse_iso(start)
    e_dt = _parse_iso(end)

    from .repository import MOCK_EVENTS, MOCK_ROOMS, _normalize

    booked_room_names: set[str] = set()
    for evs in MOCK_EVENTS.values():
        for ev in evs:
            ev_s = _parse_iso(ev.start.dateTime)
            ev_e = _parse_iso(ev.end.dateTime)
            if s_dt < ev_e and ev_s < e_dt:
                booked_room_names.add(_normalize(ev.location))

    available_rooms = [
        r for r in MOCK_ROOMS
        if _normalize(r.displayName) not in booked_room_names
    ]

    def _get_size_bucket(cap: int) -> str:
        if cap <= 8: return "small"
        if cap <= 14: return "medium"
        return "large"

    req_bucket = "small"
    if 3 <= participant_count <= 6: req_bucket = "medium"
    elif participant_count >= 7: req_bucket = "large"

    # Sort available rooms
    # Primary sort: same bucket as requested? (True < False means True first in desc sort, so use 1/0)
    # Secondary sort: capacity (closest fit first)
    available_rooms.sort(key=lambda r: (
        _get_size_bucket(r.capacity) != req_bucket,  # 0 if same bucket, 1 if different -> same bucket first
        abs(r.capacity - participant_count)         # then smallest difference
    ))

    results = []
    for r in available_rooms:
        results.append({
            "name": r.displayName,
            "capacity": r.capacity,
            "available": True,
            "size": _get_size_bucket(r.capacity),
            "fits_group": r.capacity >= participant_count,
        })

    return results


# ---------------------------------------------------------------------------
# Helper: check if a specific room is available for a time window
# ---------------------------------------------------------------------------
def _check_room_availability(room_name: str, start: str, end: str) -> bool:
    """Return True if the named room is free during [start, end]."""
    from datetime import datetime, timezone as dt_tz
    from .repository import MOCK_EVENTS, _normalize

    def _p(v: str):
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=dt_tz.utc)

    s_dt = _p(start)
    e_dt = _p(end)
    room_norm = _normalize(room_name)

    for evs in MOCK_EVENTS.values():
        for ev in evs:
            if _normalize(ev.location) == room_norm:
                ev_s = _p(ev.start.dateTime)
                ev_e = _p(ev.end.dateTime)
                if s_dt < ev_e and ev_s < e_dt:
                    return False
    return True


def _resolve_attendees(raw_attendees: list) -> list[dict]:

    """Helper to convert a list of strings (names) or dicts into proper attendee dicts.
    Prevents 'string indices must be integers' if LLM sends strings.
    """
    resolved = []
    if not isinstance(raw_attendees, list):
        return resolved

    for a in raw_attendees:
        if isinstance(a, str):
            # Try EID extraction first
            m = re.search(r"\bEID\b\s*[:#-]?\s*(\d+)\b", a, flags=re.IGNORECASE)
            if not m:
                m = re.search(r"\b(\d{2,})\b", a)
            if m:
                resolved.append({"id": m.group(1), "type": "required"})
                continue

            # If no EID, try name search
            results = get_repo().search_users(a)
            if results:
                user = results[0]
                resolved.append({"id": user.id, "name": user.displayName, "type": "required"})
            else:
                # Preserve the string if we can't resolve it yet, so _coerce_attendees can try
                resolved.append(a)
        elif isinstance(a, dict):
            # Check for alternative ID keys first
            temp_id = a.get("id") or a.get("eid") or a.get("userId") or a.get("user_id")
            if temp_id:
                resolved.append(a)
            elif "name" in a:
                # If no ID but name provided, try to resolve the name
                results = get_repo().search_users(a["name"])
                if results:
                    user = results[0]
                    resolved.append({"id": user.id, "name": user.displayName, "type": a.get("type", "required")})
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
    recurrence_end_date: str = "",
) -> dict:
    """Book a new meeting. Poojitha Reddy is always the organiser.
    
    CRITICAL RULE: NEVER call this tool unless the user has explicitly confirmed a specific time slot!
    If the user did NOT mention a time, YOU ARE FORBIDDEN from guessing or assuming a time. You MUST call get_mutual_free_slots instead and ask them!
    
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
    _set_live_status("Creating meeting...")
    event_id = str(uuid.uuid4())
    join_url = get_repo().make_join_url(event_id)

    attendee_entries = []
    attendees = _coerce_attendees(attendees)
    for a in attendees:
        user = get_repo().get_user_by_id(a["id"])
        if user:
            attendee_entries.append(AttendeeEntry(
                emailAddress=EmailAddress(address=user.mail, name=user.displayName),
                type=a.get("type", "required"),
                userId=user.id,
            ))

    # Safety net: validate conflicts at booking time so meetings are never
    # created on overlapping slots even if the model skipped conflict tools.
    requested_ids = list({ae.userId for ae in attendee_entries if ae.userId})
    requested_ids.append(_get_organiser().id)
    conflict_details = []
    for uid in sorted(set(requested_ids)):
        detail = check_conflict_detail(uid, start, end)
        if detail.get("conflict"):
            user = get_repo().get_user_by_id(uid)
            conflict_details.append({
                "user_id": uid,
                "user_name": user.displayName if user else uid,
                **detail,
            })
    if conflict_details:
        return {
            "status": "conflict",
            "message": "Cannot create meeting because one or more attendees are busy.",
            "requested_start": start,
            "requested_end": end,
            "conflicts": conflict_details,
        }

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
        "recurrence_end_date": recurrence_end_date,
        "presenter": presenter or _get_organiser().displayName,
        "organizer": _get_organiser().displayName,
        "attendees": [a.emailAddress.name for a in attendee_entries],
        "attendee_ids": [a.get("id") for a in attendees if a.get("id")],
        "fingerprint": fingerprint,
    })

    # ── Persist to PostgreSQL (meetings table) ──────────────────────
    insert_meeting(
        meeting_id=event_id,
        organiser_name=_get_organiser().displayName,
        start_date=start,
        end_date=end,
        meeting_title=subject,
        meeting_agenda=agenda,
        participants=[a.emailAddress.name for a in attendee_entries],
        recurrence_end_date=recurrence_end_date,
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
        "attendees": [{"name": a.emailAddress.name, "email": a.emailAddress.address, "id": a.userId} for a in attendee_entries],
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
    _set_live_status("Updating meeting...")
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
    new_attendees = _coerce_attendees(new_attendees)
    if new_attendees:
        for a in new_attendees:
            if a.get("type") == "remove":
                continue
            user = get_repo().get_user_by_id(a["id"])
            if user:
                new_entries.append(AttendeeEntry(
                    emailAddress=EmailAddress(address=user.mail, name=user.displayName),
                    type=a.get("type", "required"),
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
        "attendee_ids": attendee_ids,
        "fingerprint": new_fingerprint,
    })

    # 6. Sync with PostgreSQL
    update_meeting_db(
        meeting_id=event_id,
        meeting_title=subject,
        meeting_agenda=agenda,
        start_date=start,
        end_date=end,
        participants=all_attendee_names
    )

    return {
        "status": "updated",
        "event_id": event_id,
        "fingerprint": new_fingerprint,
        "new_fingerprint": new_fingerprint,
        "subject": subject,
        "start": start,
        "end": end,
        "recurrence": recurrence,
        "presenter": presenter,
        "attendees": [{"name": ae.emailAddress.name, "email": ae.emailAddress.address, "id": ae.userId} for ae in new_entries],
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
    _set_live_status("Rescheduling meeting...")
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

    # Sync with PostgreSQL
    update_meeting_db(
        meeting_id=event_id,
        start_date=new_start,
        end_date=new_end
    )

    return {"status": "rescheduled", "event_id": event_id, "new_start": new_start, "new_end": new_end}


# ---------------------------------------------------------------------------
# Tool: delete_meeting
# ---------------------------------------------------------------------------
@mcp.tool()
def delete_meeting(event_id: str, fingerprint: str = "") -> dict:
    """Permanently delete a meeting from Calendar, Redis, and Database.
    Freeing up time slots for all attendees.
    """
    _set_live_status("Deleting meeting...")
    
    # 1. Remove from Repository (Calendar)
    get_repo().delete_event(event_id)
    
    # 2. Remove from Redis (Duplicate Detection)
    if fingerprint:
        get_session_mgr().delete_meeting(fingerprint)
        
    # 3. Remove from PostgreSQL
    delete_meeting_db(event_id)
    
    return {"status": "deleted", "event_id": event_id}


# ---------------------------------------------------------------------------
# Tool: notify_user
# ---------------------------------------------------------------------------
@mcp.tool()
def notify_user(user_id: str, subject: str, body: str) -> dict:
    """Send a notification to a user."""
    _set_live_status("Sending notifications...")
    return get_repo().send_notification(user_id, subject, body)


if __name__ == "__main__":
    mcp.run()
# ---------------------------------------------------------------------------
# Tool: notify_user
# ---------------------------------------------------------------------------
@mcp.tool()
def notify_user(user_id: str, subject: str, body: str, interactive: bool = True) -> dict:
    """Send a notification to a user.
    If interactive=True, the user will see OK/CANCEL buttons in their interface.
    """
    _set_live_status(f"Notifying user {user_id}...")
    res = get_repo().send_notification(user_id, subject, body)
    
    if interactive:
        # In a real app, this would trigger a push notification with actions.
        # Here we just log it as interactive.
        res["interactive"] = True
        res["actions"] = ["OK", "Cancel"]
        
    return res
