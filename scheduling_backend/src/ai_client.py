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
    get_mutual_free_slots, check_conflict_detail,
    create_meeting, update_meeting, reschedule_meeting, notify_user, delete_meeting,
    set_current_session_id, reset_current_session_id, find_available_room
)
from .dependencies import get_session_mgr

load_dotenv()

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION   = os.getenv("GCP_LOCATION", "us-central1")
MODEL_NAME = os.getenv("VERTEX_MODEL", "gemini-2.5-pro")

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
        adk_message = types.Content(
            role="user",
            parts=[types.Part(text=message)]
        )

        # ── ensure session exists ───────────────────────────────────────────
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

        # ── run agent ─────────────────────────────────────────────────────────
        token = set_current_session_id(session_id)
        try:
            events = self.runner.run_async(
                user_id="default_user",
                session_id=session_id,
                new_message=adk_message,
            )

            async for event in events:
                if event.is_final_response():
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                final_text += part.text
        finally:
            reset_current_session_id(token)

        if not final_text:
            final_text = "I have processed your request."

        status_mgr.set_status(session_id, "Preparing final response...")

        # Minimal history for downstream compatibility
        simulated_history = [
            {"role": "user",  "parts": [{"text": message}]},
            {"role": "model", "parts": [{"text": final_text}]},
        ]

        return final_text, simulated_history

    # ── sync wrapper ──────────────────────────────────────────────────────────

    def process_message(
        self,
        message: str,
        history: list = None,   # kept for backward-compat; unused internally
        session_id: str = "default",
    ) -> tuple[str, list]:
        """
        Synchronous wrapper around process_message_async.
        Maintains API compatibility with existing services.py callers.
        """
        return asyncio.run(self.process_message_async(message, session_id))