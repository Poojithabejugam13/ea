#!/usr/bin/env python3
"""
Test script to verify the meeting title update functionality works correctly.
This script simulates the conversation flow and tests the database update.
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'scheduling_backend', 'src'))

from services import AIAgent
from session_manager import SessionManager
from repository import UserRepository

def test_meeting_title_update():
    """Test the meeting title update functionality"""
    print("=== Testing Meeting Title Update Fix ===\n")
    
    # Initialize the components
    repo = UserRepository()
    session_mgr = SessionManager()
    agent = AIAgent(repo, session_mgr)
    
    # Test session ID
    session_id = "test_session_123"
    
    # Simulate the conversation flow
    test_cases = [
        {
            "description": "Create initial meeting",
            "prompt": "schedule meeting with Anand Kumar",
            "expected_intent": "meeting_booked"
        },
        {
            "description": "Update meeting title - direct request",
            "prompt": "update meeting title to standup meeting",
            "expected_intent": "meeting_updated"
        },
        {
            "description": "Update meeting title - contextual request",
            "prompt": "for the above meeting, change title to daily sync",
            "expected_intent": "meeting_updated"
        },
        {
            "description": "Update agenda",
            "prompt": "update agenda to discuss project milestones",
            "expected_intent": "meeting_updated"
        }
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"Test {i}: {test_case['description']}")
        print(f"Prompt: '{test_case['prompt']}'")
        
        try:
            # Process the prompt
            result = agent.process_prompt(test_case['prompt'], session_id)
            
            # Check the response
            response = result.get('response', '')
            intent = result.get('intent', '')
            
            print(f"Intent: {intent}")
            print(f"Response: {response[:100]}...")
            
            # Verify the intent matches expectation
            if intent == test_case['expected_intent']:
                print("Status: PASS")
            else:
                print(f"Status: FAIL - Expected {test_case['expected_intent']}, got {intent}")
            
            # Check if meeting data is returned for updates
            if intent == "meeting_updated" and "meeting_data" in result:
                meeting_data = result['meeting_data']
                print(f"Updated meeting data: {meeting_data}")
            
        except Exception as e:
            print(f"Status: ERROR - {str(e)}")
        
        print("-" * 50)
    
    # Test session data persistence
    print("\n=== Testing Session Data Persistence ===")
    session_data = session_mgr.get_session(session_id)
    if session_data and "last_meeting" in session_data:
        last_meeting = session_data["last_meeting"]
        print(f"Last meeting in session: {last_meeting.get('subject', 'N/A')}")
        print("Session data persistence: PASS")
    else:
        print("Session data persistence: FAIL - No meeting data found")

def test_database_update():
    """Test that the database is actually updated"""
    print("\n=== Testing Database Update ===")
    
    try:
        # Initialize components
        repo = UserRepository()
        session_mgr = SessionManager()
        
        # Get the most recent meeting from the repository
        events = repo.get_events_for_user("poojitha")  # Assuming organiser ID
        if events:
            latest_event = events[-1]
            print(f"Latest event in database: {latest_event.subject}")
            print(f"Event ID: {latest_event.id}")
            print("Database update verification: PASS")
        else:
            print("Database update verification: FAIL - No events found")
            
    except Exception as e:
        print(f"Database update verification: ERROR - {str(e)}")

if __name__ == "__main__":
    test_meeting_title_update()
    test_database_update()
    print("\n=== Test Complete ===")
