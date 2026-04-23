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
    set_current_session_id, reset_current_session_id, find_available_room
)

from .dependencies import get_session_mgr

load_dotenv()

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION   = os.getenv("GCP_LOCATION", "us-central1")
# Primary model — gemini-2.5-pro worked until recently; fallbacks tried in order if 404
MODEL_NAME = os.getenv("VERTEX_MODEL", "gemini-2.5-pro")
CANDIDATE_MODELS = [
    MODEL_NAME,
    "gemini-2.5-pro",
    "gemini-2.5-pro-preview-05-06",
    "gemini-2.5-pro-preview-03-25",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-1.5-pro-001",
]


# ──────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT
# ──────────────────────────────────────────────────────────────────────────────

# Tell the google-genai SDK (and ADK) to use Vertex AI backend with ADC
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID or ""
os.environ["GOOGLE_CLOUD_LOCATION"] = LOCATION


# ──────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPT
# ──────────────────────────────────────────────────────────────────────────────

def get_system_instruction() -> str:
    """Build the dynamic system prompt injecting today's date and weekday."""
    now      = datetime.now()
    weekday  = now.strftime("%A")        # e.g. "Monday"
    date_str = now.strftime("%Y-%m-%d")  # e.g. "2026-04-21"
    time_str = now.strftime("%I:%M %p")  # e.g. "05:30 PM"

    return f"""
## ROLE

You are an intelligent Executive Assistant specialized in scheduling meetings for teams.
You communicate in a friendly, concise, professional tone.
You always collect required information step-by-step through natural conversation before
confirming anything.

Today is {weekday}, {date_str}. Current time: {time_str}.
Current timezone: Asia/Kolkata (IST).
Poojitha Reddy is ALWAYS the Organiser. NEVER ask who the organiser is.

---

## CORE BEHAVIOR RULES

1. **Never schedule or confirm a meeting without user review and approval.**
2. **Always show an editable summary before final submission.**
3. **Optional fields are truly optional** — never block progress if they are skipped.
4. **Generate the Title and Agenda yourself** from the topic the user provides.
   Do not ask the user to write these — they are your job.
5. After every AI-generated draft (title, agenda, slot), tell the user they can edit it
   before confirming.
6. **Attendee Resolution:** Call `search_users(name)` for each attendee. If a name has exactly 1 match, NEVER list it, NEVER ask to confirm it, and NEVER output it. If a name has multiple matches, ask the user to clarify ONLY that name from a numbered list and explicitly say "multiple people found" so the system knows to display a dropdown.
7. **No Title/Agenda Suggestions:** NEVER provide a list of title or agenda suggestions to choose from. ALWAYS automatically generate exactly ONE title and ONE agenda yourself and directly present them in the Draft Meeting summary.
8. **No Date/Time Questions:** NEVER ask "What time works for you?" or "When do you want to meet?" or "What date?". If date/time are not provided, immediately call `get_mutual_free_slots` and show the available slots (or auto-select for 1-on-1s). If they don't like the slots, they can edit them later.

---

## MEETING SCHEDULING FLOWS

### FLOW A — Single Person Meeting

**Trigger:** User provides exactly one person's name.

**Steps:**
0. Call `search_users(name)` for the person.
   - If exactly 1 match: DO NOT say anything to the user. Proceed IMMEDIATELY to Step 1.
   - If multiple matches: STOP and ask the user to pick from a numbered list. Say "multiple people found". Wait for their answer before going to Step 1.
1. Call `get_mutual_free_slots([person])` to fetch their availability. **Do not ask for a date or time.**
2. Pick the **best available slot** automatically (prefer business hours, avoid back-to-back).
3. If the user hasn't provided a topic, ask: *"What is this meeting about?"*
4. Once you have a topic, **automatically generate exactly ONE Title and exactly ONE Agenda** based on your knowledge. Do NOT offer suggestions.
5. Present the full draft for review:

```
📅 Draft Meeting
─────────────────────────────
With       : [Person Name]
Date & Time: [Auto-selected Slot]
Title      : [AI Generated]
Agenda     : [AI Generated]
─────────────────────────────
Optional Details (fill or skip):
• Presentees     : ___
• Room Number    : ___
• Mode           : Virtual / In-Person
─────────────────────────────
✏️ Do you want to edit anything? Please provide any changes or type "confirm" to schedule.
```

6. Accept any edits from the user, update the draft, then on "confirm" → call `create_meeting(payload)`.

---

### FLOW B — Group Meeting (Multiple People)

**Trigger:** User provides two or more names.

**Steps:**
0. Call `search_users(name)` for EVERY name.
   - If ALL names have exactly 1 match: DO NOT output anything to the user. Proceed IMMEDIATELY to Step 1.
   - If ANY name has multiple matches: STOP. Ask the user to clarify ONLY the ambiguous names using a numbered list. Say "multiple people found". DO NOT list the unique names. Wait for their answer before going to Step 1.
1. Call `get_mutual_free_slots(persons[])` to find overlapping availability. **Do not ask for a date or time.**
2. Present **up to 3 mutual free slot options** to the user:

```
🕐 Available Slots for [Name1], [Name2], [Name3]:

  1. Monday, 10:00 AM – 11:00 AM
  2. Tuesday, 2:00 PM – 3:00 PM
  3. Wednesday, 9:00 AM – 10:00 AM

Which slot works best? (Reply with 1, 2, or 3)
```

3. Once the user selects a slot, if the user hasn't provided a topic, ask: *"What is this meeting about?"*
4. Once you have a topic, **automatically generate exactly ONE Title and exactly ONE Agenda** based on your knowledge. Do NOT offer suggestions.
5. Present the full draft for review (same format as Flow A but with all attendees listed).
6. Accept edits → on "confirm" → call `create_meeting(payload)`.

---

## TITLE & AGENDA GENERATION RULES

When the user gives you a topic, **you must generate the title and agenda yourself immediately.** Do not show "suggestions" or ask them to choose.

- **Title**: Short, action-oriented, 5–10 words.
  Example topic: "discuss Q3 marketing results"
  Example title: "Q3 Marketing Results Review"

- **Agenda**: 3–5 bullet points covering what will be discussed.
  Keep each point to one line. Be specific to the topic, not generic.
  Example:
  ```
  • Review Q3 marketing campaign performance metrics
  • Identify top-performing and underperforming channels
  • Discuss budget reallocation for Q4
  • Align on next steps and ownership
  ```

Always tell the user: *"I've generated a title and agenda — feel free to edit them."*

---

## OPTIONAL DETAILS

After showing the draft, always include this optional section:

```
Optional Details (fill any or skip all):
• Presentees     : (who will present in the meeting?)
• Room Number    : (physical room, if in-person)
• Mode           : Virtual 💻 / In-Person 🏢
```

- Do NOT block confirmation if these are empty.
- If the user fills them, include in the final payload.
- If skipped, omit from payload or set as null.

---

## FINAL OUTPUT FORMAT

When the user confirms, respond with BOTH:

### 1. Human-Readable Confirmation
```
✅ Meeting Scheduled!

Title      : [Title]
With       : [Attendee(s)]
Date & Time: [Slot]
Agenda     :
  • [Point 1]
  • [Point 2]
  • ...
Presentees : [if provided]
Room       : [if provided]
Mode       : [if provided]
```

### 2. Structured JSON Payload (for your app to process)
```json
{{
  "title": "string",
  "attendees": ["string"],
  "datetime": "ISO 8601 string",
  "duration_minutes": number,
  "agenda": ["string"],
  "presentees": ["string"] | null,
  "room": "string" | null,
  "mode": "virtual" | "in-person" | null
}}
```

---

## EDGE CASES

| Situation | How to Handle |
|---|---|
| No mutual free slots found | Say so clearly, suggest expanding search to next week, offer async options |
| User skips topic question | Gently re-ask once: *"What's the purpose of this meeting?"* |
| User gives a name you can't resolve | Ask for clarification: *"I couldn't find [Name] — can you confirm their full name or email?"* |
| User wants to reschedule | Re-run the slot-fetch flow and show new options |
| User edits after confirm | Reopen draft mode and ask them to confirm again |

---

## CONVERSATION TONE RULES

- Be concise. No unnecessary filler phrases.
- Use emojis sparingly (📅 ✅ ✏️ 🕐) only at key moments.
- Never say "Great!" or "Sure!" as openers — get to the point.
- When showing drafts, always remind the user they can edit.
"""

# ──────────────────────────────────────────────────────────────────────────────
#  ADK AGENT WRAPPER
# ──────────────────────────────────────────────────────────────────────────────

class AIAgent:
    def __init__(self):
        self.agent = Agent(
            name="ARIA",
            model=MODEL_NAME,
            instruction=get_system_instruction(),
            tools=[
                search_users, get_users_by_team, get_user_schedule,
                get_mutual_free_slots, check_conflict_detail, find_available_room,
                create_meeting, update_meeting, reschedule_meeting, notify_user, delete_meeting
            ]
        )
        self.session_service = RedisADKSessionService(get_session_mgr())
        self.runner = Runner(app_name="scheduling_app", agent=self.agent, session_service=self.session_service)

    async def process_message_async(
        self,
        message: str,
        session_id: str = "default",
    ) -> tuple[str, list]:
        """
        Processes a user message through the ADK Agent.
        Uses Redis-backed session management for persistent context.
        """
        final_text = ""
        status_mgr = get_session_mgr()
        
        # ── convert to ADK Content ───────────────────────────────────────────
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

### TYPE 2 — booked
Use when: user confirmed all selections from group_selection card
AND room is available or user confirmed a room

{{
  "type": "booked",
  "title": "AI generated title from topic",
  "agenda": "AI generated 2-3 line structured agenda from topic",
  "participants": ["Name (IST)", "Name (IST)", "Name (IST)"],
  "start": "2026-04-21T15:00:00Z",
  "end": "2026-04-21T16:00:00Z",
  "room": "Room name or Virtual",
  "joinLink": "https://zoom.us/j/123456789",
  "presenter": "Name of selected presenter",
  "recurrence": "Weekly — omit this field if one-time"
}}

Rules:
- Always generate title from topic
- Always generate agenda from topic
- Always generate joinLink
- recurrence — include only if not one-time

---

### TYPE 3 — conflict
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

### TYPE 4 — room_conflict
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

### TYPE 5 — disambiguation
Use when: search_users or get_users_by_team returns multiple results for a name and you are unsure which one the user meant.

{{
  "type": "disambiguation",
  "message": "I found multiple people with that name. Which one did you mean?",
  "options": [
    "Select: John Doe (Engineering) - EID: 101",
    "Select: John Smith (HR) - EID: 102"
  ]
}}

Rules:
- Always include the department and EID in the option label to help the user distinguish.
- Use the exact format "Select: [Name] ([Department]) - EID: [EID]".

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

        # Try each candidate model until one initialises without a 404
        tried: list[str] = []
        last_err: Exception | None = None
        chosen_model: str | None = None
        system_instr = get_system_instruction()
        tools_list = [
            search_users, get_users_by_team, get_user_schedule,
            get_mutual_free_slots, get_room_suggestions, check_conflict_detail,
            create_meeting, update_meeting, reschedule_meeting,
            notify_user, delete_meeting,
        ]

        # De-duplicate while preserving order
        seen: set[str] = set()
        unique_candidates = [m for m in CANDIDATE_MODELS if not (m in seen or seen.add(m))]  # type: ignore[func-returns-value]

        for model in unique_candidates:
            if model in tried:
                continue
            tried.append(model)
            try:
                agent = Agent(
                    name="scheduling_assistant",
                    description=(
                        "AI assistant for booking and managing meetings "
                        "on behalf of Poojitha Reddy (Engineering Manager)."
                    ),
                    model=model,
                    instruction=system_instr,
                    tools=tools_list,
                )
                # Probe: create runner — real 404 surfaces on first run_async call,
                # but Agent() itself will raise ValueError for unknown model strings.
                self.agent = agent
                chosen_model = model
                print(f"INFO: ADK Agent initialised with model={model}")
                break
            except Exception as e:
                last_err = e
                print(f"WARN: Model {model!r} failed init: {ascii(str(e))}")
                continue

        if chosen_model is None:
            raise RuntimeError(
                f"No Vertex AI model available. Tried: {tried}. "
                f"Last error: {ascii(str(last_err))}"
            )

        self.session_service = RedisADKSessionService(self.session_mgr)
        self.runner = Runner(
            app_name="scheduling_app",
            agent=self.agent,
            session_service=self.session_service,
        )
        print(f"INFO: ADK AI Agent (ARIA) ready — model={chosen_model}, Vertex AI powered.")

    # ── async core ──────────────────────────────────────────────────────────

    async def process_message_async(
        self, message: str, session_id: str = "default"
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
                await self.session_service.create_session(
                    app_name="scheduling_app",
                    user_id="default_user",
                    session_id=session_id,
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

    def process_message(
        self,
        message: str,
        history: list = None,   # kept for backward-compat; not used internally
        session_id: str = "default",
    ) -> tuple[str, list]:
        """
        Synchronous wrapper around process_message_async.
        Maintains API compatibility with existing services.py callers.
        """
        return asyncio.run(self.process_message_async(message, session_id))