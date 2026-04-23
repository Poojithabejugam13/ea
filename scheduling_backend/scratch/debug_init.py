import os
from dotenv import load_dotenv
load_dotenv()
from src.ai_client import GeminiAgent
from src.repository import UserRepository
from src.session_manager import SessionManager

def test_init():
    try:
        repo = UserRepository()
        mgr = SessionManager()
        agent = GeminiAgent(repo, mgr)
        print("INIT SUCCESS")
    except Exception as e:
        import traceback
        print(f"INIT FAILED: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    test_init()
