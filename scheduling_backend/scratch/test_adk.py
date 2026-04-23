import asyncio
from google.adk import Agent, Runner

def dummy_tool(query: str) -> str:
    """Mock search user"""
    print(f"Tool called with {query}")
    return f"Found 101 user for {query}"

agent = Agent(
    name="test_agent",
    instruction="Use tool to answer user question.",
    model="gemini-2.0-flash-exp",
    tools=[dummy_tool]
)
runner = Runner(agent)

async def main():
    events = runner.run_async(user_id="1", session_id="1", new_message="who is poojitha?")
    async for event in events:
        if event.type == "model_response":
            print(f"Model: {event.data.text}")
        elif event.type == "tool_call":
            print(f"Tool Call: {event.data}")
            
if __name__ == "__main__":
    asyncio.run(main())
