from .repository import UserRepository
from .session_manager import SessionManager

_repo = None
_session_mgr = None

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
