"""
SessionManager — Redis-backed state with 4 namespaces:
  session:{id}          → LLM conversation history
  meeting:{fingerprint} → Booked meetings (duplicate detection, TTL=7d)
  prefs:{organiser_id}  → Recurrence + presenter defaults (no TTL)
  search:{query_hash}   → search_users result cache (TTL=1h)

Resilient: Falls back to in-memory if Redis is unavailable.
"""

import os
import json
import hashlib
import redis
import logging

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, redis_host="localhost", redis_port=6379, redis_password=None):
        self.use_redis = False
        self.redis_client = None
        self.in_memory_store: dict = {}

        try:
            self.redis_client = redis.Redis(
                host=os.getenv("REDIS_HOST", redis_host),
                port=int(os.getenv("REDIS_PORT", redis_port)),
                password=os.getenv("REDIS_PASSWORD", redis_password),
                socket_connect_timeout=5,
                socket_timeout=5,
                ssl=True,
                ssl_cert_reqs=None,
            )
            self.redis_client.ping()
            self.use_redis = True
            logger.info(f"✅ Redis connected to {os.getenv('REDIS_HOST', redis_host)}")
        except Exception as e:
            logger.warning(f"⚠️  Redis unavailable ({e}) — using in-memory fallback.")

    # ──────────────────────────────────────────────────────────────────────
    # Namespace helpers
    # ──────────────────────────────────────────────────────────────────────
    def _r_set(self, key: str, data: dict, ttl: int | None = None):
        if self.use_redis:
            raw = json.dumps(data)
            if ttl:
                self.redis_client.setex(key, ttl, raw)
            else:
                self.redis_client.set(key, raw)
        else:
            self.in_memory_store[key] = data

    def _r_get(self, key: str) -> dict | None:
        if self.use_redis:
            raw = self.redis_client.get(key)
            return json.loads(raw) if raw else None
        return self.in_memory_store.get(key)

    def _r_del(self, key: str):
        if self.use_redis:
            self.redis_client.delete(key)
        else:
            self.in_memory_store.pop(key, None)

    # ──────────────────────────────────────────────────────────────────────
    # 1. LLM session history
    # ──────────────────────────────────────────────────────────────────────
    def set_session(self, session_id: str, data: dict):
        self._r_set(f"session:{session_id}", data)

    def get_session(self, session_id: str) -> dict:
        return self._r_get(f"session:{session_id}") or {}

    def delete_session(self, session_id: str):
        self._r_del(f"session:{session_id}")

    # ──────────────────────────────────────────────────────────────────────
    # 2. Booked meetings — duplicate detection
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def make_fingerprint(attendee_ids: list[str], topic: str) -> str:
        """Deterministic 12-char hash from sorted attendee IDs + lowercased topic stem."""
        blob = "|".join(sorted(attendee_ids)) + "|" + topic.lower().strip()
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def save_meeting(self, fingerprint: str, meeting_data: dict, ttl: int = 7 * 24 * 3600):
        self._r_set(f"meeting:{fingerprint}", meeting_data, ttl=ttl)
        logger.info(f"📅 Meeting saved to Redis/Fallback [meeting:{fingerprint}]")

    def get_meeting(self, fingerprint: str) -> dict | None:
        return self._r_get(f"meeting:{fingerprint}")

    def delete_meeting(self, fingerprint: str):
        self._r_del(f"meeting:{fingerprint}")
        logger.info(f"🗑️  Meeting deleted from Redis/Fallback [meeting:{fingerprint}]")

    def list_meetings(self) -> list[dict]:
        """List all cached meetings (Redis SCAN or in-memory scan)."""
        meetings = []
        if self.use_redis:
            for key in self.redis_client.scan_iter("meeting:*"):
                raw = self.redis_client.get(key)
                if raw:
                    data = json.loads(raw)
                    data["_fingerprint"] = key.decode().replace("meeting:", "")
                    meetings.append(data)
        else:
            for k, v in self.in_memory_store.items():
                if k.startswith("meeting:"):
                    data = dict(v)
                    data["_fingerprint"] = k.replace("meeting:", "")
                    meetings.append(data)
        return meetings

    # ──────────────────────────────────────────────────────────────────────
    # 3. User preferences — recurrence + presenter defaults
    # ──────────────────────────────────────────────────────────────────────
    def save_preferences(self, organiser_id: str, prefs: dict):
        self._r_set(f"prefs:{organiser_id}", prefs)
        logger.info(f"⚙️  Prefs saved [prefs:{organiser_id}]")

    def get_preferences(self, organiser_id: str) -> dict:
        return self._r_get(f"prefs:{organiser_id}") or {}

    # ──────────────────────────────────────────────────────────────────────
    # 4. Attendee search cache — skip LLM tool round-trip
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _search_key(query: str) -> str:
        return "search:" + hashlib.md5(query.lower().strip().encode()).hexdigest()[:10]

    def cache_search(self, query: str, results: list, ttl: int = 3600):
        self._r_set(self._search_key(query), {"results": results}, ttl=ttl)

    def get_cached_search(self, query: str) -> list | None:
        data = self._r_get(self._search_key(query))
        return data["results"] if data else None
