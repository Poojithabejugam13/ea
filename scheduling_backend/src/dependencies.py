from .repository import UserRepository
from .session_manager import SessionManager
from contextvars import ContextVar

CALLER_USER_ID: ContextVar[str] = ContextVar("caller_user_id", default="")
_repo = None
_session_mgr = None
_ai_agent = None

def get_repo():
    global _repo
    if _repo is None:
        _repo = UserRepository()
    return _repo

def get_session_mgr():
    global _session_mgr
    if _session_mgr is None:
        _session_mgr = SessionManager()
    return _session_mgr


def get_ai_agent():
    global _ai_agent
    if _ai_agent is None:
        print("CREATING NEW AI AGENT IN DEPENDENCIES!")
        from .services import AIAgent
        _ai_agent = AIAgent(get_repo(), get_session_mgr())
    return _ai_agent
