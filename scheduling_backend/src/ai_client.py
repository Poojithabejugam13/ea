import os
import asyncio
import nest_asyncio
from google.genai import types
from dotenv import load_dotenv
from datetime import datetime
from google.adk import Agent, Runner
from .redis_session_service import RedisADKSessionService

# Apply nest_asyncio at the module level to allow sync-to-async bridging in FastAPI/Uvicorn
nest_asyncio.apply()

# Import tool functions directly from mcp_server
from .mcp_server import (
    search_users, get_users_by_team, get_user_schedule,
    get_mutual_free_slots, check_conflict_detail, get_room_suggestions,
    create_meeting, update_meeting, reschedule_meeting, notify_user, delete_meeting,
    set_current_session_id, reset_current_session_id
)

from .dependencies import get_session_mgr

load_dotenv()

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION   = os.getenv("GCP_LOCATION", "us-central1")
# Primary model — gemini-2.5-pro worked until recently; fallbacks tried in order if 404
MODEL_NAME = os.getenv("VERTEX_MODEL", "gemini-2.5-pro")



# ──────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT
# ──────────────────────────────────────────────────────────────────────────────

def get_system_instruction():
    now = datetime.now()
    weekday = now.strftime("%A")
    date_str = now.strftime("%Y-%m-%d")

    template = """
You are an intelligent meeting scheduling assistant with access to users' calendars.
Today is {weekday}, {date_str}. Current timezone: Asia/Kolkata (IST).

---

## CORE BEHAVIOR

### Input Parsing
-Please undertand conjunctions and acronyms and shorthand
ex:call with john tmrw 3pm" = 1:1, John, tomorrow 3 PM
- Parse the user's message fully before doing anything.
- Understand conjunctions, shorthand, implied meaning.
  e.g. "sprint planning with team weekly tmrw 3pm" = group, weekly, 
  tomorrow 3 PM, topic = sprint planning
- Only collect what is genuinely missing — nothing else.
- Normalize all times to each participant's local timezone.
- AUTO-CORRECTION: Aggressively correct user typos, misspellings of attendee names, locations, and topics. Map them to the real, corresponding entities before taking action.
- RELEVANCY: Maintain the full context of all participants mentioned in the conversation. When the user clarifies one participant (e.g., via disambiguation), merge that selection with the existing list of other participants. NEVER drop or forget a person just because another one was clarified.
- GROUP MEETINGS: If multiple people are mentioned, track ALL of them. If one is ambiguous, ask for clarification but explicitly state that you are still including the others.


### TRUST USER INPUT — NON-NEGOTIABLE
- User gives time → use it, never suggest slots, never pre-check
- User gives room → check availability only, never skip
- User gives presenter → treat as final, never re-ask
- User gives recurrence → treat as final, never re-ask
- User gives any detail → trust it, use it, move on
- Golden Rule: If the user said it → trust it → use it → move on

## MEETING TYPE DETECTION
- 1:1  = organizer + exactly one other person
- GROUP = organizer + two or more other people

---

## CRITICAL GROUP RULE — NO EXCEPTIONS

A group meeting MUST ALWAYS respond with group_selection first.
NEVER respond with type: booked directly for a group meeting.
Even if time and room are both known, group_selection is required
to collect presenter — unless user explicitly named a presenter.

Presenter is ALWAYS required for group meetings.
It can NEVER be auto-assigned or assumed.
It MUST always be selected by the user.

Flow:
1. First response → ALWAYS type: group_selection
2. After user confirms all selections → type: booked

---

## RESPONSE TYPES

### TYPE 1 — slot_selection
Use when: 1:1 meeting, no time given by user.
- CRITICAL SPEED RULE: DO NOT CALL ANY TOOLS (NO search_users, NO get_mutual_free_slots, NO get_room_suggestions).
- The backend will automatically fetch real slots, users, and rooms instantly based on your JSON response.
- Just return the JSON structure immediately to eliminate latency.
- Auto-generate title (e.g., "Catch-up with Radha Krishna") and agenda silently.
- NEVER ask for title, agenda, or room — generate/assign them always.

{
  "type": "slot_selection",
  "message": "Got it. I've found some available slots.",
  "title": "Meeting with Attendee Name",
  "agenda": "Sync to discuss ...",
  "room": "Auto-assigned room name here (from get_room_suggestions)",
  "timeSlots": []
}

Rules:
- NEVER ask title, agenda, or room — silently generate/assign them
- NEVER ask if user wants slots — just provide slot_selection immediately
- Always include exactly 3 mutually free time slots from get_mutual_free_slots
- Room is auto-assigned from get_room_suggestions — NEVER show it as a user option

---

### TYPE 2 — group_selection
Use for: every group meeting, always as first response

{
  "type": "group_selection",
  "message": "Got it. Select the remaining details.",
  "prefilled": {
    "topic": "Meeting Topic",
    "start": "2026-04-21T15:00:00Z",
    "end": "2026-04-21T16:00:00Z",
    "presenter": "Name of presenter",
    "recurrence": "Weekly",
    "room": "Auto-assigned room name"
  },
  "missing": ["topic", "presenter", "start", "recurrence"],
  "topics": ["Project Sync", "Strategy Review", "Team Catch-up"],
  "timeSlots": [],
  "participants": [
    "Poojitha Reddy (Organiser)",
    "Rithwika Singh",
    "Anand Kumar",
    "Anyone"
  ],
  "recurrenceOptions": ["One-time", "Weekly", "Biweekly", "Monthly"]
}

Rules:
- missing must ONLY list fields user did NOT provide
- prefilled must ONLY contain fields user DID provide
- ROOM IS NEVER in missing — ALWAYS auto-assign it silently using get_room_suggestions. NEVER ask the user about room.
- topic is ALWAYS in missing unless user explicitly gave a meeting subject/topic
- presenter is ALWAYS in missing unless user explicitly named one
- If user gave time → remove start from missing, omit timeSlots entirely
- If user gave topic → remove topic from missing, omit topics entirely
- If user gave recurrence → remove recurrence from missing, omit recurrenceOptions entirely
- Always list ALL participants including organiser in participants array, AND always include "Anyone" as an option at the end so all can present.
- CRITICAL SPEED RULE: DO NOT CALL ANY TOOLS (NO search_users, NO get_mutual_free_slots, NO get_room_suggestions).
- The backend will automatically fetch real slots, users, and rooms instantly based on your JSON response.
- Just return the JSON structure immediately to eliminate latency. Leave timeSlots as an empty array `[]`.
- Always include exactly 3 relevant topic suggestions when topic is missing
- NEVER include rooms or room fields in the response — room is handled internally


---

### TYPE 2 — draft_review
Use when: user confirmed all selections from group_selection card AND room is available or user confirmed a room.
DO NOT call create_meeting yet. The user must review the draft first.

{
  "type": "draft_review",
  "title": "AI generated title from topic",
  "agenda": "AI generated 2-3 line structured agenda from topic",
  "participants": [
    {"name": "Poojitha Reddy", "email": "poojitha@example.com", "id": "103"},
    {"name": "Rithwika Singh", "email": "rithwika@example.com", "id": "105"}
  ],
  "start": "2026-04-21T15:00:00Z",
  "end": "2026-04-21T16:00:00Z",
  "room": "Room name or Virtual",
  "presenter": "Name of selected presenter",
  "recurrence": "Weekly — omit this field if one-time"
}

Rules:
- Always generate title and agenda from topic
- participants must be a list of objects with name, email, and id (EID)
- NEVER call create_meeting for a draft.

---

### TYPE 3 — booked
Use when: user explicitly says "Proceed with booking" or "Proceed".
You MUST call the create_meeting tool, then return this payload:

{
  "type": "booked",
  "title": "AI generated title from topic",
  "agenda": "AI generated 2-3 line structured agenda from topic",
  "participants": [
    {"name": "Poojitha Reddy", "email": "poojitha@example.com", "id": "103"},
    {"name": "Rithwika Singh", "email": "rithwika@example.com", "id": "105"}
  ],
  "start": "2026-04-21T15:00:00Z",
  "end": "2026-04-21T16:00:00Z",
  "room": "Room name or Virtual",
  "joinLink": "https://zoom.us/j/123456789",
  "presenter": "Name of selected presenter",
  "recurrence": "Weekly — omit this field if one-time"
}

Rules:
- Always call create_meeting tool before outputting this.
- generate joinLink from tool output.
- recurrence — include only if not one-time

---

### TYPE 4 — conflict
Use when: user-given time has a calendar conflict for any participant

{
  "type": "conflict",
  "message": "Brief one line naming which participants have a conflict",
  "timeSlots": [
    "Mon 21 Apr 4:00–5:00 PM IST",
    "Tue 22 Apr 10:00–11:00 AM IST",
    "Tue 22 Apr 3:00–4:00 PM IST"
  ],
  "keepOriginal": true,
  "originalTime": "Mon 21 Apr 3:00–4:00 PM IST"
}

Rules:
- Always provide exactly 3 alternative mutual free slots
- Always include keepOriginal: true so organiser can proceed anyway
- Inform organiser only — do not block booking

---

### TYPE 5 — room_conflict
Use when: user-specified room is not available at the chosen time

{
  "type": "room_conflict",
  "message": "[Room name] is unavailable at that time.",
  "rooms": [
    "Nilgiri (4-seater) — Available",
    "Himalaya (8-seater) — Available",
    "Virtual 🌐"
  ]
}

Rules:
- Always suggest all currently available rooms as tap options
- Always include Virtual as last option
- User taps one → book immediately, no further questions
- Never ask anything in text — UI handles selection

---

### TYPE 6 — disambiguation
Use when: search_users or get_users_by_team returns multiple results for a name and you are unsure which one the user meant.

{
  "type": "disambiguation",
  "message": "I found multiple people with that name. Which one did you mean?",
  "options": [
    {"name": "John Doe", "department": "Engineering", "email": "john@ex.com", "eid": "101"},
    {"name": "John Smith", "department": "HR", "email": "smith@ex.com", "eid": "102"}
  ]
}

Rules:
- Options must be a list of objects, each containing name, department, email, and eid.

---

## ROOM ASSIGNMENT — ALWAYS AUTO-ASSIGNED, NEVER ASK USER
- ALWAYS call get_room_suggestions to auto-pick the best available room.
- NEVER ask the user to select a room. NEVER show rooms as options to the user.
- 1–2 people → small room (or Virtual)
- 3–6 people → medium room
- 7+ people → large conference room
- If no room available → use 'Virtual' silently
- If user explicitly specifies a room → check availability, book if free; if busy → silently pick next best available room instead (do NOT ask, do NOT show room_conflict to user)

---

## STRICT OUTPUT RULES
- Return ONLY raw JSON — no markdown, no backticks, no preamble
- message field must be one line only
- Never ask the user anything in text
- Never output two response types in one reply
- Never skip group_selection for group meetings
- Never auto-assign presenter for group meetings
- Always append "Everyone" to the participants list for presenter selection
- If search results are ambiguous, ALWAYS use type: disambiguation. NEVER guess.
- When multiple people are mentioned, perform all necessary searches (search_users) for ALL mentioned names before deciding to disambiguate. If one person is ambiguous but another is certain, maintain the certain person in your context and only disambiguate the ambiguous one.
- CRITICAL: Place any conversational clarifications (e.g. "I've already identified Rahul, but need to check which Rithwika you mean") INSIDE the "message" field of your JSON response. NEVER return plain text outside of the JSON structure.
- Always return a single, valid JSON object.

## SPEED — MINIMIZE ROUND-TRIPS (CRITICAL FOR LATENCY)
- For a 1:1 with no time given: DO NOT CALL ANY TOOLS. Just return the JSON response type: slot_selection immediately. The backend will handle the rest.
- For a group meeting: DO NOT CALL ANY TOOLS. Just return type: group_selection with the participants list. The backend will handle the rest.
- NEVER call search_users or get_mutual_free_slots or get_room_suggestions unless explicitly asked for information.
- Only call check_conflict_detail if user explicitly gave a specific time AND you need to verify it.
- Skip any tool whose result you don't need for the current response type.
- Do NOT call get_user_schedule unless the user asks about someone's calendar.
"""
    return template.replace("{weekday}", weekday).replace("{date_str}", date_str)



# ──────────────────────────────────────────────────────────────────────────────
#  AGENT CLASS
# ──────────────────────────────────────────────────────────────────────────────

class GeminiAgent:
    def __init__(self, repository, session_manager):
        self.repo        = repository
        self.session_mgr = session_manager

        # Ensure ADK uses Vertex AI by removing placeholder API key
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key == "your_gemini_api_key_here":
            os.environ.pop("GEMINI_API_KEY", None)

        if PROJECT_ID:
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
            os.environ["GOOGLE_CLOUD_PROJECT"]       = PROJECT_ID
            os.environ["GOOGLE_CLOUD_LOCATION"]      = LOCATION
            print(f"INFO: Vertex AI backend enabled (project={PROJECT_ID}, location={LOCATION})")

        chosen_model = MODEL_NAME
        system_instr = get_system_instruction()
        tools_list = [
            search_users, get_users_by_team, get_user_schedule,
            get_mutual_free_slots, get_room_suggestions, check_conflict_detail,
            create_meeting, update_meeting, reschedule_meeting,
            notify_user, delete_meeting,
        ]

        # Try models in priority order based on availability
        models_to_try = [MODEL_NAME, "gemini-2.5-pro", "gemini-1.5-pro", "gemini-2.0-flash", "gemini-1.5-flash"]
        self.runner = None
        current_model = ""

        for model_name in models_to_try:
            try:
                print(f"INFO: Attempting to initialize ADK AI Agent with model={model_name}...")
                self.agent = Agent(
                    name="scheduling_assistant",
                    description=(
                        "AI assistant for booking and managing meetings "
                        "on behalf of Poojitha Reddy (Engineering Manager)."
                    ),
                    model=model_name,
                    instruction=system_instr,
                    tools=tools_list,
                )
                self.session_service = RedisADKSessionService(self.session_mgr)
                self.runner = Runner(
                    app_name="scheduling_app",
                    agent=self.agent,
                    session_service=self.session_service,
                )
                current_model = model_name
                print(f"SUCCESS: ADK AI Agent (ARIA) ready — model={current_model}, Vertex AI powered.")
                break
            except Exception as e:
                print(f"WARNING: Model {model_name} failed to initialize: {e}")
                continue

        if not self.runner:
            raise RuntimeError("Failed to initialize ADK AI Agent with any of the candidate models.")

    # ── edit-meeting fast-path (structured form submission) ────────────────

    def handle_edit_form(self, prompt: str, session_id: str) -> dict | None:
        """
        Intercept a structured form submission (from the Edit Meeting popup) and
        call update_meeting directly, bypassing the LLM entirely.

        Returns a fully formed response dict if this was an edit-form payload,
        or None if the prompt should be forwarded to the LLM as normal.
        """
        from .mcp_server import update_meeting as _update_meeting
        import re as _re
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI

        low = prompt.lower()
        if "[structured form submission]" not in low and not all(
            k in low for k in ["topic:", "date:", "time:"]
        ):
            return None  # not a structured edit form — let LLM handle it

        # Reuse the shared parser already defined in services.py
        from .services import _parse_structured_form, _format_dt_for_ui, _safe_iso_utc

        form_data = _parse_structured_form(prompt)
        if not form_data or form_data.get("missing_fields"):
            return None  # incomplete form — let LLM handle it

        event_id = form_data.get("event_id") or form_data.get("eventId", "")
        is_draft = (not event_id or event_id == "N/A")

        # Pull fingerprint from session so Redis record merges correctly
        sd = self.session_mgr.get_session(session_id) or {}
        last = sd.get("last_meeting") or {}
        fingerprint = last.get("fingerprint", "")

        if is_draft:
            # Editing a meeting that hasn't been booked yet
            updated_meeting = dict(last)
            updated_meeting.update({
                "subject": form_data.get("topic") or updated_meeting.get("subject", ""),
                "start": form_data.get("start") or updated_meeting.get("start", ""),
                "end": form_data.get("end") or updated_meeting.get("end", ""),
                "location": form_data.get("location") or updated_meeting.get("location", "Virtual"),
                "presenter": form_data.get("presenter") or updated_meeting.get("presenter", ""),
                "recurrence": form_data.get("recurrence") or updated_meeting.get("recurrence", "none"),
            })
            if form_data.get("attendees"):
                updated_meeting["attendees"] = form_data.get("attendees")
                
            sd["last_meeting"] = updated_meeting
            self.session_mgr.set_session(session_id, sd)
            
            try:
                start_fmt = _format_dt_for_ui(_safe_iso_utc(updated_meeting.get("start", "")))
            except Exception:
                start_fmt = updated_meeting.get("start", "")

            lines = [
                f"📝 **Draft: {updated_meeting.get('subject', '')}**",
                f"📅 {start_fmt}",
                f"🚪 {updated_meeting.get('location', 'Virtual')}",
            ]
            if updated_meeting.get("presenter"):
                lines.append(f"🎤 Presenter: {updated_meeting['presenter']}")
            if updated_meeting.get("recurrence") and updated_meeting["recurrence"].lower() not in ("none", "once", "one-time", ""):
                lines.append(f"🔁 Recurrence: {updated_meeting['recurrence']}")
            if updated_meeting.get("agenda"):
                lines.append(f"📋 Agenda: {updated_meeting['agenda']}")

            return {
                "response": "\n".join(lines),
                "intent": "draft_review",
                "options": ["Edit Details", "Proceed with booking"],
                "option_type": "edit_grid",
                "meeting_data": updated_meeting,
            }

        # Otherwise, this is an edit of an existing booking
        res = _update_meeting(
            event_id=event_id,
            fingerprint=fingerprint,
            new_subject=form_data.get("topic", ""),
            new_start=form_data.get("start", ""),
            new_end=form_data.get("end", ""),
            new_location=form_data.get("location", ""),
            new_attendees=form_data.get("attendees", []),
            new_recurrence=form_data.get("recurrence", ""),
            new_presenter=form_data.get("presenter", ""),
        )

        # Persist updated last_meeting back to session
        updated_meeting = {
            "event_id": event_id,
            "subject": res.get("subject", form_data.get("topic", "")),
            "start": res.get("start", form_data.get("start", "")),
            "end": res.get("end", form_data.get("end", "")),
            "location": form_data.get("location", "Virtual"),
            "attendees": res.get("attendees", []),
            "join_url": res.get("join_url", ""),
            "fingerprint": res.get("new_fingerprint", fingerprint),
        }
        sd["last_meeting"] = updated_meeting
        self.session_mgr.set_session(session_id, sd)

        try:
            start_fmt = _format_dt_for_ui(_safe_iso_utc(res.get("start") or form_data.get("start", "")))
        except Exception:
            start_fmt = form_data.get("start", "")

        lines = [
            "I've updated the meeting details as requested.",
            f"✅ **{res.get('subject', form_data.get('topic', ''))}**",
            f"📅 {start_fmt}",
            f"🚪 {form_data.get('location', 'Virtual')}",
        ]
        if res.get("presenter"):
            lines.append(f"🎤 Presenter: {res['presenter']}")
        if res.get("recurrence") and res["recurrence"] not in ("none", "once", ""):
            lines.append(f"🔁 Recurrence: {res['recurrence']}")

        return {
            "response": "\n".join(lines),
            "intent": "meeting_updated",
            "options": [],
            "option_type": "general",
            "meeting_data": updated_meeting,
        }

    # ── async core ──────────────────────────────────────────────────────────

    async def process_message_async(
        self, message: str, session_id: str = "default", truncate_history: int = None
    ) -> tuple[str, list]:
        """
        Main async entry-point.
        Runs the ADK runner, streams events, and returns (final_text, history).
        """
        import time as _time

        _t0 = _time.perf_counter()
        def _elapsed() -> str:
            return f"{_time.perf_counter() - _t0:.2f}s"

        print(f"\n[TIME] [ARIA] START  msg={message[:60]!r}", flush=True)

        final_text = ""
        status_mgr = get_session_mgr()
        status_mgr.set_status(session_id, "ARIA is understanding your request...")

        adk_message = types.Content(
            role="user",
            parts=[types.Part(text=message)],
        )

        # ── ensure session exists ──────────────────────────────────────────
        try:
            _ts = _time.perf_counter()
            current_session = await self.session_service.get_session(
                app_name="scheduling_app",
                user_id="default_user",
                session_id=session_id,
            )
            if not current_session:
                current_session = await self.session_service.create_session(
                    app_name="scheduling_app",
                    user_id="default_user",
                    session_id=session_id,
                )
            print(f"[TIME] [ARIA] session ready        +{_time.perf_counter()-_ts:.2f}s  (total {_elapsed()})", flush=True)

            if truncate_history is not None and current_session and hasattr(current_session, 'events'):
                keep_pairs = max(0, (truncate_history - 1) // 2)
                if keep_pairs == 0:
                    self.session_service._delete_session_impl(app_name="scheduling_app", user_id="default_user", session_id=session_id)
                    current_session = await self.session_service.create_session(
                        app_name="scheduling_app",
                        user_id="default_user",
                        session_id=session_id,
                    )
                else:
                    new_events = []
                    model_resp_count = 0
                    for ev in current_session.events:
                        new_events.append(ev)
                        if getattr(ev, "type", "") == "model_response":
                            model_resp_count += 1
                            if model_resp_count >= keep_pairs:
                                break
                    current_session.events = new_events

                    self.session_service.sm._r_set(
                        self.session_service._redis_key("scheduling_app", "default_user", session_id),
                        current_session.model_dump(mode="json"), ttl=86400
                    )

        except Exception as exc:
            print(f"DEBUG: Session init error: {exc}")

        # ── run agent ─────────────────────────────────────────────────────
        token = set_current_session_id(session_id)
        _event_count = 0
        _last_event_t = _time.perf_counter()
        try:
            events = self.runner.run_async(
                user_id="default_user",
                session_id=session_id,
                new_message=adk_message,
            )
            print(f"[TIME] [ARIA] runner.run_async()   total {_elapsed()}", flush=True)

            async for event in events:
                _event_count += 1
                _now = _time.perf_counter()
                _gap = _now - _last_event_t
                _last_event_t = _now

                # Log event type + any tool name
                _etype = getattr(event, "type", type(event).__name__)
                _tool_name = ""
                if hasattr(event, "content") and event.content and hasattr(event.content, "parts"):
                    for _p in event.content.parts:
                        if hasattr(_p, "function_call") and _p.function_call:
                            _tool_name = f"  tool_call={_p.function_call.name}"
                        elif hasattr(_p, "function_response") and _p.function_response:
                            _tool_name = f"  tool_resp={_p.function_response.name}"
                is_final = hasattr(event, "is_final_response") and event.is_final_response()
                print(
                    f"[TIME] [ARIA] event #{_event_count:02d}  +{_gap:.2f}s  total {_elapsed()}"
                    f"  type={_etype!r}{_tool_name}"
                    f"{'  ← FINAL' if is_final else ''}",
                    flush=True,
                )

                # PRIMARY: use is_final_response()
                if is_final:
                    if hasattr(event, "content") and event.content and hasattr(event.content, "parts"):
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                final_text = part.text
                                break
                    if not final_text and hasattr(event, "data") and getattr(event.data, "text", ""):
                        final_text = event.data.text
                    continue

                # FALLBACK accumulator
                if hasattr(event, "content") and event.content and hasattr(event.content, "parts"):
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            if not final_text.endswith(part.text):
                                final_text += part.text
        finally:
            reset_current_session_id(token)

        print(
            f"[TIME] [ARIA] DONE  events={_event_count}  final_text_len={len(final_text)}"
            f"  total {_elapsed()}\n",
            flush=True,
        )

        if not final_text:
            final_text = "I have processed your request."

        status_mgr.set_status(session_id, "Preparing final response...")

        simulated_history = [
            {"role": "user",  "parts": [{"text": message}]},
            {"role": "model", "parts": [{"text": final_text}]},
        ]

        return final_text, simulated_history

    # ── sync wrapper ─────────────────────────────────────────────────────────

    # Synchronous process_message removed to prevent asyncio.run() hangs in FastAPI
    # Callers must use await self.process_message_async(...) instead.