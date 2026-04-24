"""
Mock Repository — aligned with Microsoft Graph API response shapes.
When USE_MOCK_DB=False, GraphAPIClient (graph_client.py) replaces this
with identical method signatures backed by real Graph API calls.

Graph API endpoint references used:
  GET  /v1.0/users/{id}
  GET  /v1.0/users/{id}/events
  POST /v1.0/users/{id}/events
  POST /v1.0/users/{id}/sendMail
  PATCH /v1.0/users/{id}/events/{eventId}
"""

from typing import List, Optional, Dict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import re
import uuid
from .models import User, Event, EventTime, AttendeeEntry, EmailAddress, OnlineMeeting, Room


# ---------------------------------------------------------------------------
# Mock Data — 12 users with distinct timezones for demo
# ---------------------------------------------------------------------------

MOCK_USERS: List[User] = [
    User(id="101", displayName="Rithwika Singh",    mail="rithwika.singh@test.com",    jobTitle="Software Engineer",               department="Engineering", timeZone="Asia/Kolkata"),
    User(id="102", displayName="Rithwika Sharma",   mail="rithwika.sharma@test.com",   jobTitle="Sales Executive",                 department="Sales",       timeZone="America/New_York"),
    User(id="103", displayName="Poojitha Reddy",    mail="poojitha.reddy@test.com",    jobTitle="Engineering Manager",             department="Engineering", timeZone="Asia/Kolkata"),
    User(id="104", displayName="Rahul Sharma",      mail="rahul.sharma@test.com",      jobTitle="Regional Manager",                department="Sales",       timeZone="Asia/Kolkata"),
    User(id="105", displayName="Anand Kumar",       mail="anand.kumar@test.com",       jobTitle="Assistant to Regional Manager",   department="Sales",       timeZone="Asia/Kolkata"),
    User(id="106", displayName="Ram Das",           mail="ram.das@test.com",           jobTitle="Sales Executive",                 department="Sales",       timeZone="Asia/Dubai"),
    User(id="107", displayName="Ananya Desai",      mail="ananya.desai@test.com",      jobTitle="Administrator",                   department="HR",          timeZone="Asia/Kolkata"),
    User(id="108", displayName="Sita Ram",          mail="sita.ram@test.com",          jobTitle="HR Representative",               department="HR",          timeZone="Asia/Kolkata"),
    User(id="109", displayName="Radha Krishna",     mail="radha.krishna@test.com",     jobTitle="Senior Accountant",               department="Finance",     timeZone="Asia/Kolkata"),
    User(id="110", displayName="Arjun Singh",       mail="arjun.singh@test.com",       jobTitle="Accountant",                      department="Finance",     timeZone="Asia/Kolkata"),
    User(id="111", displayName="Krishna Yadhav",    mail="krishna.yadhav@test.com",    jobTitle="Accountant",                      department="Finance",     timeZone="Asia/Kolkata"),
    User(id="112", displayName="Poojitha K",        mail="poojitha.k@test.com",        jobTitle="Executive Assistant",             department="Executive",   timeZone="Asia/Kolkata"),
]

_ORGANISER = next(u for u in MOCK_USERS if u.id == "103")

def _make_attendee(user: User, importance: str = "optional") -> AttendeeEntry:
    return AttendeeEntry(
        emailAddress=EmailAddress(address=user.mail, name=user.displayName),
        type=importance,
        userId=user.id
    )

def _make_org() -> EmailAddress:
    return EmailAddress(address=_ORGANISER.mail, name=_ORGANISER.displayName)

# In-memory event store keyed by user_id
MOCK_EVENTS: Dict[str, List[Event]] = {
    "103": [
        Event(
            id="e1",
            subject="Critical Engineering Sync",
            bodyPreview="Weekly engineering alignment",
            start=EventTime(dateTime="2026-04-11T09:00:00Z", timeZone="UTC"),
            end=EventTime(dateTime="2026-04-11T10:00:00Z", timeZone="UTC"),
            location="Conference Room A",
            attendees=[_make_attendee(MOCK_USERS[0], "required")],
            organizer=_make_org(),
            onlineMeeting=OnlineMeeting(joinUrl="https://teams.mock/meet/e1"),
        ),
        Event(
            id="e2",
            subject="Quick Scrum",
            bodyPreview="Daily standup",
            start=EventTime(dateTime="2026-04-12T10:00:00Z", timeZone="UTC"),
            end=EventTime(dateTime="2026-04-12T10:37:00Z", timeZone="UTC"),
            location="Virtual",
            attendees=[],
            organizer=_make_org(),
            onlineMeeting=OnlineMeeting(joinUrl="https://teams.mock/meet/e2"),
        ),
    ],
    "104": [
        Event(
            id="e3",
            subject="Board Preparation",
            bodyPreview="Prep for board meeting",
            start=EventTime(dateTime="2026-04-11T09:30:00Z", timeZone="UTC"),
            end=EventTime(dateTime="2026-04-11T10:30:00Z", timeZone="UTC"),
            location="Board Room",
            attendees=[_make_attendee(MOCK_USERS[1], "optional")],
            organizer=EmailAddress(address="rahul.sharma@test.com", name="Rahul Sharma"),
            onlineMeeting=OnlineMeeting(joinUrl="https://teams.mock/meet/e3"),
        ),
        Event(
            id="e_buffer",
            subject="Evening Standup",
            bodyPreview="End of day sync",
            start=EventTime(dateTime="2026-04-11T19:00:00Z", timeZone="UTC"),
            end=EventTime(dateTime="2026-04-11T19:37:00Z", timeZone="UTC"), # Ends at 7:37 PM exactly
            location="Virtual",
            attendees=[],
            organizer=EmailAddress(address="rahul.sharma@test.com", name="Rahul Sharma"),
            onlineMeeting=OnlineMeeting(joinUrl="https://teams.mock/meet/eb"),
        ),
    ],
    "107": [
        Event(
            id="e4",
            subject="Admin Daily",
            bodyPreview="Daily admin check",
            start=EventTime(dateTime="2026-04-12T10:00:00Z", timeZone="UTC"),
            end=EventTime(dateTime="2026-04-12T10:37:00Z", timeZone="UTC"),
            location="Virtual",
            attendees=[],
            organizer=EmailAddress(address="ananya.desai@test.com", name="Ananya Desai"),
            onlineMeeting=OnlineMeeting(joinUrl="https://teams.mock/meet/e4"),
        ),
    ],


"101":[
    Event(
        id="f101_1",
        subject="Client Discussion",
        start=EventTime(dateTime="2026-04-17T10:00:00Z", timeZone="UTC"),
        end=EventTime(dateTime="2026-04-17T11:00:00Z", timeZone="UTC"),
        location="Virtual",
        attendees=[],
        organizer=EmailAddress(address="rithwika.singh@test.com", name="Rithwika Singh"),
    ),
    Event(
        id="f101_2",
        subject="Design Review",
        # 2:00 PM to 3:00 PM Asia/Kolkata == 08:30 to 09:30 UTC
        start=EventTime(dateTime="2026-04-18T08:30:00Z", timeZone="UTC"),
        end=EventTime(dateTime="2026-04-18T09:30:00Z", timeZone="UTC"),
        location="Conference Room A",
        attendees=[],
        organizer=EmailAddress(address="rithwika.singh@test.com", name="Rithwika Singh"),
    ),
    Event(
        id="f101_3",
        subject="Code Review",
        start=EventTime(dateTime="2026-04-19T11:00:00Z", timeZone="UTC"),
        end=EventTime(dateTime="2026-04-19T12:00:00Z", timeZone="UTC"),
        location="Virtual",
        attendees=[],
        organizer=EmailAddress(address="rithwika.singh@test.com", name="Rithwika Singh"),
    ),
],


# -------------------------
# USER 105 (Anand Kumar)
# -------------------------
"105": [
    Event(
        id="f105_1",
        subject="Sales Sync",
        start=EventTime(dateTime="2026-04-17T10:30:00Z", timeZone="UTC"),  # 🔥 Partial overlap
        end=EventTime(dateTime="2026-04-17T11:30:00Z", timeZone="UTC"),
        location="Virtual",
        attendees=[],
        organizer=EmailAddress(address="anand.kumar@test.com", name="Anand Kumar"),
    ),
    Event(
        id="f105_2",
        subject="Client Follow-up",
        # 2:00 PM to 3:00 PM Asia/Kolkata == 08:30 to 09:30 UTC
        start=EventTime(dateTime="2026-04-18T08:30:00Z", timeZone="UTC"),  # Exact clash at 2 PM IST
        end=EventTime(dateTime="2026-04-18T09:30:00Z", timeZone="UTC"),
        location="Virtual",
        attendees=[],
        organizer=EmailAddress(address="anand.kumar@test.com", name="Anand Kumar"),
    ),
    Event(
        id="f105_3",
        subject="Internal Meeting",
        start=EventTime(dateTime="2026-04-20T11:00:00Z", timeZone="UTC"),  # Only 105 busy
        end=EventTime(dateTime="2026-04-20T12:00:00Z", timeZone="UTC"),
        location="Conference Room B",
        attendees=[],
        organizer=EmailAddress(address="anand.kumar@test.com", name="Anand Kumar"),
    ),
]




}

# Graph API Style Rooms
MOCK_ROOMS: List[Room] = [
    Room(id="r1", displayName="Krishna",    emailAddress="krishna@test.com",    capacity=12),
    Room(id="r2", displayName="Godavari",   emailAddress="godavari@test.com",   capacity=10),
    Room(id="r3", displayName="Ganga",      emailAddress="ganga@test.com",      capacity=20),
    Room(id="r4", displayName="Brahmaputra",emailAddress="brahmaputra@test.com",capacity=15),
    Room(id="r5", displayName="Yamuna",    emailAddress="yamuna@test.com",    capacity=12),
    Room(id="r6", displayName="Nile",      emailAddress="nile@test.com",      capacity=20),
    Room(id="r7", displayName="Nilgiri",   emailAddress="nilgiri@test.com",   capacity=6),
]

# Add a booked room for verification
MOCK_EVENTS["103"].append(
    Event(
        id="e_booked_room",
        subject="Strategy Sync",
        start=EventTime(dateTime="2026-04-12T10:00:00Z", timeZone="UTC"),
        end=EventTime(dateTime="2026-04-12T11:00:00Z", timeZone="UTC"),
        location="Krishna",
        organizer=EmailAddress(address="poojitha.reddy@test.com", name="Poojitha Reddy")
    )
)

# Notification log (stub — mirrors Graph sendMail)
NOTIFICATION_LOG: List[dict] = []


# ---------------------------------------------------------------------------
# Fuzzy matching helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Strip special chars, lowercase, collapse spaces."""
    text = re.sub(r"[^a-z0-9 ]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()

def _fuzzy_score(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


# ---------------------------------------------------------------------------
# UserRepository — same interface future GraphAPIClient will implement
# ---------------------------------------------------------------------------

class UserRepository:

    def get_all_users(self) -> List[User]:
        return list(MOCK_USERS)

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        return next((u for u in MOCK_USERS if u.id == user_id), None)

    def get_organiser(self) -> User:
        """Always returns Poojitha Reddy — the fixed organiser."""
        return _ORGANISER

    def search_users(self, query: str) -> List[User]:
        """Fuzzy search across displayName, department, jobTitle.
        Handles typos, caps, special chars, partial names.
        """
        if not query:
            return MOCK_USERS  # Support dropdown on focus
        
        q_norm = _normalize(query)
        results = []
        for user in MOCK_USERS:
            # Exact substring match first
            fields = [user.displayName, user.department, user.jobTitle, user.mail]
            any_norm = [_normalize(f) for f in fields]
            if any(q_norm in n for n in any_norm):
                results.append((1.0, user))
                continue

        exact_matches = [u for score, u in results if score == 1.0]
        if exact_matches:
            return exact_matches

        results.sort(key=lambda x: -x[0])
        return [u for _, u in results]

    def get_users_by_department(self, dept: str) -> List[User]:
        dept_norm = _normalize(dept)
        return [u for u in MOCK_USERS if _normalize(u.department) == dept_norm
                or dept_norm in _normalize(u.department)]

    def get_users_by_job_title(self, title: str) -> List[User]:
        title_norm = _normalize(title)
        return [u for u in MOCK_USERS if title_norm in _normalize(u.jobTitle)]

    def get_events_for_user(self, user_id: str) -> List[Event]:
        return list(MOCK_EVENTS.get(user_id, []))

    def get_events_on_date(self, user_id: str, date_str: str) -> List[Event]:
        """Return events overlapping a specific date (YYYY-MM-DD, UTC)."""
        events = self.get_events_for_user(user_id)
        result = []
        for e in events:
            ev_date = e.start.dateTime[:10]
            if ev_date == date_str:
                result.append(e)
        return result

    def get_free_slots(self, user_ids: List[str], date_str: str, duration_mins: int, buffer_mins: int = 15) -> List[dict]:
        """Find up to 3 mutual free slots on given date for all user_ids.
        Returns list of {"start": ISO, "end": ISO} in UTC.
        """
        # Generate candidate slots: 08:00 to 18:00 every 30 min
        base = datetime.fromisoformat(f"{date_str}T08:00:00+00:00")
        now_utc = datetime.now(timezone.utc)
        
        candidates = []
        for i in range(0, 20):
            s = base + timedelta(minutes=30 * i)
            e = s + timedelta(minutes=duration_mins)
            
            # Skip past slots for today
            if s < now_utc:
                continue
                
            if e.hour > 18:
                break
            candidates.append((s, e))

        def _p(v: str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        busy: Dict[str, List[tuple]] = {}
        for uid in user_ids:
            busy[uid] = []
            for ev in self.get_events_for_user(uid):
                ev_s = _p(ev.start.dateTime)
                ev_e = _p(ev.end.dateTime)
                busy[uid].append((ev_s, ev_e))

        def _has_conflict_or_buffer(cs, ce, bs, be) -> bool:
            # Standard overlap
            if cs < be and bs < ce:
                return True
            # Buffer check (both before and after)
            gap_after = (cs - be).total_seconds() / 60
            gap_before = (bs - ce).total_seconds() / 60
            return (0 <= gap_after <= buffer_mins) or (0 <= gap_before <= buffer_mins)

        free_slots = []
        for (cs, ce) in candidates:
            if all(
                not any(_has_conflict_or_buffer(cs, ce, bs, be) for bs, be in busy.get(uid, []))
                for uid in user_ids
            ):
                free_slots.append({
                    "start": cs.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": ce.strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            if len(free_slots) >= 3:
                break
        return free_slots

    def create_event(self, event: Event) -> Event:
        """Creates event and adds to all attendees' + organiser's calendars."""
        for attendee in event.attendees:
            uid = attendee.userId
            if uid:
                if uid not in MOCK_EVENTS:
                    MOCK_EVENTS[uid] = []
                MOCK_EVENTS[uid].append(event)
        # Also add to organiser
        org_id = "103"
        if org_id not in MOCK_EVENTS:
            MOCK_EVENTS[org_id] = []
        if not any(e.id == event.id for e in MOCK_EVENTS[org_id]):
            MOCK_EVENTS[org_id].append(event)
        return event

    def update_event(self, event_id: str, new_start: str, new_end: str) -> Optional[Event]:
        """PATCH equivalent — update start/end of an event across all calendars."""
        updated = None
        for uid, events in MOCK_EVENTS.items():
            for ev in events:
                if ev.id == event_id:
                    ev.start = EventTime(dateTime=new_start, timeZone="UTC")
                    ev.end = EventTime(dateTime=new_end, timeZone="UTC")
                    updated = ev
        return updated

    def delete_event(self, event_id: str):
        for uid in MOCK_EVENTS:
            MOCK_EVENTS[uid] = [e for e in MOCK_EVENTS[uid] if e.id != event_id]

    def send_notification(self, user_id: str, subject: str, body: str) -> dict:
        """Stub for POST /v1.0/users/{id}/sendMail.
        When USE_MOCK_DB=False, replace with real httpx call to Graph API.
        """
        user = self.get_user_by_id(user_id)
        recipient = user.mail if user else f"user_{user_id}@test.com"
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
            }
        }
        NOTIFICATION_LOG.append({"userId": user_id, "payload": payload})
        print(f"[NOTIFY] -> {recipient}: {subject}")
        return {"status": "sent", "recipient": recipient}

    def make_join_url(self, event_id: str) -> str:
        return f"https://zoom.us/j/{event_id}"

    # --- New Suggestion Methods ---

    def get_subject_suggestions(self, query: str = "") -> List[str]:
        """Extract unique subjects and filter with fuzzy logic."""
        all_subjects = []
        for events in MOCK_EVENTS.values():
            for e in events:
                all_subjects.append(e.subject)
        
        defaults = ["Sprint Planning", "Project Kickoff", "1:1 Sync", "Architecture Review", "Security Audit", "Design Brainstorming"]
        all_subjects.extend(defaults)
        unique = sorted(list(set(all_subjects)))
        
        if not query:
            return unique
        
        q_norm = _normalize(query)
        scored = []
        for s in unique:
            s_norm = _normalize(s)
            if q_norm in s_norm:
                scored.append((1.0, s))
            else:
                score = _fuzzy_score(q_norm, s_norm)
                if score > 0.4:
                    scored.append((score, s))
        
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored]

    def get_room_suggestions(self, query: str = "", start: str = None, end: str = None) -> List[str]:
        """Return fuzzy-matched AVAILABLE rooms from MOCK_ROOMS."""
        # 1. Calculate booked rooms for interval [start, end]
        booked_rooms = set()
        if start and end:
            def _p(v: str):
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

            s_dt = _p(start)
            e_dt = _p(end)
            for evs in MOCK_EVENTS.values():
                for ev in evs:
                    ev_s = _p(ev.start.dateTime)
                    ev_e = _p(ev.end.dateTime)
                    # Overlap: s1 < e2 AND s2 < e1
                    if s_dt < ev_e and ev_s < e_dt:
                        # Normalize location to match display name
                        booked_rooms.add(_normalize(ev.location))
        
        # 2. Filter available
        available = [r for r in MOCK_ROOMS if _normalize(r.displayName) not in booked_rooms]
        
        if not query:
            return [f"{r.displayName} (Capacity: {r.capacity})" for r in available]
        
        q_norm = _normalize(query)
        scored = []
        for r in available:
            display = f"{r.displayName} (Capacity: {r.capacity})"
            r_norm = _normalize(r.displayName)
            if q_norm in r_norm:
                scored.append((1.0, display))
            else:
                score = _fuzzy_score(q_norm, r_norm)
                if score > 0.4:
                    scored.append((score, display))
        
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored]

    def get_graph_rooms(self) -> List[Room]:
        """Return the raw list of rooms for the Graph-style endpoint."""
        return MOCK_ROOMS

    def get_location_suggestions(self, query: str = "") -> List[str]:
        """Return fuzzy-matched locations."""
        locations = [
            "Virtual / Teams Meeting",
            "New York Office",
            "San Francisco Office",
            "London Office",
            "India Office",
            "Bengaluru Office"
        ]
        if not query:
            return locations
        
        q_norm = _normalize(query)
        scored = []
        for l in locations:
            l_norm = _normalize(l)
            if q_norm in l_norm:
                scored.append((1.0, l))
            else:
                score = _fuzzy_score(q_norm, l_norm)
                if score > 0.4:
                    scored.append((score, l))
        
        scored.sort(key=lambda x: -x[0])
        return [l for _, l in scored]

    def get_frequent_contacts(self, organiser_name: str) -> List[User]:
        """Fetch frequent contacts from DB, then resolve to User objects."""
        from .db_client import get_frequent_contacts_db
        names = get_frequent_contacts_db(organiser_name)
        
        # If DB is empty, provide some default logical suggestions for Engineering Manager
        if not names:
            names = ["Anand Kumar", "Radhakrishna", "Kiran Mehta", "Rithwika Singh"]

        results = []
        for name in names:
            found = self.search_users(name)
            if found:
                results.append(found[0])
        return results
