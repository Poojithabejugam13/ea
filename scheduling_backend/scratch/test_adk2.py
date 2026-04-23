import asyncio
from google.adk import Agent, Runner

agent = Agent(name="test", instruction="Say hello", model="gemini-2.0-flash-exp", tools=[])
runner = Runner(app_name="test", agent=agent)

async def main():
    events = runner.run_async(user_id="1", session_id="1", new_message="Say hello")
    async for e in events:
        is_final = getattr(e, "is_final_response", lambda: False)()
        t = getattr(e, "type", type(e).__name__)
        print(f"Event: {t}, is_final: {is_final}")
        if hasattr(e, "content") and e.content and hasattr(e.content, "parts"):
            for p in e.content.parts:
                print(f"  part.text: {getattr(p, 'text', None)}")

asyncio.run(main())
