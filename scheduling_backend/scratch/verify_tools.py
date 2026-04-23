import asyncio
import logging
import os
os.environ["ADK_LOG_LEVEL"] = "DEBUG"

logging.basicConfig(level=logging.DEBUG)

from src.ai_client import GeminiAgent
from src.repository import UserRepository
from src.redis_session_service import SessionManager

repo = UserRepository()
session_mgr = SessionManager()
agent = GeminiAgent(repo, session_mgr)

async def main():
    print("Sending message to agent...")
    try:
        res, hist = await agent.process_message_async("schedule meet with radhakrishna", session_id="test_1")
        print(f"Final Text length: {len(res)}, content: {repr(res)}")
    except Exception as e:
        print(f"Exception caught: {e}")

asyncio.run(main())
