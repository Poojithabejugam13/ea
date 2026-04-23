"""
Tests — TDD for scheduling engine.
Run: pytest tests/ -v
"""

import pytest
from src.repository import UserRepository, MOCK_EVENTS, Event, EventTime, AttendeeEntry, EmailAddress, OnlineMeeting
from src.services import extract_options, classify_option_type


@pytest.fixture(autouse=True)
def reset_events():
    """Reset MOCK_EVENTS before each test to avoid state bleed."""
    original = {k: list(v) for k, v in MOCK_EVENTS.items()}
    yield
    MOCK_EVENTS.clear()
    MOCK_EVENTS.update(original)


@pytest.fixture
def repo():
    return UserRepository()


# ---------------------------------------------------------------------------
# Fuzzy search
# ---------------------------------------------------------------------------

class TestFuzzySearch:
    def test_exact_match(self, repo):
        results = repo.search_users("Rithwika")
        names = [u.displayName for u in results]
        assert "Rithwika Singh" in names
        assert "Rithwika Sharma" in names



    def test_caps_insensitive(self, repo):
        results = repo.search_users("RITHWIKA")
        assert len(results) >= 1

    def test_special_chars_stripped(self, repo):
        results = repo.search_users("Rithwika!")
        assert len(results) >= 1

    def test_department_match(self, repo):
        results = repo.search_users("Engineering")
        depts = [u.department for u in results]
        assert all(d == "Engineering" for d in depts)

    def test_no_result_for_garbage(self, repo):
        results = repo.search_users("xyzxyzxyz123")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Team fetch
# ---------------------------------------------------------------------------

class TestTeamFetch:
    def test_department_fetch(self, repo):
        results = repo.get_users_by_department("Finance")
        assert all(u.department == "Finance" for u in results)
        assert len(results) == 3  # Radha Krishna, Arjun Singh, Krishna Yadhav

    def test_job_title_fetch(self, repo):
        results = repo.get_users_by_job_title("Accountant")
        assert len(results) >= 2


# ---------------------------------------------------------------------------
# Multiple attendees
# ---------------------------------------------------------------------------

class TestMultipleAttendees:
    def test_both_rithwikas_can_be_added(self, repo):
        r1 = repo.get_user_by_id("101")
        r2 = repo.get_user_by_id("102")
        event = Event(
            id="test_e1",
            subject="Dual Rithwika Meeting",
            bodyPreview="Test",
            start=EventTime(dateTime="2026-04-15T10:00:00Z", timeZone="UTC"),
            end=EventTime(dateTime="2026-04-15T11:00:00Z", timeZone="UTC"),
            location="Virtual",
            attendees=[
                AttendeeEntry(emailAddress=EmailAddress(address=r1.mail, name=r1.displayName), type="required", userId=r1.id),
                AttendeeEntry(emailAddress=EmailAddress(address=r2.mail, name=r2.displayName), type="optional", userId=r2.id),
            ],
            organizer=EmailAddress(address="poojitha.reddy@test.com", name="Poojitha Reddy"),
        )
        repo.create_event(event)
        events_101 = repo.get_events_for_user("101")
        events_102 = repo.get_events_for_user("102")
        assert any(e.id == "test_e1" for e in events_101)
        assert any(e.id == "test_e1" for e in events_102)


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

class TestConflictDetection:
    def test_no_conflict(self, repo):
        from src.mcp_server import check_conflict_detail
        result = check_conflict_detail("103", "2026-04-15T10:00:00Z", "2026-04-15T11:00:00Z")
        assert result["conflict"] is False

    def test_full_overlap_conflict(self, repo):
        from src.mcp_server import check_conflict_detail
        # Poojitha has e1: 2026-04-11 09:00-10:00
        result = check_conflict_detail("103", "2026-04-11T09:00:00Z", "2026-04-11T10:00:00Z")
        assert result["conflict"] is True
        assert result["type"] in ("full_overlap", "partial_overlap")

    def test_buffer_overlap(self, repo):
        from src.mcp_server import check_conflict_detail
        # Poojitha's e2 ends at 10:37 on Apr 12. Meeting at 10:40 should trigger buffer.
        result = check_conflict_detail("103", "2026-04-12T10:40:00Z", "2026-04-12T11:40:00Z", buffer_mins=15)
        assert result["conflict"] is True
        assert result["type"] == "buffer"


# ---------------------------------------------------------------------------
# Free slot finding
# ---------------------------------------------------------------------------

class TestFreeSlots:
    def test_returns_free_slots(self, repo):
        slots = repo.get_free_slots(["101", "103"], "2026-04-15", 60)
        assert len(slots) > 0
        assert "start" in slots[0]
        assert "end" in slots[0]

    def test_excludes_busy_slots(self, repo):
        # Poojitha is busy 09:00-10:00 on Apr 11
        slots = repo.get_free_slots(["103"], "2026-04-11", 60)
        for slot in slots:
            s = slot["start"]
            # 09:00 slot should NOT appear
            assert "T09:00" not in s


# ---------------------------------------------------------------------------
# Notification stub
# ---------------------------------------------------------------------------

class TestNotifications:
    def test_notification_logged(self, repo):
        from src.repository import NOTIFICATION_LOG
        initial_len = len(NOTIFICATION_LOG)
        repo.send_notification("101", "Test Subject", "Test Body")
        assert len(NOTIFICATION_LOG) == initial_len + 1
        assert NOTIFICATION_LOG[-1]["payload"]["message"]["subject"] == "Test Subject"


# ---------------------------------------------------------------------------
# Option extraction from AI text
# ---------------------------------------------------------------------------

class TestOptionExtraction:
    def test_extracts_numbered_list(self):
        text = "I found multiple matches:\n1. Rithwika Singh (rithwika.singh@test.com) - EID: 101\n2. Rithwika Sharma (rithwika.sharma@test.com) - EID: 102"
        options = extract_options(text)
        assert len(options) == 2
        assert "Rithwika Singh" in options[0]

    def test_classify_attendee_type(self):
        options = ["Rithwika Singh (email@test.com) - EID: 101"]
        opt_type = classify_option_type(options, "I found multiple matches")
        assert opt_type == "attendee"

    def test_classify_time_slot(self):
        options = ["Tomorrow 10:00 AM IST", "Tomorrow 2:00 PM IST"]
        opt_type = classify_option_type(options, "Here are free slots")
        assert opt_type == "timeslot"


# ---------------------------------------------------------------------------
# Meeting creation + join URL
# ---------------------------------------------------------------------------

class TestMeetingCreation:
    def test_join_url_generated(self, repo):
        join_url = repo.make_join_url("test_e99")
        assert "test_e99" in join_url

    def test_create_event_adds_to_organiser(self, repo):
        from src.mcp_server import create_meeting
        result = create_meeting(
            subject="Test Meeting",
            agenda="Discuss items",
            location="Virtual",
            start="2026-04-15T10:00:00Z",
            end="2026-04-15T11:00:00Z",
            attendees=[{"id": "101", "name": "Rithwika Singh", "email": "rithwika.singh@test.com", "type": "required"}],
        )
        assert result["status"] == "booked"
        assert "join_url" in result
        organiser_events = repo.get_events_for_user("103")
        assert any(e.id == result["event_id"] for e in organiser_events)
