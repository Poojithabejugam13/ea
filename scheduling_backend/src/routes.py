"""
Routes — Graph API-aligned mock endpoints + new /prefs and /meetings endpoints.
"""

from fastapi import APIRouter
from .dependencies import get_repo, get_session_mgr
from .db_client import delete_meeting_db
from .models import Event, EventTime, AttendeeEntry, EmailAddress, OnlineMeeting

router = APIRouter()

def _get_organiser_id():
    return "poojitha"


# ── Existing Graph mock endpoints ────────────────────────────────────────────

@router.get("/v1.0/users")
def list_users():
    users = get_repo().get_all_users()
    return {
        "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users",
        "value": [u.model_dump() for u in users],
    }


@router.get("/v1.0/users/{user_id}")
def get_user(user_id: str):
    user = get_repo().get_user_by_id(user_id)
    if not user:
        return {"error": "User not found"}, 404
    return user.model_dump()


@router.get("/v1.0/users/search/{query}")
def search_users(query: str):
    results = get_repo().search_users(query)
    return {
        "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users",
        "value": [u.model_dump() for u in results],
    }


@router.get("/v1.0/users/{user_id}/events")
def get_user_events(user_id: str):
    events = get_repo().get_events_for_user(user_id)
    return {
        "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users('{}')events".format(user_id),
        "value": [e.model_dump() for e in events],
    }


@router.post("/v1.0/users/{user_id}/events")
def create_event(user_id: str, body: dict):
    from .repository import Event, EventTime, AttendeeEntry, EmailAddress, OnlineMeeting
    import uuid
    event_id = f"e_{uuid.uuid4().hex[:8]}"
    event = Event(
        id=event_id,
        subject=body.get("subject", "Meeting"),
        bodyPreview=body.get("body", {}).get("content", ""),
        start=EventTime(**body["start"]),
        end=EventTime(**body["end"]),
        location=body.get("location", {}).get("displayName", "Virtual"),
        attendees=[],
        organizer=EmailAddress(address="poojitha.reddy@test.com", name="Poojitha Reddy"),
        onlineMeeting=OnlineMeeting(joinUrl=repo.make_join_url(event_id)),
        isOnlineMeeting=True,
    )
    get_repo().create_event(event)
    return event.model_dump()


@router.get("/v1.0/users/{user_id}/calendar/freebusy")
def get_freebusy(user_id: str, date: str, duration_mins: int = 60):
    slots = get_repo().get_free_slots([user_id], date, duration_mins)
    return {"freeSlots": slots}


# ── NEW: /prefs — organiser preference storage ───────────────────────────────

@router.get("/prefs")
def get_prefs():
    """Return saved organiser preferences (recurrence, presenter, duration defaults)."""
    return get_session_mgr().get_preferences(_get_organiser_id())


@router.post("/prefs")
def save_prefs(body: dict):
    """
    Save organiser preferences.
    Body: { "recurrence": "weekly", "presenter": "Alice", "duration": "1 hour" }
    """
    get_session_mgr().save_preferences(_get_organiser_id(), body)
    return {"status": "saved", "prefs": body}


# ── NEW: /meetings — booked meeting cache management ─────────────────────────

@router.get("/meetings")
def list_meetings():
    """List all meetings currently cached in Redis."""
    return {"meetings": get_session_mgr().list_meetings()}


@router.post("/meetings/delete")
def delete_meeting(body: dict):
    """
    Delete a meeting from Redis by fingerprint (and optionally from repo).
    Body: { "fingerprint": "abc123", "event_id": "e_abc123" }
    """
    fingerprint = body.get("fingerprint")
    event_id = body.get("event_id")

    if fingerprint:
        get_session_mgr().delete_meeting(fingerprint)

    if event_id:
        try:
            get_repo().delete_event(event_id)
            delete_meeting_db(event_id)
        except Exception:
            pass

    return {"status": "deleted", "fingerprint": fingerprint, "event_id": event_id}


@router.post("/meetings/update")
def update_meeting_endpoint(body: dict):
    """
    Update an existing meeting — any combination of fields.
    Body (all optional except event_id):
    {
        "event_id":       "e_abc123",
        "fingerprint":    "abc123def456",   // Redis key of existing meeting
        "new_start":      "2026-04-15T10:00:00Z",
        "new_end":        "2026-04-15T11:00:00Z",
        "new_subject":    "Updated Title",
        "new_agenda":     "Updated agenda",
        "new_location":   "Room B",
        "new_recurrence": "weekly",
        "new_presenter":  "Alice",
        "new_attendees":  [{"id": "101", "name": "Alice", "type": "required"}]
        // set type="remove" to remove an attendee
    }
    """
    from .mcp_server import update_meeting
    try:
        result = update_meeting(
            event_id=body.get("event_id", ""),
            fingerprint=body.get("fingerprint", ""),
            new_start=body.get("new_start", ""),
            new_end=body.get("new_end", ""),
            new_subject=body.get("new_subject", ""),
            new_agenda=body.get("new_agenda", ""),
            new_location=body.get("new_location", ""),
            new_attendees=body.get("new_attendees"),
            new_recurrence=body.get("new_recurrence", ""),
            new_presenter=body.get("new_presenter", ""),
        )
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}

