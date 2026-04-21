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
MODEL_NAME = os.getenv("VERTEX_MODEL", "gemini-1.5-pro")
CANDIDATE_MODELS = [
    MODEL_NAME,
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


# ──────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT
# ──────────────────────────────────────────────────────────────────────────────

def get_system_instruction():
    now = datetime.now()
    weekday = now.strftime("%A")
    date_str = now.strftime("%Y-%m-%d")

    return f"""
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
- Call get_mutual_free_slots to find real free slots.
- Call get_room_suggestions to suggest appropriate rooms.
- Auto-generate title (e.g., "Catch-up with Radha Krishna") and agenda silently.
- NEVER ask for title or agenda — generate them always.

{{
  "type": "slot_selection",
  "message": "One short line max e.g. Scheduling with Radha Krishna",
  "title": "auto-generated meeting title",
  "agenda": "auto-generated 2-3 line agenda",
  "timeSlots": [
    "Mon 21 Apr 10:00–11:00 AM IST",
    "Mon 21 Apr 2:00–3:00 PM IST",
    "Tue 22 Apr 11:00 AM–12:00 PM IST"
  ],
  "rooms": ["Nilgiri (4-seater)", "Himalaya (8-seater)", "Virtual 🌐"]
}}

Rules:
- NEVER ask title or agenda for 1:1 — generate them silently
- NEVER ask if user wants slots — just provide slot_selection immediately
- Always include exactly 3 time slots
- Always include room options

---

### TYPE 2 — group_selection
Use for: every group meeting, always as first response

{{
  "type": "group_selection",
  "message": "Got it. Select the remaining details.",
  "prefilled": {{
    "topic": "Meeting Topic",
    "start": "2026-04-21T15:00:00Z",
    "end": "2026-04-21T16:00:00Z",
    "presenter": "Name of presenter",
    "recurrence": "Weekly",
    "room": "Nilgiri"
  }},
  "missing": ["topic", "presenter", "start", "recurrence", "room"],
  "topics": ["Project Sync", "Strategy Review", "Team Catch-up"],
  "timeSlots": [
    "Mon 21 Apr 3:00–4:00 PM IST",
    "Mon 21 Apr 4:30–5:30 PM IST",
    "Tue 22 Apr 10:00–11:00 AM IST"
  ],
  "participants": [
    "Poojitha Reddy (Organiser)",
    "Rithwika Singh",
    "Anand Kumar",
    "Anyone"
  ],
  "rooms": ["Nilgiri (4-seater)", "Himalaya (8-seater)", "Virtual 🌐"],
  "recurrenceOptions": ["One-time", "Weekly", "Biweekly", "Monthly"]
}}

Rules:
- missing must ONLY list fields user did NOT provide
- prefilled must ONLY contain fields user DID provide
- topic is ALWAYS in missing unless user explicitly gave a meeting subject/topic
- presenter is ALWAYS in missing unless user explicitly named one
- If user gave time → remove start from missing, omit timeSlots entirely
- If user gave room → remove room from missing, omit rooms entirely
- If user gave topic → remove topic from missing, omit topics entirely
- If user gave recurrence → remove recurrence from missing, omit recurrenceOptions entirely
- Always list ALL participants including organiser in participants array, AND always include "Anyone" as an option at the end so all can present.
- Always include exactly 3 time slots when start is missing
- Always include exactly 3 relevant topic suggestions when topic is missing

---

### TYPE 2 — draft_review
Use when: user confirmed all selections from group_selection card AND room is available or user confirmed a room.
DO NOT call create_meeting yet. The user must review the draft first.

{{
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
}}

Rules:
- Always generate title and agenda from topic
- participants must be a list of objects with name, email, and id (EID)
- NEVER call create_meeting for a draft.

---

### TYPE 3 — booked
Use when: user explicitly says "Proceed with booking" or "Proceed".
You MUST call the create_meeting tool, then return this payload:

{{
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
}}

Rules:
- Always call create_meeting tool before outputting this.
- generate joinLink from tool output.
- recurrence — include only if not one-time

---

### TYPE 4 — conflict
Use when: user-given time has a calendar conflict for any participant

{{
  "type": "conflict",
  "message": "Brief one line naming which participants have a conflict",
  "timeSlots": [
    "Mon 21 Apr 4:00–5:00 PM IST",
    "Tue 22 Apr 10:00–11:00 AM IST",
    "Tue 22 Apr 3:00–4:00 PM IST"
  ],
  "keepOriginal": true,
  "originalTime": "Mon 21 Apr 3:00–4:00 PM IST"
}}

Rules:
- Always provide exactly 3 alternative mutual free slots
- Always include keepOriginal: true so organiser can proceed anyway
- Inform organiser only — do not block booking

---

### TYPE 5 — room_conflict
Use when: user-specified room is not available at the chosen time

{{
  "type": "room_conflict",
  "message": "[Room name] is unavailable at that time.",
  "rooms": [
    "Nilgiri (4-seater) — Available",
    "Himalaya (8-seater) — Available",
    "Virtual 🌐"
  ]
}}

Rules:
- Always suggest all currently available rooms as tap options
- Always include Virtual as last option
- User taps one → book immediately, no further questions
- Never ask anything in text — UI handles selection

---

### TYPE 6 — disambiguation
Use when: search_users or get_users_by_team returns multiple results for a name and you are unsure which one the user meant.

{{
  "type": "disambiguation",
  "message": "I found multiple people with that name. Which one did you mean?",
  "options": [
    {"name": "John Doe", "department": "Engineering", "email": "john@ex.com", "eid": "101"},
    {"name": "John Smith", "department": "HR", "email": "smith@ex.com", "eid": "102"}
  ]
}}

Rules:
- Options must be a list of objects, each containing name, department, email, and eid.

---

## ROOM ASSIGNMENT — when user gives no room
- 3–6 people → medium room
- 7+ people → large conference room
- If no room available → Virtual, generate join link

## ROOM ASSIGNMENT — when user gives a room
- Check availability at chosen time
- If available → book directly, no questions
- If NOT available → return type: room_conflict with available alternatives

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
"""


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

        # Direct initialization without probing to avoid hangs in the FastAPI loop
        chosen_model = "gemini-1.5-flash"
        system_instr = get_system_instruction()
        tools_list = [
            search_users, get_users_by_team, get_user_schedule,
            get_mutual_free_slots, get_room_suggestions, check_conflict_detail,
            create_meeting, update_meeting, reschedule_meeting,
            notify_user, delete_meeting,
        ]

        self.agent = Agent(
            name="scheduling_assistant",
            description=(
                "AI assistant for booking and managing meetings "
                "on behalf of Poojitha Reddy (Engineering Manager)."
            ),
            model=chosen_model,
            instruction=system_instr,
            tools=tools_list,
        )

        self.session_service = RedisADKSessionService(self.session_mgr)
        self.runner = Runner(
            app_name="scheduling_app",
            agent=self.agent,
            session_service=self.session_service,
        )
        print(f"INFO: ADK AI Agent (ARIA) ready — model={chosen_model}, Vertex AI powered.")
        print(f"INFO: ADK AI Agent (ARIA) ready — model={chosen_model}, Vertex AI powered.")

    # ── async core ──────────────────────────────────────────────────────────

    async def process_message_async(
        self, message: str, session_id: str = "default", truncate_history: int = None
    ) -> tuple[str, list]:
        """
        Main async entry-point.
        Runs the ADK runner, streams events, and returns (final_text, history).
        """
        final_text = ""
        status_mgr = get_session_mgr()
        status_mgr.set_status(session_id, "ARIA is understanding your request...")

        adk_message = types.Content(
            role="user",
            parts=[types.Part(text=message)],
        )

        # ── ensure session exists ──────────────────────────────────────────
        try:
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
            
            if truncate_history is not None and current_session and hasattr(current_session, 'events'):
                keep_pairs = max(0, truncate_history - 1)
                if keep_pairs == 0:
                    current_session.events = []
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
                
                # Force save to Redis
                self.session_service.sm._r_set(
                    self.session_service._redis_key("scheduling_app", "default_user", session_id), 
                    current_session.model_dump(mode="json"), ttl=86400
                )
        except Exception as exc:
            print(f"DEBUG: Session init error: {exc}")

        # ── run agent ─────────────────────────────────────────────────────
        token = set_current_session_id(session_id)
        try:
            events = self.runner.run_async(
                user_id="default_user",
                session_id=session_id,
                new_message=adk_message,
            )

            async for event in events:
                # Accumulate text from any event that carries content
                if hasattr(event, "content") and event.content and hasattr(event.content, "parts"):
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            if final_text.endswith(part.text) or (part.text in final_text):
                                continue # Prevent duplicating chunks if ADK replays them
                            final_text += part.text
                
                # ADK sometimes attaches text to model_response events natively
                if getattr(event, "type", "") == "model_response":
                    if hasattr(event, "data") and getattr(event.data, "text", ""):
                        if event.data.text not in final_text:
                            final_text += event.data.text
        finally:
            reset_current_session_id(token)

        if not final_text:
            final_text = "I have processed your request."

        status_mgr.set_status(session_id, "Preparing final response...")

        # Return minimal history representation for downstream compatibility
        simulated_history = [
            {"role": "user",  "parts": [{"text": message}]},
            {"role": "model", "parts": [{"text": final_text}]},
        ]

        return final_text, simulated_history

    # ── sync wrapper ─────────────────────────────────────────────────────────

    # Synchronous process_message removed to prevent asyncio.run() hangs in FastAPI
    # Callers must use await self.process_message_async(...) instead.