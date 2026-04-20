"""
Graph API Client stub — same method signatures as UserRepository.
When USE_MOCK_DB=False in .env, main.py imports this instead of UserRepository.
At that point, only the HTTP call bodies need to be filled in.

Graph API base: https://graph.microsoft.com/v1.0
Auth: Authorization: Bearer {entra_token}

Endpoints used:
  GET  /users/{id}
  GET  /users/{id}/events
  POST /users/{id}/events
  PATCH /users/{id}/events/{eventId}
  DELETE /users/{id}/events/{eventId}
  POST /users/{id}/sendMail
  POST /users/{id}/calendar/getSchedule   (free/busy)
"""

import os
import httpx
from typing import List, Optional
from .repository import User, Event, EventTime, AttendeeEntry, EmailAddress, OnlineMeeting

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _headers() -> dict:
    token = os.getenv("ENTRA_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


class GraphAPIClient:
    """Future real Graph API client. Same interface as UserRepository."""

    def get_organiser(self) -> User:
        # TODO: return the authenticated user (GET /me)
        raise NotImplementedError("Implement GET /me when Graph API access is available.")

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        # TODO: GET /users/{user_id}
        raise NotImplementedError

    def search_users(self, query: str) -> List[User]:
        # TODO: GET /users?$search="displayName:{query}"
        raise NotImplementedError

    def get_users_by_department(self, dept: str) -> List[User]:
        # TODO: GET /users?$filter=department eq '{dept}'
        raise NotImplementedError

    def get_users_by_job_title(self, title: str) -> List[User]:
        # TODO: GET /users?$filter=jobTitle eq '{title}'
        raise NotImplementedError

    def get_events_for_user(self, user_id: str) -> List[Event]:
        # TODO: GET /users/{user_id}/events
        raise NotImplementedError

    def get_events_on_date(self, user_id: str, date_str: str) -> List[Event]:
        # TODO: GET /users/{user_id}/calendarView?startDateTime=...&endDateTime=...
        raise NotImplementedError

    def get_free_slots(self, user_ids: List[str], date_str: str, duration_mins: int) -> List[dict]:
        # TODO: POST /users/{organiser_id}/calendar/getSchedule
        # Body: { "schedules": [emails], "startTime": {...}, "endTime": {...}, "availabilityViewInterval": duration_mins }
        raise NotImplementedError

    def create_event(self, event: Event) -> Event:
        # TODO: POST /users/{organiser_id}/events
        raise NotImplementedError

    def update_event(self, event_id: str, new_start: str, new_end: str) -> Optional[Event]:
        # TODO: PATCH /users/{organiser_id}/events/{event_id}
        raise NotImplementedError

    def delete_event(self, event_id: str):
        # TODO: DELETE /users/{organiser_id}/events/{event_id}
        raise NotImplementedError

    def send_notification(self, user_id: str, subject: str, body: str) -> dict:
        # TODO: POST /users/{user_id}/sendMail
        # Body: { "message": { "subject": subject, "body": {...}, "toRecipients": [...] } }
        raise NotImplementedError

    def make_join_url(self, event_id: str) -> str:
        # Real Graph API: isOnlineMeeting=True on event creation returns joinUrl automatically
        return f"https://zoom.us/j/{event_id}"
