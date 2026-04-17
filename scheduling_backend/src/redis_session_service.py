from typing import Optional, Any
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session
from google.adk.events.event import Event
from .session_manager import SessionManager

class RedisADKSessionService(InMemorySessionService):
    """
    Extends ADK's InMemorySessionService to seamlessly back up 
    all sessions and events to Redis using the existing SessionManager.
    Adds a 24-hour TTL to prevent history from growing indefinitely.
    """
    def __init__(self, session_manager: SessionManager):
        super().__init__()
        self.sm = session_manager

    def _redis_key(self, app_name: str, user_id: str, session_id: str) -> str:
        return f"adk_session:{app_name}:{user_id}:{session_id}"

    def _create_session_impl(self, *, app_name: str, user_id: str, state: Optional[dict[str, Any]] = None, session_id: Optional[str] = None) -> Session:
        session = super()._create_session_impl(app_name=app_name, user_id=user_id, state=state, session_id=session_id)
        # Save to Redis with 24 hours TTL
        self.sm._r_set(self._redis_key(app_name, user_id, session.id), session.model_dump(mode="json"), ttl=86400)
        return session

    def _get_session_impl(self, *, app_name: str, user_id: str, session_id: str, config=None) -> Optional[Session]:
        # Try memory first
        session = super()._get_session_impl(app_name=app_name, user_id=user_id, session_id=session_id, config=config)
        if session is not None:
            return session
            
        # Try Redis fallback
        raw_data = self.sm._r_get(self._redis_key(app_name, user_id, session_id))
        if raw_data:
            session = Session.model_validate(raw_data)
            # Restore to in-memory store so ADK mechanisms can append seamlessly
            if app_name not in self.sessions: 
                self.sessions[app_name] = {}
            if user_id not in self.sessions[app_name]: 
                self.sessions[app_name][user_id] = {}
            self.sessions[app_name][user_id][session_id] = session
            return super()._get_session_impl(app_name=app_name, user_id=user_id, session_id=session_id, config=config)
            
        return None

    def _delete_session_impl(self, *, app_name: str, user_id: str, session_id: str) -> None:
        super()._delete_session_impl(app_name=app_name, user_id=user_id, session_id=session_id)
        self.sm._r_del(self._redis_key(app_name, user_id, session_id))

    async def append_event(self, session: Session, event: Event) -> Event:
        result = await super().append_event(session, event)
        # Update Redis synchronously after in-memory state is modified
        app_name = session.app_name
        user_id = session.user_id
        session_id = session.id
        
        latest_session = self.sessions.get(app_name, {}).get(user_id, {}).get(session_id)
        if latest_session:
            self.sm._r_set(self._redis_key(app_name, user_id, session_id), latest_session.model_dump(mode="json"), ttl=86400)
            
        return result
