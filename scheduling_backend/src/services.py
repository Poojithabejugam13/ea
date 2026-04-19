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
from .mcp_server import create_meeting


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
    """Extract numbered list items from AI response for UI tap buttons.
    Skips section header lines like 'Title suggestions:' and 'Agenda suggestions:'.
    """
    options = []
    for line in text.split("\n"):
        stripped = line.strip()
        # Skip pure section headers (no number prefix)
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
    If the AI response contains 'Title suggestions:' AND 'Agenda suggestions:'
    sections, return {"titles": [...], "agendas": [...]}.
    Otherwise returns empty dict.
    """
    result = {}
    title_match = re.search(
        r'Title suggestions?:?\s*\n((?:\s*\d+[.)\]].+\n?)+)',
        text, re.IGNORECASE
    )
    agenda_match = re.search(
        r'Agenda suggestions?:?\s*\n((?:\s*\d+[.)\]].+\n?)+)',
        text, re.IGNORECASE
    )
    if title_match:
        block = title_match.group(1)
        result["titles"] = [
            m.group(1).strip()
            for m in re.finditer(r'^\s*\d+[.)\]]\s+(.+)$', block, re.MULTILINE)
        ]
    if agenda_match:
        block = agenda_match.group(1)
        result["agendas"] = [
            m.group(1).strip()
            for m in re.finditer(r'^\s*\d+[.)\]]\s+(.+)$', block, re.MULTILINE)
        ]
    return result


def classify_option_type(options: list, text: str) -> str:
    """Classify option type from AI response labels."""
    ctx = text.lower()
    # Combined title + agenda block (STEP 2 new format)
    has_title_section = bool(re.search(r'title suggestions?', ctx))
    has_agenda_section = bool(re.search(r'agenda suggestions?', ctx))
    if has_title_section and has_agenda_section:
        return "title_and_agenda"
    if has_title_section or "title suggestion" in ctx or "choose a meeting title" in ctx:
        return "title"
    if has_agenda_section or "choose an agenda" in ctx or ("agenda" in ctx and "tap one" in ctx):
        return "agenda"
    if any(w in ctx for w in ["free slot", "here are free slots"]):
        return "timeslot"
    if any(w in ctx for w in ["select attendee", "please select attendees"]):
        return "attendee"
    if any(w in ctx for w in ["update time", "cancel", "book as separate", "already booked", "already exists"]):
        return "duplicate_action"
    if any(w in ctx for w in ["reschedule", "conflict", "busy", "overlap", "continue"]) and len(options) <= 3:
        return "conflict"
    if options and any("@" in o or "eid" in o.lower() for o in options):
        return "attendee"
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
        "Topic", "Team", "Attendees", "Date", "Time", "Timezone", "Duration",
        "Recurrence", "Room", "Location/Link", "Presenter", "EID Verification"
    ]

    def grab(label: str) -> str:
        # Works for both multiline forms and one-line "label: value label: value" payloads.
        other_labels = [l for l in labels if l != label]
        lookahead = "|".join(re.escape(l) for l in other_labels)
        pattern = rf"{re.escape(label)}:\s*(.*?)(?=\s+(?:{lookahead}):|$)"
        m = re.search(pattern, prompt, flags=re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else ""

    attendees_raw = grab("Attendees")
    attendees = []
    if attendees_raw:
        for part in attendees_raw.split(","):
            eid = re.search(r"EID:\s*(\d+)", part, flags=re.IGNORECASE)
            importance = re.search(r"\((required|optional)\)", part, flags=re.IGNORECASE)
            if eid:
                attendees.append({
                    "id": eid.group(1),
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

    if date_s and time_s:
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

    return data


def _merge_missing_fields(draft: dict, prompt: str) -> dict:
    """Best-effort merge for missing field follow-up answers."""
    text = prompt.strip()
    low = text.lower()

    if "attendees" in draft.get("missing_fields", []):
        ids = re.findall(r"\b\d{2,}\b", text)
        if ids:
            draft["attendees"] = [{"id": i, "type": "optional"} for i in ids]
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
        try:
            self.gemini = RealGeminiAgent(repository, session_manager)
            self.use_ai = True
        except Exception as e:
            print(f"Vertex AI init failed: {e}")
            self.use_ai = False

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

    def _build_default_title(self, topic: str, attendee_name: str) -> str:
        cleaned = (topic or "General discussion").strip()
        return f"{cleaned} with {attendee_name}"

    def _single_person_auto_book(self, prompt: str, session_data: dict, session_id: str) -> dict | None:
        low = prompt.lower()
        if _looks_like_structured_payload(prompt):
            return None
        if not any(k in low for k in ["schedule", "book", "set up", "arrange", "meeting", "meet"]):
            return None
        if "team" in low:
            return None

        organiser = self.repo.get_organiser()
        organiser_id = organiser.id if organiser else "103"

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
            if len(fuzzy) == 1:
                attendee = fuzzy[0]
            elif len(fuzzy) > 1:
                # If top result is clearly stronger, auto-pick it to avoid extra questions.
                top = fuzzy[0]
                second = fuzzy[1]
                top_name = top.displayName.lower()
                second_name = second.displayName.lower()
                if top_name in low and second_name not in low:
                    attendee = top
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

        # Optional time extraction. If not present (or conflicting), auto-pick earliest mutual slot.
        chosen_start = ""
        chosen_end = ""
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
            start_s = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_s = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            has_conflict = bool(self.scheduler.check_conflicts(attendee.id, start_s, end_s) or self.scheduler.check_conflicts(organiser_id, start_s, end_s))
            if not has_conflict:
                chosen_start, chosen_end = start_s, end_s

        if not chosen_start:
            # Try up to 7 days to guarantee immediate booking attempt without extra questions.
            picked = None
            for i in range(0, 7):
                d = (today_local + timedelta(days=i)).isoformat() if not date_match else date_str
                slots = self.repo.get_free_slots([attendee.id, organiser_id], d, duration_mins)
                if slots:
                    picked = slots[0]
                    break
                if date_match:
                    break
            if not picked:
                return {
                    "response": "I could not find a mutual free slot right now. Please share a preferred date.",
                    "intent": "no_mutual_slot",
                    "options": [],
                    "option_type": "general",
                    "titled_sections": {},
                }
            chosen_start, chosen_end = picked["start"], picked["end"]

        topic = _extract_topic_from_prompt(prompt) or "One-on-one meet"
        subject = f"One-on-one meet with {attendee.displayName}"
        agenda = self._build_default_agenda(topic, duration_mins)
        result = create_meeting(
            subject=subject,
            agenda=agenda,
            location="Virtual",
            start=chosen_start,
            end=chosen_end,
            attendees=[{"id": attendee.id, "type": "required"}],
            recurrence="none",
            presenter=organiser.displayName if organiser else "",
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
            "subject": subject,
            "agenda": agenda,
            "start": result.get("start", chosen_start),
            "end": result.get("end", chosen_end),
            "location": result.get("location", "Virtual"),
            "attendees": [{"id": attendee.id, "type": "required"}],
        }
        self.session_mgr.set_session(session_id, session_data)
        return {
            "response": (
                f'The meeting "{subject}" has been scheduled for '
                f'{_format_dt_for_ui(_safe_iso_utc(result["start"]))} to '
                f'{_format_dt_for_ui(_safe_iso_utc(result["end"]))}. '
                f'Agenda: {agenda}\n{result.get("join_url","")}'
            ),
            "intent": "meeting_booked",
            "options": [],
            "option_type": "general",
            "titled_sections": {},
        }

    def process_prompt(self, prompt: str, session_id: str = "default") -> dict:
        self.session_mgr.set_status(session_id, "Processing request...")
        session_data = self.session_mgr.get_session(session_id) or {}
        single_auto = self._single_person_auto_book(prompt, session_data, session_id)
        if single_auto is not None:
            session_data = self.session_mgr.get_session(session_id) or {}
            session_data = self._append_history(session_data, prompt, single_auto.get("response", ""))
            self.session_mgr.set_session(session_id, session_data)
            self.session_mgr.set_status(session_id, "Preparing final response...")
            return single_auto
        # Fast deterministic workflow for form-based scheduling to keep latency low.
        fast_result = self._process_structured_workflow(prompt, session_id)
        if fast_result is not None:
            session_data = self.session_mgr.get_session(session_id) or {}
            session_data = self._append_history(session_data, prompt, fast_result.get("response", ""))
            self.session_mgr.set_session(session_id, session_data)
            self.session_mgr.set_status(session_id, "Preparing final response...")
            return fast_result

        if not self.use_ai:
            return {"response": "Vertex AI not initialized.", "intent": "error",
                    "options": [], "option_type": "none"}

        # ── 1. Duplicate check — NO LLM call if match found ────────────────
        duplicate_response = self._check_duplicate(prompt)
        if duplicate_response:
            return duplicate_response

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
            response_text, updated_context = self.gemini.process_message(
                enriched_prompt,
                context_history,
                session_id=session_id,
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

        options = extract_options(response_text)
        option_type = classify_option_type(options, response_text)
        titled_sections = extract_titled_sections(response_text)

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
            options = []
            option_type = "conflict"

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
            "titled_sections": titled_sections,  # {"titles": [...], "agendas": [...]}
        }

    def _process_structured_workflow(self, prompt: str, session_id: str) -> dict | None:
        session_data = self.session_mgr.get_session(session_id) or {}
        draft = session_data.get("draft_meeting")
        p = prompt.strip()
        lower = p.lower()
        organiser = self.repo.get_organiser()
        organiser_id = organiser.id if organiser else "103"

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
            }

        if draft and lower.startswith("book slot:"):
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
