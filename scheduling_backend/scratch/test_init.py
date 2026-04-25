import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.dependencies import get_ai_agent

agent = get_ai_agent()
print("USE_AI:", agent.use_ai)
print("INIT_ERROR:", getattr(agent, "init_error", "None"))
