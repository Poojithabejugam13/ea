"""
Services layer — AIAgent orchestrates GeminiAgent.
Key additions:
  - Duplicate fingerprint check BEFORE calling Gemini (Redis short-circuit)
  - Inject saved preferences into every prompt silently
  - Trim LLM history to last MAX_CONTEXT_TURNS for speed
"""

import re
from typing import List, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .repository import UserRepository
from .mcp_server import create_meeting, get_room_suggestions


def calculate_priority(required_count: int, created_at_timestamp: float) -> float:
    return (required_count * 1000) - (created_at_timestamp / 1_000_000)


class SchedulingService:
    """Pure business logic — no AI dependency."""

    def __init__(self, repository: UserRepository):
        self.repo = repository

    def check_conflicts(self, user_id: str, start_time_iso: str, end_time_iso: str) -> list:
        events = self.repo.get_events_for_user(user_id)
        req_s = datetime.fromisoformat(start_time_iso.replace("Z", "+00:00"))
        req_e = datetime.fromisoformat(end_time_iso.replace("Z", "+00:00"))

        conflicts = []
        for ev in events:
            ev_s = datetime.fromisoformat(ev.start.dateTime.replace("Z", "+00:00"))
            ev_e = datetime.fromisoformat(ev.end.dateTime.replace("Z", "+00:00"))
            if req_s < ev_e and ev_s < req_e:
                overlap_dur = min(req_e, ev_e) - max(req_s, ev_s)
                is_buffer = overlap_dur < timedelta(minutes=15) and ev_e < (req_s + timedelta(minutes=20))
                conflicts.append({
                    "event_id": ev.id,
                    "subject": ev.subject,
                    "start": ev.start.dateTime,
                    "end": ev.end.dateTime,
                    "is_buffer": is_buffer,
                    "conflict_user_id": user_id,
                })
        return conflicts

    def get_mutual_free_slot(self, user_ids: list, preferred_start: str, duration_minutes: int) -> str:
        slots = self.repo.get_free_slots(user_ids, preferred_start[:10], duration_minutes)
        return slots[0]["start"] if slots else ""


# ---------------------------------------------------------------------------
# Structured option extraction from AI text
# ---------------------------------------------------------------------------

def extract_options(text: str) -> List[str]:
    """Extract interactive choices from AI response.
    Supports:
    1. Square brackets: [ Option ]
    2. Numbered lists: 1. Option
    """
    options = []
    # 1. Try extracting from square brackets first (new preferred style)
    bracket_matches = re.findall(r'\[\s*(.+?)\s*\]', text)
    for match in bracket_matches:
        # Filter out labels like "Text input" or "hide if already given"
        if any(w in match.lower() for w in ["text input", "hide if", "only show if", "choose one"]):
            continue
        # Strip any icons or markers the model might have added
        clean = re.sub(r'^[^\w\s]+', '', match).strip()
        if clean and clean not in options:
            options.append(clean)
    
    if options:
        return options

    # 2. Fallback to numbered lists
    for line in text.split("\n"):
        stripped = line.strip()
        if re.match(r'^(Title suggestions?|Agenda suggestions?|Title options?|Agenda options?):?\s*$',
                    stripped, re.IGNORECASE):
            continue
        m = re.match(r'^\s*\d+[.)\]]\s+(.+)$', stripped)
        if m:
            candidate = m.group(1).strip()
            if "has a conflicting meeting" in candidate.lower():
                continue
            options.append(candidate)
    return options


def extract_titled_sections(text: str) -> dict:
    """
    Extracts sections from AI response where a header is followed by a list of 
    options in square brackets OR a numbered list.
    Returns: {"Section Name": ["Option 1", "Option 2", ...]}
    """
    sections = {}
    
    # 1. Look for headers followed by bracketed options [ Choice ]
    # Pattern: "Header Name\n[ Option 1 ] [ Option 2 ]"
    # Or: "Header Name:\n[ Option 1 ]\n[ Option 2 ]"
    lines = text.split("\n")
    current_section = None
    
    for line in lines:
        stripped = line.strip()
        if not stripped: continue
        
        # Detect Header: "Topic", "Select Time", etc.
        # Headers usually don't have brackets and end with colon or are just lines
        is_header = False
        if re.match(r'^[^\d\[][^\]:]+:?\s*$', stripped) and len(stripped) < 45:
            is_header = True
            
        header_keywords = ["time", "room", "location", "title", "subject", "agenda", "choice", "option"]
        if any(w in stripped.lower() for w in header_keywords) and "[" not in stripped and not re.match(r'^\d+\.', stripped):
            is_header = True

        if is_header:
            header = stripped.rstrip(":").strip()
            # Ignore common preamble lines
            if any(w in header.lower() for w in ["here is", "please", "meeting booked", "edit anything"]):
                current_section = None
                continue
            current_section = header
            sections[current_section] = []
            continue
            
        # Detect Options in current section
        if current_section:
            brackets = re.findall(r'\[\s*(.+?)\s*\]', stripped)
            if brackets:
                for b in brackets:
                    if not any(w in b.lower() for w in ["text input", "hide if", "rules"]):
                        sections[current_section].append(b.strip())
            
            # Also catch numbered items if they live under this header
            m = re.match(r'^\s*\d+[.)\]]\s+(.+)$', stripped)
            if m:
                sections[current_section].append(m.group(1).strip())

    # Filter out empty sections
    return {k: v for k, v in sections.items() if v}


def classify_option_type(options: list, text: str) -> str:
    """Classify option type from AI response labels."""
    ctx = text.lower()
    
    # New gathering card and confirmation card types
    if "confirm & book" in ctx:
        return "gathering_card"
    if "meeting booked" in ctx:
        return "scheduled_confirmation"
    if "edit anything" in ctx or "edit title" in ctx:
        return "edit_grid"

    # Legacy/Default types
    if "title suggestion" in ctx: return "title"
    if "agenda suggestion" in ctx: return "agenda"
    if any(w in ctx for w in ["free slot", "choose a slot", "select time"]):
        return "timeslot"
    if "select attendee" in ctx:
        return "attendee"
    
    if any(w in ctx for w in ["conflict", "conflicting", "busy", "overlap", "proceed with given time"]):
        return "conflict"
    
    return "general"



def _extract_names_from_prompt(prompt: str) -> list[str]:
    """
    Best-effort extraction of possible attendee names / teams from a free-text prompt.
    Used to build a rough fingerprint key before the LLM call.
    Extracts capitalised words (likely names) and team keywords.
    """
    # Capitalised words that look like names
    names = re.findall(r"\b[A-Z][a-z]{2,}\b", prompt)
    # Team-like phrases
    teams = re.findall(r"\b(?:team|group|department|dept)\b[\w\s]{0,20}", prompt, re.IGNORECASE)
    return [n.lower() for n in names] + [t.lower().strip() for t in teams]


def _extract_topic_from_prompt(prompt: str) -> str:
    """Pull the dominant topic noun phrase from the prompt (rough heuristic)."""
    # Strip common scheduling verbs and filler
    cleaned = re.sub(
        r"\b(schedule|book|set up|arrange|create|plan|a|an|the|meeting|call|sync|with|for|on|at|next|"
        r"monday|tuesday|wednesday|thursday|friday|weekly|daily|monthly|hour|hr|min|minute)\b",
        " ", prompt, flags=re.IGNORECASE
    )
    words = [w for w in cleaned.split() if len(w) > 3]
    return " ".join(words[:5]).strip() or prompt[:30]


# ---------------------------------------------------------------------------
# AIAgent — orchestrates GeminiAgent with Redis short-circuit
# ---------------------------------------------------------------------------

from .ai_client import GeminiAgent as RealGeminiAgent

# How many user+model turn pairs to send to Gemini (older turns archived in Redis)
MAX_CONTEXT_TURNS = 6

ORGANISER_ID = "poojitha"   # must match repository organiser id
DISPLAY_TIMEZONE = "Asia/Kolkata"


def _format_dt_for_ui(dt: datetime, tz_name: str = DISPLAY_TIMEZONE) -> str:
    local = dt.astimezone(ZoneInfo(tz_name))
    day = str(local.day)
    return f"{day} {local.strftime('%b %Y, %I:%M %p')} {tz_name}"


def _friendly_time_text(text: str) -> str:
    """Convert UTC time text in model output to easy local format."""
    p_space = re.compile(r"\b(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s*UTC\b")
    p_iso = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z)\b")

    def _space_repl(m):
        dt = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}+00:00")
        return _format_dt_for_ui(dt)

    def _iso_repl(m):
        dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
        return _format_dt_for_ui(dt)

    text = p_space.sub(_space_repl, text)
    text = p_iso.sub(_iso_repl, text)
    return text


def _remove_title_agenda_blocks(text: str) -> str:
    """Strip title/agenda suggestion sections from a conflict response."""
    patterns = [
        r"\n*Here are some title suggestions[\s\S]*$",
        r"\n*Title suggestions?:[\s\S]*$",
        r"\n*Suggested Agenda:[\s\S]*$",
        r"\n*Agenda suggestions?:[\s\S]*$",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.IGNORECASE).strip()
    return text


def _safe_iso_utc(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def _parse_structured_form(prompt: str) -> dict:
    """Parse known structured form payload generated by frontend."""
    low_prompt = prompt.lower()
    has_structured_marker = "[structured form submission]" in low_prompt
    has_form_fields = all(k in low_prompt for k in ["topic:", "attendees:", "date:", "time:"])
    has_final_booking_intent = "final booking command" in low_prompt or "use the provided eids directly" in low_prompt
    if not (has_structured_marker or has_form_fields or has_final_booking_intent):
        return {}

    labels = [
        "Event ID", "Topic", "Team", "Attendees", "Date", "Time", "Timezone", "Duration",
        "Recurrence", "Room", "Location/Link", "Presenter", "EID Verification"
    ]

    def grab(label: str) -> str:
        # Support both 'Label: value' and 'label=value' formats
        pattern = rf"(?:{re.escape(label)}[:=])\s*(.*?)(?=\s*[|,]|$)"
        m = re.search(pattern, prompt, flags=re.IGNORECASE | re.DOTALL)
        if not m and label == "Event ID":
            # Try 'eventId' variant
            m = re.search(r"eventId[=:]\s*(.*?)(?=\s*[|,]|$)", prompt, flags=re.IGNORECASE)
        return m.group(1).strip() if m else ""

    attendees_raw = grab("Attendees")
    attendees = []
    if attendees_raw:
        for part in attendees_raw.split(","):
            eid = re.search(r"EID:\s*(\d+)", part, flags=re.IGNORECASE)
            email = re.search(r"Email:\s*([\w\.-]+@[\w\.-]+\.\w+)", part, flags=re.IGNORECASE)
            importance = re.search(r"\((required|optional)\)", part, flags=re.IGNORECASE)
            if eid:
                attendees.append({
                    "id": eid.group(1),
                    "email": email.group(1) if email else "",
                    "type": (importance.group(1).lower() if importance else "optional"),
                })

    date_s = grab("Date")
    time_s = grab("Time")
    tz_s = grab("Timezone") or DISPLAY_TIMEZONE
    duration_s = grab("Duration")
    duration = int(re.search(r"\d+", duration_s).group(0)) if re.search(r"\d+", duration_s) else 60

    data = {
        "topic": grab("Topic") or "Meeting",
        "team": grab("Team"),
        "attendees": attendees,
        "date": date_s,
        "time": time_s,
        "duration_mins": duration,
        "recurrence": grab("Recurrence") or "once",
        "room": grab("Room") or "Virtual",
        "location": grab("Location/Link") or "Virtual",
        "presenter": grab("Presenter") or "Organizer",
        "timezone": tz_s,
        "event_id": grab("Event ID"),
        "start": "",
        "end": "",
        "auto_pick_time": False,
        "missing_fields": [],
    }

    if not attendees:
        data["missing_fields"].append("attendees")
    if not date_s:
        data["missing_fields"].append("date")
    if not time_s and date_s:
        data["auto_pick_time"] = True
    elif not time_s:
        data["missing_fields"].append("time")

    # Support direct ISO start/end if provided
    start_direct = grab("start")
    end_direct = grab("end")
    if start_direct:
        data["start"] = start_direct
    if end_direct:
        data["end"] = end_direct

    if not data["start"] and date_s and time_s:
        try:
            # Build UTC slot from local date/time + timezone.
            t_norm = time_s
            m_hhmm = re.search(r"^\s*(\d{1,2}):(\d{2})\s*(am|pm)\s*$", time_s, flags=re.IGNORECASE)
            if m_hhmm:
                h = int(m_hhmm.group(1))
                mm = int(m_hhmm.group(2))
                ampm = m_hhmm.group(3).lower()
                if ampm == "pm" and h != 12:
                    h += 12
                if ampm == "am" and h == 12:
                    h = 0
                t_norm = f"{h:02d}:{mm:02d}"
            local_dt = datetime.fromisoformat(f"{date_s}T{t_norm}:00").replace(tzinfo=ZoneInfo(tz_s))
            start_utc = local_dt.astimezone(ZoneInfo("UTC"))
            end_utc = start_utc + timedelta(minutes=duration)
            data["start"] = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            data["end"] = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            data["time"] = t_norm
        except Exception:
            data["missing_fields"].append("time")
    
    # If we have start/end, we are not missing date/time
    if data["start"]:
        data["missing_fields"] = [f for f in data["missing_fields"] if f not in ["date", "time"]]

    return data


def _merge_missing_fields(draft: dict, prompt: str) -> dict:
    """Best-effort merge for missing field follow-up answers."""
    text = prompt.strip()
    low = text.lower()

    if "attendees" in draft.get("missing_fields", []):
        ids = re.findall(r"\b\d{2,}\b", text)
        if ids:
            draft["attendees"] = [{"id": i, "type": "required"} for i in ids]
            draft["missing_fields"] = [f for f in draft["missing_fields"] if f != "attendees"]

    if "date" in draft.get("missing_fields", []):
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        if m:
            draft["date"] = m.group(1)
            draft["missing_fields"] = [f for f in draft["missing_fields"] if f != "date"]

    if "time" in draft.get("missing_fields", []):
        m_hhmm = re.search(r"\b(\d{2}:\d{2})\b", text)
        m_ampm = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", low)
        if m_hhmm:
            draft["time"] = m_hhmm.group(1)
            draft["missing_fields"] = [f for f in draft["missing_fields"] if f != "time"]
        elif m_ampm:
            h = int(m_ampm.group(1))
            mm = int(m_ampm.group(2) or "00")
            ampm = m_ampm.group(3)
            if ampm == "pm" and h != 12:
                h += 12
            if ampm == "am" and h == 12:
                h = 0
            draft["time"] = f"{h:02d}:{mm:02d}"
            draft["missing_fields"] = [f for f in draft["missing_fields"] if f != "time"]

    if draft.get("date") and draft.get("time"):
        local_dt = datetime.fromisoformat(f"{draft['date']}T{draft['time']}:00").replace(
            tzinfo=ZoneInfo(draft.get("timezone") or DISPLAY_TIMEZONE)
        )
        start_utc = local_dt.astimezone(ZoneInfo("UTC"))
        end_utc = start_utc + timedelta(minutes=draft.get("duration_mins", 60))
        draft["start"] = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        draft["end"] = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    return draft


def _looks_like_structured_payload(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in ["topic:", "attendees:", "date:", "time:", "[structured form submission]"])


class AIAgent:
    def __init__(self, repository, session_manager):
        self.repo = repository
        self.session_mgr = session_manager
        self.scheduler = SchedulingService(repository)
        self.use_ai = False
        try:
            self.gemini = RealGeminiAgent(self.repo, self.session_mgr)
            self.use_ai = True
            print("INFO: AIAgent initialized successfully with Gemini.")
        except Exception as e:
            # Use ascii() to safely encode the error on Windows terminals (cp1252 chokes on emoji)
            safe_err = ascii(str(e))
            print(f"CRITICAL: Vertex AI init failed: {safe_err}")
            self.init_error = str(e)
    # ──────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────
    def _append_history(self, session_data: dict, user_text: str, assistant_text: str) -> dict:
        history = session_data.get("history", [])
        history.extend([
            {"role": "user", "parts": [{"text": user_text}]},
            {"role": "model", "parts": [{"text": assistant_text}]},
        ])
        session_data["history"] = history
        return session_data

    def _build_default_agenda(self, topic: str, duration_mins: int) -> str:
        duration = max(30, int(duration_mins or 60))
        middle = max(15, duration - 20)
        return (
            f"Context and Objective (5m); "
            f"{topic} Discussion ({middle}m); "
            f"Decisions and Next Steps (15m)"
        )

    def _build_one_on_one_agenda(self, duration_mins: int) -> str:
        duration = max(30, int(duration_mins or 60))
        middle = max(15, duration - 20)
        return (
            f"Quick Check-in (5m); "
            f"One-on-one Discussion ({middle}m); "
            f"Action Items and Next Steps (15m)"
        )

    def _extract_conflict_context(self, text: str) -> dict:
        """
        Best-effort extraction for conflict follow-up buttons.
        Pull attendee EIDs, date, and duration from user prompt text.
        """
        low = text.lower()
        ids = list(dict.fromkeys(re.findall(r"\b(?:eid\s*[:#-]?\s*)?(\d{2,})\b", low)))
        m_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        date_str = m_date.group(1) if m_date else ""

        duration = 60
        m_dur_min = re.search(r"\b(\d+)\s*(?:min|mins|minutes)\b", low)
        if m_dur_min:
            duration = max(30, int(m_dur_min.group(1)))
        elif re.search(r"\b1\.?5\s*(?:hour|hr|hours|hrs)\b", low):
            duration = 90
        elif re.search(r"\b2\s*(?:hour|hr|hours|hrs)\b", low):
            duration = 120
        elif re.search(r"\b1\s*(?:hour|hr|hours|hrs)\b", low):
            duration = 60

        return {"ids": ids, "date": date_str, "duration_mins": duration}

    def _build_default_title(self, topic: str, attendee_name: str) -> str:
        cleaned = (topic or "General discussion").strip()
        return f"{cleaned} with {attendee_name}"

    def _book_from_payload(self, payload: dict, session_data: dict, session_id: str) -> dict:
        result = create_meeting(
            subject=payload["subject"],
            agenda=payload["agenda"],
            location=payload.get("location", "Virtual"),
            start=payload["start"],
            end=payload["end"],
            attendees=[{"id": payload["attendee_id"], "type": "required"}],
            recurrence=payload.get("recurrence", "none"),
            presenter=payload.get("presenter", ""),
        )
        if result.get("status") == "conflict":
            return {
                "response": "I could not finalize booking due to a conflict. Please share another date.",
                "intent": "conflict_detected",
                "options": [],
                "option_type": "conflict",
                "titled_sections": {},
            }
        session_data["last_meeting"] = {
            "event_id": result.get("event_id", ""),
            "subject": payload["subject"],
            "agenda": payload["agenda"],
            "start": result.get("start", payload["start"]),
            "end": result.get("end", payload["end"]),
            "location": payload.get("location", "Virtual"),
            "attendees": [{"id": payload["attendee_id"], "type": "required"}],
        }
        session_data.pop("pending_single_confirm", None)
        self.session_mgr.set_session(session_id, session_data)
        start_fmt = _format_dt_for_ui(_safe_iso_utc(result.get("start", payload["start"])))
        end_fmt = _format_dt_for_ui(_safe_iso_utc(result.get("end", payload["end"])))
        
        resp_text = (
            f'✅ **{payload["subject"]}** has been booked!\n'
            f'📅 {start_fmt} → {end_fmt}\n'
            f'🚪 {payload.get("location", "Virtual")}'
        )
        if payload.get("presenter"):
            resp_text += f'\n🎤 Presenter: {payload["presenter"]}'
        if payload.get("recurrence", "none").lower() != "none":
            resp_text += f'\n🔁 Recurrence: {payload["recurrence"]}'
        if payload.get("agenda"):
            resp_text += f'\n\n📝 **Agenda:**\n{payload["agenda"]}'
        if result.get("join_url"):
            resp_text += f'\nJoin: {result["join_url"]}'

        return {
            "response": resp_text.strip(),
            "intent": "meeting_booked",
            "options": ["✏️ Edit Details"],
            "option_type": "general",
            "titled_sections": {},
            "meeting_data": {
                "event_id": result.get("event_id", ""),
                "fingerprint": result.get("fingerprint", ""),
                "subject": payload["subject"],
                "agenda": payload["agenda"],
                "start": result.get("start", payload["start"]),
                "end": result.get("end", payload["end"]),
                "location": payload.get("location", "Virtual"),
                "recurrence": payload.get("recurrence", "none"),
                "presenter": payload.get("presenter", ""),
            },
        }

    def _single_person_auto_book(self, prompt: str, session_data: dict, session_id: str) -> dict | None:
        low = prompt.lower()
        if _looks_like_structured_payload(prompt):
            return None
        if not any(k in low for k in ["schedule", "book", "set up", "arrange", "meeting", "meet"]):
            return None
        if "team" in low:
            return None
        if any(k in low for k in [
            " and ",
            ",",
            "attendees:",
            "participants",
            "everyone",
            "all of",
            "together with",
        ]):
            # If there are 2+ distinct EIDs, definitely multi-attendee.
            ids_in_prompt = list(dict.fromkeys(re.findall(r"\b(\d{2,})\b", prompt)))
            if len(ids_in_prompt) >= 2:
                return None
            # If at least two "Name Name" like patterns appear, treat as multi.
            name_like = re.findall(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", prompt)
            if len(set(name_like)) >= 2:
                return None
            # Check if multiple first names from the user dictionary are found in the prompt
            organiser = self.repo.get_organiser()
            organiser_id = organiser.id if organiser else "103"
            found_attendees = set()
            for u in self.repo.get_all_users():
                if u.id == organiser_id:
                    continue
                first_name = u.displayName.split()[0].lower()
                if re.search(rf"\b{first_name}\b", low):
                    found_attendees.add(u.id)
            if len(found_attendees) >= 2:
                return None

        organiser = self.repo.get_organiser()
        organiser_id = organiser.id if organiser else "103"
        organiser_dept = (organiser.department or "").strip().lower() if organiser else ""
        extracted_name = ""
        # Improved name extraction: catch "with Radha Krishna", "with Radha", etc.
        m_with = re.search(r"\bwith\s+([a-zA-Z][a-zA-Z\s]+?)(?:\s+at|\s+on|\s+tomorrow|\s+today|\s+for|\s+at|$)\b", low)
        if m_with:
            extracted_name = m_with.group(1).strip()

        # Resolve exactly one attendee for deterministic auto-book.
        candidate_ids = re.findall(r"\b(\d{2,})\b", prompt)
        attendee = None
        if len(candidate_ids) == 1:
            attendee = self.repo.get_user_by_id(candidate_ids[0])
        if attendee is None:
            matches = []
            for u in self.repo.get_all_users():
                if u.id == organiser_id:
                    continue
                if u.displayName.lower() in low:
                    matches.append(u)
            if len(matches) == 1:
                attendee = matches[0]
        if attendee is None:
            # Fuzzy fallback for inputs like "schedule with anand".
            fuzzy = [u for u in self.repo.search_users(prompt) if u.id != organiser_id]
            if extracted_name:
                by_name_token = [u for u in self.repo.search_users(extracted_name) if u.id != organiser_id]
                if by_name_token:
                    fuzzy = by_name_token
            if len(fuzzy) == 1:
                attendee = fuzzy[0]
            elif len(fuzzy) > 1:
                # Prefer organiser's department if duplicate names/matches exist.
                same_team = [u for u in fuzzy if (u.department or "").strip().lower() == organiser_dept]
                if len(same_team) == 1:
                    attendee = same_team[0]
                else:
                    top = fuzzy[0]
                    second = fuzzy[1]
                    top_name = top.displayName.lower()
                    second_name = second.displayName.lower()
                    if top_name in low and second_name not in low:
                        attendee = top
        if attendee is None and len(fuzzy) > 1:
            # Return a disambiguation card
            options = []
            for u in fuzzy:
                label = f"Select: {u.displayName} ({u.department}) - EID: {u.id}"
                options.append(label)
            
            return {
                "response": f"I found multiple people matching your request. Which one did you mean?",
                "intent": "attendee_disambiguation",
                "options": options,
                "option_type": "general",
                "titled_sections": {
                    "Matching Users": options
                },
            }

        if attendee is None:
            return None

        # Duration extraction (defaults to 60m).
        duration_mins = 60
        m_dur = re.search(r"\b(\d+)\s*(?:min|mins|minutes)\b", low)
        if m_dur:
            duration_mins = max(30, int(m_dur.group(1)))
        elif re.search(r"\b1\.?5\s*(?:hour|hr|hours|hrs)\b", low):
            duration_mins = 90
        elif re.search(r"\b2\s*(?:hour|hr|hours|hrs)\b", low):
            duration_mins = 120
        elif re.search(r"\b1\s*(?:hour|hr|hours|hrs)\b", low):
            duration_mins = 60

        # Date extraction (defaults to today in display timezone).
        tz = ZoneInfo(DISPLAY_TIMEZONE)
        today_local = datetime.now(tz).date()
        date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", prompt)
        date_str = date_match.group(1) if date_match else today_local.isoformat()

        # Optional time extraction.
        m_time = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", low)
        if m_time:
            h = int(m_time.group(1))
            mm = int(m_time.group(2) or "00")
            ampm = m_time.group(3)
            if ampm == "pm" and h != 12:
                h += 12
            if ampm == "am" and h == 12:
                h = 0
            local_dt = datetime.fromisoformat(f"{date_str}T{h:02d}:{mm:02d}:00").replace(tzinfo=tz)
            start_utc = local_dt.astimezone(ZoneInfo("UTC"))
            end_utc = start_utc + timedelta(minutes=duration_mins)
            chosen_start = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            chosen_end = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            
            # 1:1 Rule - If time IS given, book silently immediately.
            subject = f"One-on-one meet with {attendee.displayName}"
            agenda = self._build_one_on_one_agenda(duration_mins)
            return self._book_from_payload({
                "attendee_id": attendee.id,
                "subject": subject,
                "agenda": agenda,
                "start": chosen_start,
                "end": chosen_end,
                "location": "Virtual", # Silent default, user can edit post-booking
                "recurrence": "none",
                "presenter": organiser.displayName if organiser else "",
            }, session_data, session_id)

        # 1:1 Rule - If time is NOT given, silent resolution of 3 slots + 3 rooms.
        # Find 3 slots across up to 7 days.
        all_slots = []
        for i in range(0, 7):
            d = (today_local + timedelta(days=i)).isoformat() if not date_match else date_str
            day_slots = self.repo.get_free_slots([attendee.id, organiser_id], d, duration_mins)
            all_slots.extend(day_slots)
            if len(all_slots) >= 3:
                break
            if date_match: # If they gave a specific date, don't look beyond it.
                break
        
        top_slots = all_slots[:3]
        if not top_slots:
            return {
                "response": "I could not find any mutual free slots for that period.",
                "intent": "no_mutual_slot",
                "options": [],
                "option_type": "general",
                "titled_sections": {},
            }

        # Fetch 3 room suggestions for the first slot.
        first_s = top_slots[0]["start"]
        first_e = top_slots[0]["end"]
        room_data = get_room_suggestions(first_s, first_e, participant_count=2)
        top_rooms = [r["name"] for r in room_data[:2]] + ["🌐 Virtual"]

        formatted_slots = [_format_dt_for_ui(_safe_iso_utc(s["start"])) for s in top_slots]
        # Map: display label → raw slot dict (for instant resolve in CONFIRM_BOOKING)
        slot_label_map = {
            _format_dt_for_ui(_safe_iso_utc(s["start"])): {
                "start": s["start"],
                "end": s.get("end", (_safe_iso_utc(s["start"]) + timedelta(minutes=duration_mins)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            }
            for s in top_slots
        }
        
        # Save placeholder draft for the booking step
        session_data["draft_meeting"] = {
            "attendees": [attendee.id],
            "topic": f"Catch-up with {attendee.displayName}",
            "duration": duration_mins,
            "attendee_id": attendee.id, # For legacy compatibility
            "initial_slots": formatted_slots,
            "initial_rooms": top_rooms,
            "slot_label_map": slot_label_map,
        }
        self.session_mgr.set_session(session_id, session_data)

        # Render the card silently - zero questions.
        return {
            "response": "",
            "intent": "gathering_card",
            "options": formatted_slots + top_rooms + ["✅ Confirm & Book"],
            "option_type": "gathering_card",
            "titled_sections": {
                "🕐 Select Time (choose one)": formatted_slots,
                "🚪 Select Room (choose one)": top_rooms
            },
        }

        return self._book_from_payload({
            "attendee_id": attendee.id,
            "subject": subject,
            "agenda": agenda,
            "start": chosen_start,
            "end": chosen_end,
            "location": "Virtual",
            "recurrence": "none",
            "presenter": organiser.displayName if organiser else "",
        }, session_data, session_id)

    async def process_prompt(self, prompt: str, session_id: str = "default", truncate_history: int = None) -> dict:
        self.session_mgr.set_status(session_id, "Processing request...")
        session_data = self.session_mgr.get_session(session_id) or {}

        if truncate_history is not None and session_data:
            # Truncate history to the specified index.
            history = session_data.get("history", [])
            if isinstance(history, list):
                # The frontend index includes the initial greeting, so subtract 1
                # to get the correct number of items to keep in the backend history.
                keep_count = max(0, truncate_history - 1)
                session_data["history"] = history[:keep_count]
                # Clear draft meeting so old ambiguous state doesn't pollute the edited query
                session_data.pop("draft_meeting", None)
                self.session_mgr.set_session(session_id, session_data)

        if not self.use_ai:
            return {
                "response": (
                    "I can help you schedule meetings.\n\n"
                    "How can I help you schedule today?"
                ),
                "intent": "help",
                "options": [],
                "option_type": "general",
                "is_interactive": False,
            }

        # ── 1.5 Fast-path for structured form submissions (Edit Meeting) ───
        if _looks_like_structured_payload(prompt):
            form_data = _parse_structured_form(prompt)
            if form_data and not form_data.get("missing_fields"):
                event_id = form_data.get("eventId") or form_data.get("event_id")
                if event_id and event_id != "N/A":
                    # This is an update
                    res = self.gemini.tools["update_meeting"](
                        event_id=event_id,
                        subject=form_data["topic"],
                        start=form_data["start"],
                        end=form_data["end"],
                        location=form_data["location"],
                        attendees=form_data["attendees"],
                        recurrence=form_data["recurrence"],
                        presenter=form_data["presenter"]
                    )
                    resp_lines = [
                        "I've updated the meeting details as requested.",
                        f"✅ **{form_data['topic']}**",
                        f"📅 {_format_dt_for_ui(_safe_iso_utc(form_data['start']))}",
                        f"🚪 {form_data.get('location', 'Virtual')}",
                    ]
                    if form_data.get('presenter'):
                        resp_lines.append(f"🎤 Presenter: {form_data['presenter']}")
                    if form_data.get('recurrence') and form_data['recurrence'] != 'none':
                        resp_lines.append(f"🔁 Recurrence: {form_data['recurrence']}")

                    return {
                        "response": "\n".join(resp_lines),
                        "intent": "meeting_updated",
                        "options": [],
                        "option_type": "general",
                        "meeting_data": res
                    }

        # ── 2. Load full history from Redis ────────────────────────────────
        session_data = self.session_mgr.get_session(session_id)
        full_history: list = session_data.get("history", [])

        # ── 3. Inject saved preferences into prompt ────────────────────────
        enriched_prompt = self._inject_preferences(prompt)

        # ── 4. Trim history to last MAX_CONTEXT_TURNS pairs ────────────────
        context_history = (
            full_history[-(MAX_CONTEXT_TURNS * 2):]
            if len(full_history) > MAX_CONTEXT_TURNS * 2
            else full_history
        )

        # ── 5. Call Gemini ─────────────────────────────────────────────────
        try:
            response_text, updated_context = await self.gemini.process_message_async(
                enriched_prompt,
                session_id=session_id,
                truncate_history=truncate_history,
            )
        except Exception as e:
            return {"response": f"Gemini Error: {e}", "intent": "error",
                    "options": [], "option_type": "none"}

        response_text = _friendly_time_text(response_text)

        # ── 6. Merge updated context back into full history and save ────────
        # Append only the new turns (last 2: user + model)
        new_turns = updated_context[-2:] if len(updated_context) >= 2 else updated_context
        full_history.extend(new_turns)
        self.session_mgr.set_session(session_id, {"history": full_history})

        # ── 7. Try parsing the AI response as structured JSON ──────────────
        # The new system prompt mandates raw JSON output. If we get it, map it
        # directly to our card format — no regex required.
        import json as _json
        try:
            # Strip potential markdown code fences just in case
            clean_json = response_text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            ai_json = _json.loads(clean_json)
            response_type = ai_json.get("type", "")

            if response_type == "slot_selection":
                # 1:1 — LLM returned slot suggestions (fast-path missed this message)
                rooms = ai_json.get("rooms", [])
                title = ai_json.get("title", "")
                agenda = ai_json.get("agenda", "")
                msg_text = ai_json.get("message", "")

                # Load draft (may have attendees if fast-path partially ran)
                session_data = self.session_mgr.get_session(session_id) or {}
                draft = session_data.get("draft_meeting") or {}
                draft["ai_title"] = title
                draft["ai_agenda"] = agenda

                # ── Resolve attendee from the prompt if draft has none ──────────
                draft_attendees = draft.get("attendees", []) or []
                if not draft_attendees:
                    # Try name resolution from the current prompt
                    low = prompt.lower()
                    organiser = self.repo.get_organiser()
                    _org_id = organiser.id if organiser else ORGANISER_ID
                    candidates = self.repo.search_users(prompt)
                    candidates = [u for u in candidates if u.id != _org_id]
                    if candidates:
                        draft_attendees = [candidates[0].id]
                        draft["attendee_id"] = candidates[0].id
                        draft["attendees"] = draft_attendees
                        draft["topic"] = f"Catch-up with {candidates[0].displayName}"

                # ── Fetch real slots and build display labels ───────────────────
                # CRITICAL: use _format_dt_for_ui for BOTH the card display labels
                # AND the slot_label_map keys so CONFIRM_BOOKING can match them.
                tz_zone = ZoneInfo(DISPLAY_TIMEZONE)
                today_dt = datetime.now(tz_zone).date()
                all_repo_slots: list = []
                dur = draft.get("duration", 60)
                for i_day in range(0, 7):
                    day_str = (today_dt + timedelta(days=i_day)).isoformat()
                    all_repo_slots.extend(self.repo.get_free_slots(
                        draft_attendees + [ORGANISER_ID], day_str, dur
                    ))

                if all_repo_slots:
                    top_slots = all_repo_slots[:3]
                    display_slots = [_format_dt_for_ui(_safe_iso_utc(s["start"])) for s in top_slots]
                    slot_label_map = {
                        _format_dt_for_ui(_safe_iso_utc(s["start"])): {
                            "start": s["start"],
                            "end": s.get("end", (_safe_iso_utc(s["start"]) + timedelta(minutes=dur)).strftime("%Y-%m-%dT%H:%M:%SZ"))
                        }
                        for s in all_repo_slots
                    }
                    # Use the first free slot to filter AVAILABLE rooms only
                    first_slot = all_repo_slots[0]
                    first_start = first_slot["start"]
                    first_end = first_slot.get("end", (_safe_iso_utc(first_slot["start"]) + timedelta(minutes=dur)).strftime("%Y-%m-%dT%H:%M:%SZ"))
                    
                    from .mcp_server import get_room_suggestions as mcp_get_rooms
                    mcp_rooms = mcp_get_rooms(first_start, first_end, 2)
                    rooms = [r["name"] for r in mcp_rooms if r.get("fits_group")]
                    if not rooms:
                        rooms = [mcp_rooms[0]["name"]] if mcp_rooms else ["Virtual 🌐"]
                    if "Virtual 🌐" not in rooms:
                        rooms.append("Virtual 🌐")
                else:
                    # Fallback: use AI labels (won't resolve at confirm, but better than nothing)
                    display_slots = ai_json.get("timeSlots", [])
                    slot_label_map = {}
                    rooms = ai_json.get("rooms", [])
                    if not rooms:
                        rooms = ["Virtual 🌐"]

                # Automatically pick the first room if available
                first_room = available_rooms[0] if available_rooms else "Virtual 🌐"
                
                draft["initial_slots"] = display_slots
                draft["initial_rooms"] = [first_room]
                draft["room"] = first_room
                draft["slot_label_map"] = slot_label_map
                draft["duration"] = dur
                session_data["draft_meeting"] = draft
                self.session_mgr.set_session(session_id, session_data)

                return {
                    "response": msg_text,
                    "intent": "slot_selection",
                    "options": display_slots + ["✅ Confirm & Book"],
                    "option_type": "gathering_card",
                    "titled_sections": {
                        "🕐 Select Time (choose one)": display_slots
                    },
                    "is_interactive": True,
                }

            elif response_type == "group_selection":
                # Group meeting — render a single comprehensive gathering card
                missing = ai_json.get("missing", [])
                prefilled = ai_json.get("prefilled", {})
                ai_time_slots = ai_json.get("timeSlots", [])
                topics = ai_json.get("topics", [])
                if not topics and "topic" in missing:
                    topics = self.repo.get_subject_suggestions("")[:3]
                rooms = ai_json.get("rooms", [])
                recurrence_opts = ai_json.get("recurrenceOptions", [])
                participants = ai_json.get("participants", [])
                msg = ai_json.get("message", "Got it. Select the remaining details.")

                # Ensure "Everyone" is always the last presenter option
                if "Everyone" not in participants and "Anyone" not in participants:
                    participants.append("Everyone")
                else:
                    # Normalise "Anyone" → "Everyone"
                    participants = ["Everyone" if p == "Anyone" else p for p in participants]
                    if "Everyone" not in participants:
                        participants.append("Everyone")

                session_data = self.session_mgr.get_session(session_id) or {}
                draft = session_data.get("draft_meeting") or {}
                draft.update({k: v for k, v in prefilled.items() if v})
                draft["missing_fields_card"] = missing
                draft["initial_rooms"] = rooms
                draft["participants"] = participants

                # ── Build slot_label_map so CONFIRM_BOOKING can resolve labels ──
                # Prefer real free-slot data; fall back to AI labels
                slot_label_map: dict = {}
                display_slots = ai_time_slots
                if "start" in missing:
                    organiser = self.repo.get_organiser()
                    _org_id = organiser.id if organiser else "103"
                    
                    attendee_ids = []
                    for p in participants:
                        if p in ["Everyone", "Anyone"] or "Organiser" in p:
                            continue
                        users = self.repo.search_users(p)
                        if users:
                            attendee_ids.append(users[0].id)
                    
                    draft["attendees"] = attendee_ids # Store resolved EIDs
                    
                    dur = draft.get("duration", 60)
                    tz = ZoneInfo(DISPLAY_TIMEZONE)
                    today = datetime.now(tz).date()
                    all_slots: list = []
                    for i in range(7):
                        d = (today + timedelta(days=i)).isoformat()
                        # Require organiser + matched attendees
                        all_slots.extend(self.repo.get_free_slots([_org_id] + attendee_ids, d, dur))
                    
                    if not all_slots:
                        # Fallback: just use organiser's free slots if no mutual slots
                        for i in range(7):
                            d = (today + timedelta(days=i)).isoformat()
                            all_slots.extend(self.repo.get_free_slots([_org_id], d, dur))

                    top_slots = all_slots[:3]
                    if top_slots:
                        display_slots = [_format_dt_for_ui(_safe_iso_utc(s["start"])) for s in top_slots]
                        slot_label_map = {
                            _format_dt_for_ui(_safe_iso_utc(s["start"])): {
                                "start": s["start"],
                                "end": s.get("end", (_safe_iso_utc(s["start"]) + timedelta(minutes=dur)).strftime("%Y-%m-%dT%H:%M:%SZ"))
                            }
                            for s in all_slots
                        }
                    else:
                        display_slots = ai_time_slots

                draft["initial_slots"] = display_slots
                draft["slot_label_map"] = slot_label_map
                
                # Fetch real available rooms for the first free slot using capacity logic
                from .mcp_server import get_room_suggestions as mcp_get_rooms
                p_count = len(participants) if participants else 2
                
                if slot_label_map:
                    first_key = next(iter(slot_label_map))
                    room_start = slot_label_map[first_key]["start"]
                    room_end = slot_label_map[first_key]["end"]
                    mcp_rooms = mcp_get_rooms(room_start, room_end, p_count)
                else:
                    room_start = datetime.utcnow().isoformat() + "Z"
                    room_end = (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z"
                    mcp_rooms = mcp_get_rooms(room_start, room_end, p_count)
                
                # Auto-assign the best room and remove room selection from the UI
                best_room = next((r["name"] for r in mcp_rooms if r.get("fits_group")), mcp_rooms[0]["name"] if mcp_rooms else "Virtual 🌐")
                prefilled["room"] = best_room
                draft["room"] = best_room
                if "room" in missing:
                    missing.remove("room")

                session_data["draft_meeting"] = draft
                self.session_mgr.set_session(session_id, session_data)

                titled = {}
                if "topic" in missing:
                    unique_topics = []
                    seen = set()
                    for t in topics:
                        if t not in seen:
                            unique_topics.append(t)
                            seen.add(t)
                    if "Other" not in seen:
                        unique_topics.append("Other")
                    titled["📝 Topic (choose one)"] = unique_topics
                if "start" in missing and display_slots:
                    titled["🕐 Select Time (choose one)"] = display_slots
                if "presenter" in missing and participants:
                    titled["🎤 Select Presenter (multi)"] = participants
                if "recurrence" in missing and recurrence_opts:
                    titled["🔁 Select Recurrence (choose one)"] = recurrence_opts

                all_options = []
                for opts in titled.values():
                    all_options.extend(opts)
                all_options.append("✅ Confirm & Book")

                return {
                    "response": msg,
                    "intent": "group_selection",
                    "options": all_options,
                    "option_type": "gathering_card",
                    "titled_sections": titled,
                    "is_interactive": True,
                }

            elif response_type == "room_conflict":
                msg = ai_json.get("message", "The selected room is unavailable.")
                rooms = ai_json.get("rooms", [])
                return {
                    "response": msg,
                    "intent": "room_conflict",
                    "options": rooms,
                    "option_type": "general",
                    "titled_sections": {
                        "🚪 Available Rooms": rooms
                    },
                    "is_interactive": True,
                }

            elif response_type == "disambiguation":
                msg = ai_json.get("message", "I found multiple people with that name. Which one did you mean?")
                raw_opts = ai_json.get("options", [])
                opts = []
                for o in raw_opts:
                    if isinstance(o, dict):
                        # Construct strict string for the frontend parser
                        name = o.get('name', 'Unknown')
                        dept = o.get('department', '')
                        email = o.get('email', '')
                        eid = o.get('eid', o.get('id', ''))
                        opts.append(f"Select: {name} ({dept}) - Email: {email} - EID: {eid}")
                    else:
                        opts.append(str(o))
                
                return {
                    "response": msg,
                    "intent": "attendee_disambiguation",
                    "options": opts,
                    "option_type": "general",
                    "titled_sections": {
                        "👥 Matching Users": opts
                    },
                    "is_interactive": True,
                }

            elif response_type == "draft_review":
                # User wants a final check before booking
                participants_raw = ai_json.get("participants", [])
                participants = []
                attendee_ids = []
                for p in participants_raw:
                    if isinstance(p, dict):
                        participants.append(p.get("name", "Unknown"))
                        attendee_ids.append(str(p.get("id", "")))
                    else:
                        participants.append(str(p))
                
                title = ai_json.get("title", ai_json.get("subject", "Meeting"))
                agenda = ai_json.get("agenda", "")
                start_iso = ai_json.get("start", ai_json.get("time", ""))
                end_iso = ai_json.get("end", "")
                room = ai_json.get("room", ai_json.get("location", "Virtual"))
                presenter = ai_json.get("presenter", "")
                recurrence = ai_json.get("recurrence", "One-time")

                try:
                    start_dt = _safe_iso_utc(start_iso)
                    time_str = _format_dt_for_ui(start_dt)
                    if end_iso:
                        end_dt = _safe_iso_utc(end_iso)
                        time_str += f" → {_format_dt_for_ui(end_dt)}"
                except:
                    time_str = start_iso

                confirm_lines = [
                    f"📝 **Draft: {title}**",
                    f"📅 {time_str}",
                    f"🚪 {room}",
                ]
                if presenter:
                    confirm_lines.append(f"🎤 Presenter: {presenter}")
                if recurrence and recurrence.lower() != "one-time":
                    confirm_lines.append(f"🔁 Recurrence: {recurrence}")
                if agenda:
                    confirm_lines.append(f"📋 Agenda: {agenda}")

                session_data = self.session_mgr.get_session(session_id) or {}
                session_data["last_meeting"] = {
                    "subject": title, "agenda": agenda,
                    "start": start_iso, "end": end_iso, "location": room,
                    "attendees": participants,
                    "event_id": "",
                    "fingerprint": "",
                    "attendee_ids": attendee_ids,
                    "presenter": presenter,
                    "recurrence": recurrence
                }
                self.session_mgr.set_session(session_id, session_data)

                return {
                    "response": "\n".join(confirm_lines),
                    "intent": "draft_review",
                    "options": ["Edit Details", "Proceed with booking"],
                    "option_type": "edit_grid",
                    "titled_sections": {},
                    "meeting_data": session_data["last_meeting"],
                }

            elif response_type == "booked":
                # Direct booking confirmed
                participants_raw = ai_json.get("participants", [])
                participants = []
                attendee_ids = []
                for p in participants_raw:
                    if isinstance(p, dict):
                        participants.append(p.get("name", "Unknown"))
                        attendee_ids.append(str(p.get("id", "")))
                    else:
                        participants.append(str(p))
                
                if not attendee_ids:
                    attendee_ids = ai_json.get("attendee_ids", [])
                join_url = ai_json.get("joinLink", "") or ai_json.get("join_url", "")
                title = ai_json.get("title", ai_json.get("subject", "Meeting"))
                agenda = ai_json.get("agenda", "")
                
                # Use start/end if available, fallback to 'time'
                start_iso = ai_json.get("start", ai_json.get("time", ""))
                end_iso = ai_json.get("end", "")
                
                room = ai_json.get("room", ai_json.get("location", "Virtual"))
                presenter = ai_json.get("presenter", "")
                recurrence = ai_json.get("recurrence", "One-time")

                try:
                    start_dt = _safe_iso_utc(start_iso)
                    time_str = _format_dt_for_ui(start_dt)
                    if end_iso:
                        end_dt = _safe_iso_utc(end_iso)
                        time_str += f" → {_format_dt_for_ui(end_dt)}"
                except:
                    time_str = start_iso

                confirm_lines = [
                    f"✅ **{title}** has been booked!",
                    f"📅 {time_str}",
                    f"🚪 {room}",
                ]
                if presenter:
                    confirm_lines.append(f"🎤 Presenter: {presenter}")
                if recurrence and recurrence.lower() != "one-time":
                    confirm_lines.append(f"🔁 Recurrence: {recurrence}")
                if agenda:
                    confirm_lines.append(f"📋 Agenda: {agenda}")

                session_data = self.session_mgr.get_session(session_id) or {}
                session_data["last_meeting"] = {
                    "subject": title, "agenda": agenda,
                    "start": start_iso, "end": end_iso, "location": room,
                    "attendees": participants, "join_url": join_url,
                    "event_id": ai_json.get("event_id", ""),
                    "fingerprint": ai_json.get("fingerprint", ""),
                    "attendee_ids": attendee_ids,
                    "presenter": presenter,
                    "recurrence": recurrence
                }
                session_data.pop("draft_meeting", None)
                self.session_mgr.set_session(session_id, session_data)

                return {
                    "response": "\n".join(confirm_lines),
                    "intent": "meeting_booked",
                    "options": ["✏️ Edit Details"],
                    "option_type": "general",
                    "titled_sections": {},
                    "links": [join_url] if join_url else [],
                    "meeting_data": session_data["last_meeting"],
                }

            elif response_type == "conflict":
                # Conflict detected — show alternates + keep-original option
                msg = ai_json.get("message", "There is a scheduling conflict.")
                alt_slots = ai_json.get("timeSlots", [])
                original_time = ai_json.get("originalTime", "")
                options = alt_slots[:]
                if ai_json.get("keepOriginal") and original_time:
                    options.append(f"Proceed with original: {original_time}")

                return {
                    "response": msg,
                    "intent": "conflict_detected",
                    "options": options,
                    "option_type": "conflict",
                    "titled_sections": {},
                    "is_interactive": True,
                }

        except (_json.JSONDecodeError, AttributeError, KeyError):
            # Not valid JSON — fall through to legacy regex extraction
            pass

        # ── 8. Legacy regex-based extraction (fallback) ─────────────────────
        options = extract_options(response_text)
        option_type = classify_option_type(options, response_text)
        titled_sections = extract_titled_sections(response_text)

        # Clear tap options if the meeting was successfully booked
        is_booked = bool(re.search(r'(Join Link|meeting booked|successfully booked|has been scheduled|has been booked|I\'ve booked)', response_text, re.IGNORECASE))
        if is_booked and option_type != "duplicate_action":
            options = []
            option_type = "general"
            titled_sections = {}

        candidate_options = []
        selection_map = {}

        # ── Automated Attendee Resolution for Dropdown ──────────────────
        if option_type == "attendee_confirm":
             # Extract the name from the response context (e.g. 'Multiple people found for "Anand"')
             name_match = re.search(r'Multiple people found for "([^"]+)"', response_text, re.IGNORECASE)
             if not name_match:
                 name_match = re.search(r'Which ([^ ]+) did you mean', response_text, re.IGNORECASE)
             if not name_match:
                 name_match = re.search(r'I see (?:two|multiple|several) ([^ ,?]+)', response_text, re.IGNORECASE)
             
             name = name_match.group(1).strip() if name_match else ""
             if name.lower().endswith("s") and len(name) > 3:
                 # Check if it's a pluralized name (e.g. "Rithwikas")
                 name = name[:-1]
             if not name:
                 # Try to extract the name from the options themselves if they look like "Name (Email)"
                 for o in options:
                     n = o.split("(")[0].strip()
                     if n:
                         name = n
                         break
             
             if name:
                 users = self.repo.search_users(name)
                 if users:
                     selection_map = { f"{u.displayName} ({u.mail})": u.id for u in users }
                     candidate_options = list(selection_map.keys())

        # ── FAST TRACK: Save resolved attendees to session ─────────────
        if "📅 Pick a time" in response_text:
            attendee_ids = []
            # For 1-on-1: "Pick a time to meet Anand Kumar"
            m_name = re.search(r"Pick a time to meet ([^ \n\r]+ [^ \n\r]+)", response_text)
            if m_name:
                name = m_name.group(1).strip()
                found = self.repo.search_users(name)
                if found:
                    attendee_ids = [found[0].id]
                    session_data["fast_track_attendee_names"] = found[0].displayName
            else:
                # For Group: "Attendees: Anand Kumar, Kiran Mehta"
                m_group = re.search(r"Attendees:\s*([^\n\r]+)", response_text)
                if m_group:
                    names = [n.strip() for n in m_group.group(1).split(",")]
                    for n in names:
                        found = self.repo.search_users(n)
                        if found:
                            attendee_ids.append(found[0].id)
                    session_data["fast_track_attendee_names"] = ", ".join(names)

            if attendee_ids:
                session_data["fast_track_attendee_ids"] = attendee_ids
                self.session_mgr.set_session(session_id, session_data)

        is_conflict_flow = any(w in response_text.lower() for w in [
            "cannot book",
            "conflict",
            "conflicting meeting",
            "busy",
            "overlap",
        ])
        if is_conflict_flow:
            response_text = _remove_title_agenda_blocks(response_text)
            titled_sections = {}
            option_type = "conflict"

            # Provide quick-action buttons for conflict recovery.
            conflict_ctx = self._extract_conflict_context(prompt)
            attendee_ids = [i for i in conflict_ctx.get("ids", []) if self.repo.get_user_by_id(i)]
            if attendee_ids and conflict_ctx.get("date"):
                organiser = self.repo.get_organiser()
                organiser_id = organiser.id if organiser else "103"
                check_ids = list(dict.fromkeys(attendee_ids + [organiser_id]))
                slots = self.repo.get_free_slots(
                    check_ids,
                    conflict_ctx["date"],
                    conflict_ctx.get("duration_mins", 60)
                )
                slot_options = [_format_dt_for_ui(_safe_iso_utc(s["start"])) for s in slots]
                options = slot_options + ["Continue with given time anyway"]

            else:
                options = ["Proceed with given time"]

        has_titles = bool(titled_sections.get("titles"))
        has_agendas = bool(titled_sections.get("agendas"))
        if has_titles and has_agendas and not is_conflict_flow:
            if prompt.lower().startswith("use title:"):
                options = titled_sections["agendas"]
                option_type = "agenda"
            else:
                options = titled_sections["titles"]
                option_type = "title"

        return {
            "response": response_text,
            "intent": "ai_generated",
            "options": options,
            "option_type": option_type,
            "candidate_options": candidate_options,
            "selection_map": selection_map,
            "titled_sections": titled_sections,  # {"titles": [...], "agendas": [...]}
        }

    def _process_structured_workflow(self, prompt: str, session_id: str) -> dict | None:
        session_data = self.session_mgr.get_session(session_id) or {}
        draft = session_data.get("draft_meeting")
        p = prompt.strip()
        lower = p.lower()
        organiser = self.repo.get_organiser()
        organiser_id = organiser.id if organiser else "103"

        # ── [CONFIRM_BOOKING] — fired by 'Confirm & Book' tap on gathering card ─
        # Format: "[CONFIRM_BOOKING] | 🕐 Select Time (choose one)=Mon 20 Apr ... | 🚪 Select Room (choose one)=Nilgiri..."
        if lower.startswith("[confirm_booking]"):
            self.session_mgr.set_status(session_id, "Booking your meeting...")
            # Parse selections from the payload
            selections: dict = {}
            for part in p.split("|"):
                part = part.strip()
                if "=" in part and not part.lower().startswith("[confirm"):
                    key, _, val = part.partition("=")
                    selections[key.strip()] = val.strip()

            # Resolve time label → ISO start/end
            chosen_time_label = next((v for k, v in selections.items() if "time" in k.lower()), "")
            chosen_topic = next((v for k, v in selections.items() if "topic" in k.lower() and v.strip() and v.strip() != "None"), draft.get("topic", ""))
            chosen_room = next((v for k, v in selections.items() if "room" in k.lower()), draft.get("room", "Virtual"))
            # Presenter may be multi-select (comma-separated names) or single
            chosen_presenter = next((v for k, v in selections.items() if "presenter" in k.lower()), draft.get("presenter", ""))
            chosen_recurrence = next((v for k, v in selections.items() if "recurrence" in k.lower()), draft.get("recurrence", "none"))

            # Match slot label back to ISO datetime — use stored map first
            start_iso, end_iso = "", ""
            if draft and chosen_time_label:
                slot_map = draft.get("slot_label_map", {})
                if chosen_time_label in slot_map:
                    start_iso = slot_map[chosen_time_label]["start"]
                    end_iso   = slot_map[chosen_time_label]["end"]
                else:
                    # Fallback: fuzzy rescan of the next 7 days
                    attendees_to_check = draft.get("attendees") or []
                    if not attendees_to_check and draft.get("attendee_id"):
                        attendees_to_check = [draft["attendee_id"]]
                    
                    if attendees_to_check:
                        tz = ZoneInfo(DISPLAY_TIMEZONE)
                        today = datetime.now(tz).date()
                        all_slots: list = []
                        duration = draft.get("duration", 60)
                        ids_to_check = list(set(attendees_to_check + [organiser_id]))
                        for i in range(0, 7):
                            d = (today + timedelta(days=i)).isoformat()
                            all_slots.extend(self.repo.get_free_slots(ids_to_check, d, duration))
                        for s in all_slots:
                            label = _format_dt_for_ui(_safe_iso_utc(s["start"]))
                            if label == chosen_time_label:
                                start_iso = s["start"]
                                end_iso = (_safe_iso_utc(s["start"]) + timedelta(minutes=duration)).strftime("%Y-%m-%dT%H:%M:%SZ")
                                break

            if not start_iso and draft and draft.get("start"):
                start_iso = draft["start"]
                # If draft missing end, assume 1 hour duration
                if draft.get("end"):
                    end_iso = draft["end"]
                else:
                    try:
                        s_dt = _safe_iso_utc(start_iso)
                        end_iso = (s_dt + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except:
                        end_iso = start_iso

            if not start_iso:
                return {
                    "response": "Could not resolve a time slot. Please try again.",
                    "intent": "error", "options": [], "option_type": "general", "titled_sections": {},
                }

            # Determine subject and agenda from draft or AI-generated values
            subject = chosen_topic or (draft or {}).get("ai_title") or (draft or {}).get("topic") or "Meeting"
            agenda = (draft or {}).get("ai_agenda") or self._build_default_agenda(subject, (draft or {}).get("duration", 60))
            attendees = (draft or {}).get("attendees", [])
            if not attendees and (draft or {}).get("attendee_id"):
                attendees = [(draft or {})["attendee_id"]]

            # Map room name to "Virtual" if needed
            location = chosen_room if chosen_room else "Virtual"
            if "virtual" in location.lower() or "🌐" in location:
                location = "Virtual"

            result = create_meeting(
                subject=subject,
                agenda=agenda,
                location=location,
                start=start_iso,
                end=end_iso,
                attendees=attendees,
                recurrence=chosen_recurrence.lower() if chosen_recurrence != "none" else "none",
                presenter=organiser.displayName if not chosen_presenter else chosen_presenter,
                recurrence_end_date=next((v for k, v in selections.items() if "recurrence_end_date" in k.lower()), ""),
            )

            session_data["last_meeting"] = {
                "event_id": result.get("event_id", ""),
                "subject": subject,
                "agenda": agenda,
                "start": result.get("start", start_iso),
                "end": result.get("end", end_iso),
                "location": result.get("location", location),
                "attendees": attendees,
                "join_url": result.get("join_url", ""),
                "presenter": chosen_presenter,
                "recurrence": chosen_recurrence
            }
            session_data.pop("draft_meeting", None)
            self.session_mgr.set_session(session_id, session_data)

            try:
                start_dt = _safe_iso_utc(result.get("start", start_iso))
                start_fmt = _format_dt_for_ui(start_dt)
            except:
                start_fmt = result.get("start", start_iso)

            try:
                end_dt = _safe_iso_utc(result.get("end", end_iso))
                end_fmt = _format_dt_for_ui(end_dt)
            except:
                end_fmt = result.get("end", end_iso)
            
            join_url  = result.get("join_url", "")
            
            resp_text = (
                f'✅ **{subject}** has been booked!\n'
                f'📅 {start_fmt} → {end_fmt}\n'
                f'🚪 {location}'
            )
            
            if chosen_presenter:
                resp_text += f'\n🎤 Presenter: {chosen_presenter}'
            
            if agenda:
                resp_text += f'\n\n📝 **Agenda:**\n{agenda}'

            return {
                "response": resp_text.strip(),
                "intent": "meeting_booked",
                "options": ["✏️ Edit Details"],
                "option_type": "general",
                "titled_sections": {},
                "links": [join_url] if join_url else [],
                "meeting_data": session_data["last_meeting"],
            }

        # ── [UPDATE_MEETING] — fired by Meeting Editor 'Save' ─
        if lower.startswith("[update_meeting]"):
            self.session_mgr.set_status(session_id, "Updating meeting details...")
            updates: dict = {}
            for part in p.split("|"):
                part = part.strip()
                if "=" in part and not part.lower().startswith("[update"):
                    key, _, val = part.partition("=")
                    updates[key.strip()] = val.strip()

            event_id = updates.get("event_id")
            fingerprint = updates.get("fingerprint")
            
            from .mcp_server import update_meeting
            
            # Prepare update parameters
            update_params = {
                "event_id": event_id,
                "fingerprint": fingerprint or "",
                "new_subject": updates.get("subject"),
                "new_agenda": updates.get("agenda"),
                "new_location": updates.get("location"),
                "new_recurrence": updates.get("recurrence"),
                "new_presenter": updates.get("presenter"),
                "new_recurrence_end_date": updates.get("recurrence_end_date")
            }
            
            # Handle date/time update
            if updates.get("date") and updates.get("time"):
                try:
                    dt_str = f"{updates['date']}T{updates['time']}:00"
                    tz = ZoneInfo(DISPLAY_TIMEZONE)
                    dt = datetime.fromisoformat(dt_str).replace(tzinfo=tz)
                    start_iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    duration = int(updates.get("duration", 60))
                    end_iso = (dt + timedelta(minutes=duration)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    update_params["new_start"] = start_iso
                    update_params["new_end"] = end_iso
                except:
                    pass

            result = update_meeting(**update_params)
            
            if result.get("status") == "updated":
                # Update session memory
                if "last_meeting" in session_data and session_data["last_meeting"].get("event_id") == event_id:
                    session_data["last_meeting"].update({
                        "subject": updates.get("subject", session_data["last_meeting"].get("subject")),
                        "agenda": updates.get("agenda", session_data["last_meeting"].get("agenda")),
                        "location": updates.get("location", session_data["last_meeting"].get("location")),
                        "presenter": updates.get("presenter", session_data["last_meeting"].get("presenter")),
                        "recurrence": updates.get("recurrence", session_data["last_meeting"].get("recurrence")),
                    })
                    if "new_start" in update_params:
                        session_data["last_meeting"]["start"] = update_params["new_start"]
                        session_data["last_meeting"]["end"] = update_params["new_end"]
                    self.session_mgr.set_session(session_id, session_data)

                return {
                    "response": f"✅ The meeting **{updates.get('subject')}** has been updated successfully.",
                    "intent": "meeting_updated",
                    "options": ["✏️ Edit Details"],
                    "option_type": "general",
                    "titled_sections": {},
                    "meeting_data": session_data.get("last_meeting")
                }
            else:
                return {
                    "response": f"❌ Failed to update meeting: {result.get('message', 'Unknown error')}",
                    "intent": "update_failed",
                    "options": [],
                    "option_type": "general",
                    "titled_sections": {},
                }

        # Deterministic duplicate-update entry point from UI action button.
        if "please update existing meeting" in lower:
            ev = re.search(r"event_id\s*=\s*([a-zA-Z0-9_:-]+)", p, flags=re.IGNORECASE)
            fp = re.search(r"fingerprint\s*=\s*([a-zA-Z0-9_:-]+)", p, flags=re.IGNORECASE)
            session_data["pending_update"] = {
                "event_id": ev.group(1) if ev else "",
                "fingerprint": fp.group(1) if fp else "",
            }
            self.session_mgr.set_session(session_id, session_data)
            return {
                "response": (
                    "Sure — what would you like to update?\n"
                    "You can send: new date/time, title, agenda, attendees, location, recurrence, or presenter."
                ),
                "intent": "update_requested",
                "options": [],
                "option_type": "general",
                "titled_sections": {},
            }

        # Lightweight deterministic session memory for post-booking follow-ups.
        if not draft and session_data.get("last_meeting"):
            last = session_data.get("last_meeting") or {}
            is_direct_question = any(q in lower for q in ["what", "show", "tell", "which"])
            if (
                is_direct_question
                and not _looks_like_structured_payload(p)
                and "agenda" in lower
                and any(k in lower for k in ["this meeting", "that meeting", "meeting"])
            ):
                agenda = last.get("agenda", "").strip() or "No agenda was saved for the last booked meeting."
                return {
                    "response": f'The agenda for "{last.get("subject", "your last meeting")}" is:\n{agenda}',
                    "intent": "meeting_context_answered",
                    "options": [],
                    "option_type": "general",
                    "titled_sections": {},
                }
            if (
                is_direct_question
                and not _looks_like_structured_payload(p)
                and "title" in lower
                and any(k in lower for k in ["this meeting", "that meeting", "meeting"])
            ):
                return {
                    "response": f'The meeting title is "{last.get("subject", "Unknown")}".',
                    "intent": "meeting_context_answered",
                    "options": [],
                    "option_type": "general",
                    "titled_sections": {},
                }
            if (
                is_direct_question
                and not _looks_like_structured_payload(p)
                and any(k in lower for k in ["when", "time", "scheduled"])
                and any(k in lower for k in ["this meeting", "that meeting", "meeting"])
            ):
                start = last.get("start")
                end = last.get("end")
                if start and end:
                    when_text = f'{_format_dt_for_ui(_safe_iso_utc(start))} to {_format_dt_for_ui(_safe_iso_utc(end))}'
                else:
                    when_text = "time was not saved."
                return {
                    "response": f'The meeting "{last.get("subject", "your last meeting")}" is scheduled for {when_text}',
                    "intent": "meeting_context_answered",
                    "options": [],
                    "option_type": "general",
                    "titled_sections": {},
                }

        # If we are waiting for missing form fields, merge and continue.
        if draft and draft.get("missing_fields") and "[structured form submission]" not in lower:
            self.session_mgr.set_status(session_id, "Checking missing form details...")
            draft = _merge_missing_fields(draft, p)
            session_data["draft_meeting"] = draft
            self.session_mgr.set_session(session_id, session_data)
            if draft.get("missing_fields"):
                missing = ", ".join(draft["missing_fields"])
                return {
                    "response": f"I still need these details to continue: {missing}. Please provide them.",
                    "intent": "missing_form_fields",
                    "options": [],
                    "option_type": "general",
                    "titled_sections": {},
                }

        if "[structured form submission]" in lower:
            self.session_mgr.set_status(session_id, "Reading form details...")
            parsed = _parse_structured_form(prompt)
            if not parsed:
                return None

            if parsed.get("missing_fields"):
                session_data["draft_meeting"] = parsed
                self.session_mgr.set_session(session_id, session_data)
                missing = ", ".join(parsed["missing_fields"])
                return {
                    "response": (
                        f"I found missing form details: {missing}. "
                        "Please share the missing values and I will continue."
                    ),
                    "intent": "missing_form_fields",
                    "options": [],
                    "option_type": "general",
                    "titled_sections": {},
                }

            attendee_ids = [a["id"] for a in parsed["attendees"]]
            ids_to_check = list(dict.fromkeys(attendee_ids + [organiser_id]))
            if parsed.get("auto_pick_time"):
                slots = self.repo.get_free_slots(ids_to_check, parsed["date"], parsed["duration_mins"])
                if not slots:
                    return {
                        "response": "I could not find a mutual free slot on that date. Please share a different date.",
                        "intent": "no_mutual_slot",
                        "options": [],
                        "option_type": "general",
                        "titled_sections": {},
                    }
                chosen = slots[0]
                parsed["start"] = chosen["start"]
                parsed["end"] = chosen["end"]
                parsed["time"] = _format_dt_for_ui(_safe_iso_utc(chosen["start"]))

            self.session_mgr.set_status(session_id, "Checking conflicts...")
            conflicts = []
            for uid in ids_to_check:
                for c in self.scheduler.check_conflicts(uid, parsed["start"], parsed["end"]):
                    user = self.repo.get_user_by_id(uid)
                    conflicts.append({
                        "name": user.displayName if user else uid,
                        "subject": c["subject"],
                        "start": _format_dt_for_ui(_safe_iso_utc(c["start"])),
                        "end": _format_dt_for_ui(_safe_iso_utc(c["end"])),
                    })

            parsed["selected_title"] = ""
            parsed["selected_agenda"] = ""
            session_data["draft_meeting"] = parsed
            self.session_mgr.set_session(session_id, session_data)

            if conflicts:
                lines = ["I found conflicts for selected attendees:"]
                for i, c in enumerate(conflicts, 1):
                    lines.append(f"{i}. {c['name']} is busy with \"{c['subject']}\" from {c['start']} to {c['end']}.")
                lines.append("")
                lines.append("Please choose a different time to continue.")

                slots = self.repo.get_free_slots(ids_to_check, parsed["date"], parsed["duration_mins"])
                slot_options = [_format_dt_for_ui(_safe_iso_utc(s["start"])) for s in slots]
                parsed["slot_map"] = {
                    _format_dt_for_ui(_safe_iso_utc(s["start"])): s["start"]
                    for s in slots
                }
                session_data["draft_meeting"] = parsed
                self.session_mgr.set_session(session_id, session_data)

                return {
                    "response": "\n".join(lines),
                    "intent": "conflict_detected",
                    "options": slot_options,
                    "option_type": "timeslot",
                    "titled_sections": {},
                }

            self.session_mgr.set_status(session_id, "Creating meeting...")
            auto_subject = parsed.get("topic") or "Meeting"
            auto_agenda = self._build_default_agenda(auto_subject, parsed.get("duration_mins", 60))
            result = create_meeting(
                subject=auto_subject,
                agenda=auto_agenda,
                location=parsed.get("location") or parsed.get("room") or "Virtual",
                start=parsed["start"],
                end=parsed["end"],
                attendees=parsed.get("attendees", []),
                recurrence=parsed.get("recurrence", "none"),
                presenter=parsed.get("presenter", ""),
            )
            if result.get("status") == "conflict":
                return {
                    "response": "This time slot is conflicting. I found this during final validation. Please provide a different date.",
                    "intent": "conflict_detected",
                    "options": [],
                    "option_type": "conflict",
                    "titled_sections": {},
                }
            session_data["last_meeting"] = {
                "event_id": result.get("event_id", ""),
                "subject": auto_subject,
                "agenda": auto_agenda,
                "start": result.get("start", parsed.get("start", "")),
                "end": result.get("end", parsed.get("end", "")),
                "location": result.get("location", parsed.get("location", "")),
                "attendees": parsed.get("attendees", []),
            }
            session_data.pop("draft_meeting", None)
            self.session_mgr.set_session(session_id, session_data)
            return {
                "response": (
                    f'The meeting "{auto_subject}" has been scheduled for '
                    f'{_format_dt_for_ui(_safe_iso_utc(result["start"]))} to '
                    f'{_format_dt_for_ui(_safe_iso_utc(result["end"]))}. '
                    f'Agenda: {auto_agenda}\n{result.get("join_url","")}'
                ),
                "intent": "meeting_booked",
                "options": [],
                "option_type": "general",
                "titled_sections": {},
                "meeting_data": {
                    "event_id": result.get("event_id", ""),
                    "fingerprint": result.get("fingerprint", ""),
                    "subject": auto_subject,
                    "agenda": auto_agenda,
                    "start": result.get("start", parsed.get("start", "")),
                    "end": result.get("end", parsed.get("end", "")),
                    "location": result.get("location", parsed.get("location", "")),
                    "recurrence": parsed.get("recurrence", "none"),
                    "presenter": parsed.get("presenter", ""),
                },
            }

        if draft and lower.startswith("book slot:"):
            # Existing specific slot booking logic
            self.session_mgr.set_status(session_id, "Updating selected time...")
            raw_choice = p.split(":", 1)[1].strip() if ":" in p else p
            mapped = (draft.get("slot_map") or {}).get(raw_choice, "")
            if mapped:
                new_start = mapped
            else:
                m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", p)
                if not m:
                    return {
                        "response": "I could not read that slot. Please tap one of the suggested slots.",
                        "intent": "invalid_slot",
                        "options": [],
                        "option_type": "conflict",
                        "titled_sections": {},
                    }
                new_start = m.group(1)

            if not new_start:
                return {
                    "response": "I could not read that slot. Please tap one of the suggested slots.",
                    "intent": "invalid_slot",
                    "options": [],
                    "option_type": "conflict",
                    "titled_sections": {},
                }
            new_end = (_safe_iso_utc(new_start) + timedelta(minutes=draft.get("duration_mins", 60))).strftime("%Y-%m-%dT%H:%M:%SZ")
            draft["start"] = new_start
            draft["end"] = new_end
            session_data["draft_meeting"] = draft
            self.session_mgr.set_session(session_id, session_data)
            # Auto-finalize meeting after user picks a new slot.
            auto_subject = draft.get("topic") or "Meeting"
            auto_agenda = self._build_default_agenda(auto_subject, draft.get("duration_mins", 60))
            result = create_meeting(
                subject=auto_subject,
                agenda=auto_agenda,
                location=draft.get("location") or draft.get("room") or "Virtual",
                start=draft["start"],
                end=draft["end"],
                attendees=draft.get("attendees", []),
                recurrence=draft.get("recurrence", "none"),
                presenter=draft.get("presenter", ""),
            )
            if result.get("status") == "conflict":
                return {
                    "response": "This time slot is still conflicting. Please choose a different time.",
                    "intent": "conflict_detected",
                    "options": [],
                    "option_type": "conflict",
                    "titled_sections": {},
                }
            session_data["last_meeting"] = {
                "event_id": result.get("event_id", ""),
                "subject": auto_subject,
                "agenda": auto_agenda,
                "start": result.get("start", draft.get("start", "")),
                "end": result.get("end", draft.get("end", "")),
                "location": result.get("location", draft.get("location", "")),
                "attendees": draft.get("attendees", []),
            }
            session_data.pop("draft_meeting", None)
            self.session_mgr.set_session(session_id, session_data)
            return {
                "response": (
                    f'The meeting "{auto_subject}" has been scheduled for '
                    f'{_format_dt_for_ui(_safe_iso_utc(result["start"]))} to '
                    f'{_format_dt_for_ui(_safe_iso_utc(result["end"]))}. '
                    f'Agenda: {auto_agenda}\n{result.get("join_url","")}'
                ),
                "intent": "meeting_booked",
                "options": [],
                "option_type": "general",
                "titled_sections": {},
                "meeting_data": {
                    "event_id": result.get("event_id", ""),
                    "fingerprint": result.get("fingerprint", ""),
                    "subject": auto_subject,
                    "agenda": auto_agenda,
                    "start": result.get("start", draft.get("start", "")),
                    "end": result.get("end", draft.get("end", "")),
                    "location": result.get("location", draft.get("location", "")),
                    "recurrence": draft.get("recurrence", "none"),
                    "presenter": draft.get("presenter", ""),
                },
            }

        # 1:1 Rule - Handle Slot Selection from gathering card
        # Matches: "Mon 21 Apr 10:00 AM - 11:00 AM IST" or similar
        m_card_slot = re.search(r"(\w{3})\s+(\d{1,2})\s+(\w{3})\s+(\d{1,2}:\d{2})\s*(AM|PM)", p, re.IGNORECASE)
        if draft and m_card_slot and "attendee_id" in draft:
            # Silently update draft time
            self.session_mgr.set_status(session_id, "Updating selected time...")
            # We need to find the ISO time from the formatted string.
            # Easiest way in this mock: search all available slots for a match.
            all_slots = []
            organiser_id = "103"
            target_id = draft.get("attendee_id")
            tz = ZoneInfo(DISPLAY_TIMEZONE)
            today = datetime.now(tz).date()
            for i in range(0, 7):
                d = (today + timedelta(days=i)).isoformat()
                all_slots.extend(self.repo.get_free_slots([target_id, organiser_id], d, draft.get("duration", 60)))
            
            chosen_iso = None
            for s in all_slots:
                if _format_dt_for_ui(_safe_iso_utc(s["start"])) == p.strip():
                    chosen_iso = s["start"]
                    break
            
            if chosen_iso:
                draft["start"] = chosen_iso
                draft["end"] = (_safe_iso_utc(chosen_iso) + timedelta(minutes=draft.get("duration", 60))).strftime("%Y-%m-%dT%H:%M:%SZ")
                draft["selected_time_label"] = p.strip()
                session_data["draft_meeting"] = draft
                self.session_mgr.set_session(session_id, session_data)
                
                # RE-RENDER the card with selection marked
                slots_marked = [(f"✅ {s}" if s == p.strip() else s) for s in draft.get("initial_slots", [])]
                rooms_marked = [(f"✅ {r}" if r == draft.get("location") else r) for r in draft.get("initial_rooms", [])]
                
                return {
                    "response": "", # Silent update
                    "intent": "gathering_card",
                    "options": slots_marked + rooms_marked + ["✅ Confirm & Book"],
                    "option_type": "gathering_card",
                    "titled_sections": {
                        "🕐 Select Time (choose one)": slots_marked,
                        "🚪 Select Room (choose one)": rooms_marked
                    },
                }

        # 1:1 Rule - Handle Room Selection
        # Matches: "Nilgiri (4-seater)", "🌐 Virtual", etc.
        if draft and ("(" in p or "virtual" in lower) and "attendee_id" in draft:
            # Silently update room
            self.session_mgr.set_status(session_id, "Updating selected room...")
            room_name = p.split("(")[0].strip()
            draft["location"] = room_name
            session_data["draft_meeting"] = draft
            self.session_mgr.set_session(session_id, session_data)
            
            # RE-RENDER the card with selection marked
            slots_marked = [(f"✅ {s}" if s == draft.get("selected_time_label") else s) for s in draft.get("initial_slots", [])]
            rooms_marked = [(f"✅ {r}" if r == room_name else r) for r in draft.get("initial_rooms", [])]
            
            return {
                "response": "", # Silent update
                "intent": "gathering_card",
                "options": slots_marked + rooms_marked + ["✅ Confirm & Book"],
                "option_type": "gathering_card",
                "titled_sections": {
                    "🕐 Select Time (choose one)": slots_marked,
                    "🚪 Select Room (choose one)": rooms_marked
                },
            }

        # 1:1 Rule - Final Confirmation
        if draft and lower == "confirm & book" and "attendee_id" in draft:
            if not draft.get("start"):
                return {"response": "Please select a time slot first.", "intent": "missing_selection", "options": [], "option_type": "general", "titled_sections": {}}
            
            self.session_mgr.set_status(session_id, "Finalizing 1:1 booking...")
            subject = draft.get("topic") or "Meeting"
            agenda = self._build_one_on_one_agenda(draft.get("duration", 60))
            result = create_meeting(
                subject=subject,
                agenda=agenda,
                location=draft.get("location") or "Virtual",
                start=draft["start"],
                end=draft["end"],
                attendees=[draft["attendee_id"]],
                recurrence="none",
                presenter=organiser.displayName if organiser else "",
            )
            # Standard post-booking card return
            session_data["last_meeting"] = {
                "event_id": result.get("event_id", ""),
                "subject": subject,
                "agenda": agenda,
                "start": result.get("start", draft.get("start", "")),
                "end": result.get("end", draft.get("end", "")),
                "location": result.get("location", draft.get("location", "")),
                "attendees": [draft["attendee_id"]],
            }
            session_data.pop("draft_meeting", None)
            self.session_mgr.set_session(session_id, session_data)
            return {
                "response": "Meeting booked successfully.",
                "intent": "meeting_booked",
                "options": [],
                "option_type": "general",
                "titled_sections": {},
                "meeting_data": session_data["last_meeting"]
            }

        if lower.startswith("edit "):
            last_meeting = session_data.get("last_meeting")
            if last_meeting:
                return {
                    "response": f"Opening the editor for \"{last_meeting.get('subject')}\"...",
                    "intent": "edit_redirect",
                    "options": [],
                    "option_type": "edit_grid",
                    "meeting_data": last_meeting,
                    "titled_sections": {},
                }

        if draft and lower.startswith("use title:"):
            self.session_mgr.set_status(session_id, "Preparing agenda options...")
            chosen = p.split(":", 1)[1].strip()
            draft["selected_title"] = chosen
            session_data["draft_meeting"] = draft
            self.session_mgr.set_session(session_id, session_data)
            agendas = [
                "Introduction (5m); Current Status (15m); Discussion (25m); Next Steps (15m)",
                "Quick Context (10m); Deep Dive (30m); Risks and Decisions (20m)",
                "Highlights (10m); Open Items (20m); Action Plan (30m)",
            ]
            return {
                "response": f"Great choice: \"{chosen}\". Now choose an agenda.",
                "intent": "agenda_selection",
                "options": agendas,
                "option_type": "agenda",
                "titled_sections": {},
            }

        if draft and lower.startswith("use agenda:"):
            self.session_mgr.set_status(session_id, "Creating meeting...")
            chosen = p.split(":", 1)[1].strip()
            draft["selected_agenda"] = chosen
            subject = draft.get("selected_title") or draft.get("topic") or "Meeting"
            result = create_meeting(
                subject=subject,
                agenda=chosen,
                location=draft.get("location") or draft.get("room") or "Virtual",
                start=draft["start"],
                end=draft["end"],
                attendees=draft.get("attendees", []),
                recurrence=draft.get("recurrence", "none"),
                presenter=draft.get("presenter", ""),
            )
            if result.get("status") == "conflict":
                return {
                    "response": "This time slot is still conflicting. Please choose a different time.",
                    "intent": "conflict_detected",
                    "options": [],
                    "option_type": "conflict",
                    "titled_sections": {},
                }
            session_data["last_meeting"] = {
                "event_id": result.get("event_id", ""),
                "subject": subject,
                "agenda": chosen,
                "start": result.get("start", draft.get("start", "")),
                "end": result.get("end", draft.get("end", "")),
                "location": result.get("location", draft.get("location", "")),
                "attendees": draft.get("attendees", []),
            }
            session_data.pop("draft_meeting", None)
            self.session_mgr.set_session(session_id, session_data)
            return {
                "response": (
                    f'The meeting "{subject}" has been successfully booked for '
                    f'{_format_dt_for_ui(_safe_iso_utc(result["start"]))} to '
                    f'{_format_dt_for_ui(_safe_iso_utc(result["end"]))}. '
                    f'A Teams meeting link has been generated.\n{result.get("join_url","")}'
                ),
                "intent": "meeting_booked",
                "options": [],
                "option_type": "general",
                "titled_sections": {},
                "meeting_data": {
                    "event_id": result.get("event_id", ""),
                    "fingerprint": result.get("fingerprint", ""),
                    "subject": subject,
                    "agenda": chosen,
                    "start": result.get("start", draft.get("start", "")),
                    "end": result.get("end", draft.get("end", "")),
                    "location": result.get("location", draft.get("location", "")),
                    "recurrence": draft.get("recurrence", "none"),
                    "presenter": draft.get("presenter", ""),
                },
            }

        # Handle non-bracketed structured prompts too (e.g., "I have all details... Topic: ...")
        if _parse_structured_form(prompt):
            self.session_mgr.set_status(session_id, "Reading form details...")
            parsed = _parse_structured_form(prompt)
            if not parsed:
                return None

            if parsed.get("missing_fields"):
                session_data["draft_meeting"] = parsed
                self.session_mgr.set_session(session_id, session_data)
                missing = ", ".join(parsed["missing_fields"])
                return {
                    "response": (
                        f"I found missing form details: {missing}. "
                        "Please share the missing values and I will continue."
                    ),
                    "intent": "missing_form_fields",
                    "options": [],
                    "option_type": "general",
                    "titled_sections": {},
                }

            attendee_ids = [a["id"] for a in parsed["attendees"]]
            ids_to_check = list(dict.fromkeys(attendee_ids + [organiser_id]))
            if parsed.get("auto_pick_time"):
                slots = self.repo.get_free_slots(ids_to_check, parsed["date"], parsed["duration_mins"])
                if not slots:
                    return {
                        "response": "I could not find a mutual free slot on that date. Please share a different date.",
                        "intent": "no_mutual_slot",
                        "options": [],
                        "option_type": "general",
                        "titled_sections": {},
                    }
                chosen = slots[0]
                parsed["start"] = chosen["start"]
                parsed["end"] = chosen["end"]
                parsed["time"] = _format_dt_for_ui(_safe_iso_utc(chosen["start"]))
            self.session_mgr.set_status(session_id, "Checking conflicts...")
            conflicts = []
            for uid in ids_to_check:
                for c in self.scheduler.check_conflicts(uid, parsed["start"], parsed["end"]):
                    user = self.repo.get_user_by_id(uid)
                    conflicts.append({
                        "name": user.displayName if user else uid,
                        "subject": c["subject"],
                        "start": _format_dt_for_ui(_safe_iso_utc(c["start"])),
                        "end": _format_dt_for_ui(_safe_iso_utc(c["end"])),
                        "event_id": c["event_id"],
                        "uid": uid,
                    })

            parsed["selected_title"] = ""
            parsed["selected_agenda"] = ""
            session_data["draft_meeting"] = parsed
            self.session_mgr.set_session(session_id, session_data)

            if conflicts:
                organiser_conflict = next((c for c in conflicts if c["uid"] == organiser_id), None)
                if organiser_conflict:
                    cached = None
                    for m in self.session_mgr.list_meetings():
                        if m.get("event_id") == organiser_conflict.get("event_id"):
                            cached = m
                            break
                    if cached:
                        return {
                            "response": (
                                f'You already have "{cached.get("subject", "a meeting")}" scheduled at '
                                f'{_format_dt_for_ui(_safe_iso_utc(cached.get("start", parsed["start"])))}. '
                                "What would you like to do?"
                            ),
                            "intent": "duplicate_detected",
                            "options": [
                                "🔄 Update time / details",
                                "🗑️ Cancel & delete this meeting",
                                "➕ Book as a separate new meeting",
                            ],
                            "option_type": "duplicate_action",
                            "existing_meeting": cached,
                            "titled_sections": {},
                        }

                lines = ["I found conflicts for selected attendees:"]
                for i, c in enumerate(conflicts, 1):
                    lines.append(f"{i}. {c['name']} is busy with \"{c['subject']}\" from {c['start']} to {c['end']}.")
                lines.append("")
                lines.append("Please choose a different time to continue.")

                slots = self.repo.get_free_slots(ids_to_check, parsed["date"], parsed["duration_mins"])
                slot_options = [_format_dt_for_ui(_safe_iso_utc(s["start"])) for s in slots]
                parsed["slot_map"] = {
                    _format_dt_for_ui(_safe_iso_utc(s["start"])): s["start"]
                    for s in slots
                }
                session_data["draft_meeting"] = parsed
                self.session_mgr.set_session(session_id, session_data)

                return {
                    "response": "\n".join(lines),
                    "intent": "conflict_detected",
                    "options": slot_options,
                    "option_type": "timeslot",
                    "titled_sections": {},
                }

            self.session_mgr.set_status(session_id, "Creating meeting...")
            auto_subject = parsed.get("topic") or "Meeting"
            auto_agenda = self._build_default_agenda(auto_subject, parsed.get("duration_mins", 60))
            result = create_meeting(
                subject=auto_subject,
                agenda=auto_agenda,
                location=parsed.get("location") or parsed.get("room") or "Virtual",
                start=parsed["start"],
                end=parsed["end"],
                attendees=parsed.get("attendees", []),
                recurrence=parsed.get("recurrence", "none"),
                presenter=parsed.get("presenter", ""),
            )
            if result.get("status") == "conflict":
                return {
                    "response": "This time slot is conflicting. I found this during final validation. Please provide a different date.",
                    "intent": "conflict_detected",
                    "options": [],
                    "option_type": "conflict",
                    "titled_sections": {},
                }
            session_data["last_meeting"] = {
                "event_id": result.get("event_id", ""),
                "subject": auto_subject,
                "agenda": auto_agenda,
                "start": result.get("start", parsed.get("start", "")),
                "end": result.get("end", parsed.get("end", "")),
                "location": result.get("location", parsed.get("location", "")),
                "attendees": parsed.get("attendees", []),
            }
            session_data.pop("draft_meeting", None)
            self.session_mgr.set_session(session_id, session_data)
            return {
                "response": (
                    f'The meeting "{auto_subject}" has been scheduled for '
                    f'{_format_dt_for_ui(_safe_iso_utc(result["start"]))} to '
                    f'{_format_dt_for_ui(_safe_iso_utc(result["end"]))}. '
                    f'Agenda: {auto_agenda}\n{result.get("join_url","")}'
                ),
                "intent": "meeting_booked",
                "options": [],
                "option_type": "general",
                "titled_sections": {},
                "meeting_data": {
                    "event_id": result.get("event_id", ""),
                    "fingerprint": result.get("fingerprint", ""),
                    "subject": auto_subject,
                    "agenda": auto_agenda,
                    "start": result.get("start", parsed.get("start", "")),
                    "end": result.get("end", parsed.get("end", "")),
                    "location": result.get("location", parsed.get("location", "")),
                    "recurrence": parsed.get("recurrence", "none"),
                    "presenter": parsed.get("presenter", ""),
                },
            }

        return None

    # ──────────────────────────────────────────────────────────────────────
    # Redis duplicate check (pre-LLM gate)
    # ──────────────────────────────────────────────────────────────────────
    def _check_duplicate(self, prompt: str) -> dict | None:
        names = _extract_names_from_prompt(prompt)
        topic = _extract_topic_from_prompt(prompt)
        if not names:
            return None  # can't fingerprint — let LLM handle

        fingerprint = self.session_mgr.make_fingerprint(names, topic)
        existing = self.session_mgr.get_meeting(fingerprint)
        if not existing:
            return None  # no duplicate

        # Build human-friendly duplicate warning
        attendee_list = ", ".join(existing.get("attendees", [])) or "unknown attendees"
        start = existing.get("start", "unknown time")
        subject = existing.get("subject", "a meeting")
        join_url = existing.get("join_url", "")
        recurrence = existing.get("recurrence", "one-time")
        presenter = existing.get("presenter", "")

        msg_lines = [
            f"⚠️ **A meeting with these attendees already exists:**",
            f"",
            f"📌 **{subject}**",
            f"👥 Attendees: {attendee_list}",
            f"🕐 When: {start}",
            f"🔁 Recurrence: {recurrence}",
        ]
        if presenter:
            msg_lines.append(f"🎤 Presenter: {presenter}")
        if join_url:
            msg_lines.append(f"🔗 Join: {join_url}")
        msg_lines += ["", "What would you like to do?"]

        return {
            "response": "\n".join(msg_lines),
            "intent": "duplicate_detected",
            "options": [
                "🔄 Update time / details",
                "🗑️ Cancel & delete this meeting",
                "➕ Book as a separate new meeting",
            ],
            "option_type": "duplicate_action",
            "existing_meeting": existing,
            "fingerprint": fingerprint,
        }

    # ──────────────────────────────────────────────────────────────────────
    # Inject Redis preferences into prompt silently
    # ──────────────────────────────────────────────────────────────────────
    def _inject_preferences(self, prompt: str) -> str:
        prefs = self.session_mgr.get_preferences(ORGANISER_ID)
        if not prefs:
            return prompt

        pref_lines = []
        if prefs.get("recurrence"):
            pref_lines.append(f"Default recurrence: {prefs['recurrence']}")
        if prefs.get("presenter"):
            pref_lines.append(f"Default presenter: {prefs['presenter']}")
        if prefs.get("duration"):
            pref_lines.append(f"Default duration: {prefs['duration']}")

        if pref_lines:
            pref_block = "[PREFS] " + ". ".join(pref_lines) + "."
            return f"{pref_block}\n{prompt}"
        return prompt
