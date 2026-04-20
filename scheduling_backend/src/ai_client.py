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
    set_current_session_id, reset_current_session_id
)
from .dependencies import get_session_mgr

load_dotenv()

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION = os.getenv("GCP_LOCATION", "us-central1")
MODEL_NAME = os.getenv("VERTEX_MODEL", "gemini-2.5-pro")

def get_system_instruction():
    """Dynamic instruction to inject today's date and weekday."""
    now = datetime.now()
    weekday = now.strftime("%A")  # e.g. "Sunday", "Saturday" — always correct
    date_str = now.strftime("%Y-%m-%d")
    return f"""
You are an AI Executive Assistant for Poojitha Reddy (Engineering Manager).
Poojitha Reddy is ALWAYS the Organiser. NEVER ask who the organiser is.
Today is {weekday}, {date_str}.

╔══════════════════════════════════════════════════════════════╗
║               NON-NEGOTIABLE EXTRACTION RULES                ║
╚══════════════════════════════════════════════════════════════╝

1. EXTRACT EVERYTHING FROM EVERY MESSAGE — including if answers are comma-separated or combined in a single line.
2. EXAMPLES OF COMBINED INPUTS (extract all parts, proceed without asking):
   • "12th april study plan"        → date=12 April, title hint=Study Plan
   • "radhakrishna, sprint review, 2pm tomorrow" → attendee=Radhakrishna, topic=Sprint Review, time=2 PM tomorrow
3. IF A DATE IS GIVEN: use it immediately. NEVER ask for the date again.
4. IF A TITLE HINT / TOPIC IS GIVEN (e.g. "study plan", "sprint review"): suggest titles immediately. NEVER ask for the title.
5. ACCUMULATE context across ALL turns. Never forget previous messages.
6. NEVER re-ask for anything the user has mentioned — in any form, in any turn.
7. BEFORE calling create_meeting or reschedule_meeting, ALWAYS validate every attendee (and organiser) with check_conflict_detail for the exact start/end.
8. NEVER claim attendees are free unless conflict tools/schedules confirm it for that exact slot.

╔══════════════════════════════════════════════════════════════╗
║              SILENT DEFAULTS (never ask about these)         ║
╚══════════════════════════════════════════════════════════════╝
- Duration: 1 hour
- Recurrence: one-time
- Presenter: Poojitha Reddy
- Location: Virtual

╔══════════════════════════════════════════════════════════════╗
║   WORKFLOW A: CHAT INPUT (Negotiation Mode)                  ║
╚══════════════════════════════════════════════════════════════╝
1. RESOLVE ATTENDEES: If you get names in chat without EIDs, search_users and ask user to pick.
2. DISAMBIGUATION: If multiple matches exist, you MUST ask the user to choose.

╔══════════════════════════════════════════════════════════════╗
║   WORKFLOW B: FORM SUBMISSION (Autonomous Mode)              ║
╚══════════════════════════════════════════════════════════════╝
If you receive a message labeled "STRUCTURED FORM SUBMISSION":
1. USE PROVIDED IDs: Do NOT call search_users. The user has already picked from duplicates.
2. CHECK CONFLICTS: Call check_conflict_detail/get_user_schedule for the provided IDs.
3. IF CONFLICT EXISTS:
   - STOP booking flow immediately.
   - Ask only for a different time.
   - DO NOT suggest title or agenda yet.
4. IF NO CONFLICT:
   - Ask TITLE suggestions first.
   - Ask AGENDA suggestions only after user confirms a title.
5. TIME DISPLAY:
   - Never show raw UTC timestamps in user-facing text.
   - Always show easy local format (example: "18 Apr 2026, 2:00 PM Asia/Kolkata").
6. NO QUESTIONS: Do not ask for any details already present in the form.
7. End with: "Ready to book." only when title + agenda + valid time are confirmed.

╔══════════════════════════════════════════════════════════════╗
║             ARCHITECTURE & PERSISTENCE RULES                 ║
╚══════════════════════════════════════════════════════════════╝
1. You ARE integrated with a local Redis server for persistent session storage.
2. You have permanent memory of this conversation across server restarts.
3. If asked how you remember details, accurately mention your Redis-backed session service.

=== STYLE ===
Responses must be short and clean. Use numbered lists. Never repeat a question.
Never ask what was already given or verified in the form.
"""

class GeminiAgent:
    def __init__(self, repository, session_manager):
        self.repo = repository
        self.session_mgr = session_manager
        
        # Ensure ADK uses Vertex AI by removing the placeholder API key
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key == "your_gemini_api_key_here":
            os.environ.pop("GEMINI_API_KEY", None)

        if PROJECT_ID:
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
            os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
            os.environ["GOOGLE_CLOUD_LOCATION"] = LOCATION
            print(f"INFO: Vertex AI backend enabled (project={PROJECT_ID}, location={LOCATION})")

        self.agent = Agent(
            name="scheduling_assistant",
            description="AI assistant for booking and managing meetings on behalf of a manager.",
            model=MODEL_NAME,
            instruction=get_system_instruction(),
            tools=[
                search_users, 
                get_users_by_team, 
                get_user_schedule,
                get_mutual_free_slots, 
                check_conflict_detail,
                create_meeting, 
                update_meeting, 
                reschedule_meeting, 
                notify_user,
                delete_meeting
            ]
        )
        
        self.session_service = RedisADKSessionService(self.session_mgr)
        self.runner = Runner(
            app_name="scheduling_app",
            agent=self.agent,
            session_service=self.session_service
        )
        
        print("INFO: ADK AI Agent initialized (Vertex AI powered).")

    async def process_message_async(self, message: str, session_id: str = "default") -> tuple[str, list]:
        final_text = ""
        status_mgr = get_session_mgr()
        status_mgr.set_status(session_id, "AI is understanding your request...")
        adk_message = types.Content(
            role="user",
            parts=[types.Part(text=message)]
        )
        
        try:
            current_session = await self.session_service.get_session(
                app_name="scheduling_app",
                user_id="default_user",
                session_id=session_id
            )
            if not current_session:
                await self.session_service.create_session(
                    app_name="scheduling_app",
                    user_id="default_user",
                    session_id=session_id
                )
        except Exception as e:
            print(f"DEBUG: Session init error: {e}")

        token = set_current_session_id(session_id)
        try:
            events = self.runner.run_async(
                user_id="default_user",
                session_id=session_id,
                new_message=adk_message
            )
            
            async for event in events:
                if event.is_final_response():
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if hasattr(part, 'text') and part.text:
                                final_text += part.text
        finally:
            reset_current_session_id(token)

        if not final_text:
            final_text = "I have processed your request."

        status_mgr.set_status(session_id, "Preparing final response...")

        simulated_history = [
            {"role": "user", "parts": [{"text": message}]},
            {"role": "model", "parts": [{"text": final_text}]}
        ]
        
        return final_text, simulated_history

    def process_message(self, message: str, history: list = None, session_id: str = "default") -> tuple[str, list]:
        """Synchronous wrapper for existing services.py compatibility."""
        return asyncio.run(self.process_message_async(message, session_id))
