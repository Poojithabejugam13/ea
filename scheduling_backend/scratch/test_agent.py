import asyncio
import os
from src.ai_client import GeminiAgent
from src.repository import UserRepository
from src.session_manager import SessionManager

async def test_agent():
    print("--- Starting Vertex AI Agent Test ---")
    repo = UserRepository()
    session_mgr = SessionManager()
    
    try:
        print("Initializing Agent...")
        agent = GeminiAgent(repo, session_mgr)
        
        print("\nSending test prompt: 'Who are you?'")
        # Using a simple prompt to verify connectivity
        response, history = await agent.process_message_async("Who are you?", session_id="test_session")
        
        print("\n--- AI Response ---")
        print(response)
        print("-------------------")
        print("\nSUCCESS: Agent is communicating with Vertex AI.")
        
    except Exception as e:
        print(f"\nFAILURE: {e}")
        print("\nCheck if:")
        print("1. You have run 'gcloud auth application-default login'")
        print("2. Your GCP Project ID is set correctly.")

if __name__ == "__main__":
    asyncio.run(test_agent())
