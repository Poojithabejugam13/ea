import os
import asyncio
from google.genai import types
from google.adk import Agent, Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from dotenv import load_dotenv

load_dotenv()

async def test():
    print("Initializing ADK Agent...")
    agent = Agent(
        name="test",
        instruction="say hello",
        model="gemini-1.5-flash",
        tools=[]
    )
    runner = Runner(
        app_name="test_app",
        agent=agent,
        session_service=InMemorySessionService()
    )
    
    print("Running Agent...")
    try:
        # Wrap the message correctly
        msg = types.Content(role="user", parts=[types.Part(text="hello")])
        events = runner.run_async(user_id="u1", session_id="s1", new_message=msg)
        async for event in events:
            print(f"Event: {event.type}")
            if event.type == "model_response":
                print(f"Response: {event.data}")
                if hasattr(event.data, 'text'):
                     print(f"Text Component: {event.data.text}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
