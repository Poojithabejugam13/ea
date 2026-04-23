"""
Run from scheduling_backend dir:
  python -m scratch.test_agent_direct
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import traceback
from dotenv import load_dotenv
load_dotenv()

try:
    from src.dependencies import get_repo, get_session_mgr, get_ai_agent
    agent = get_ai_agent()
    print("[OK] Agent initialised.")
    result = agent.process_prompt("hello", session_id="test-diag")
    print("[OK] Result:", result)
except Exception:
    traceback.print_exc()
