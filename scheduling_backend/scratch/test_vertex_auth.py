import os
from google.auth import default
from google.cloud import aiplatform
from dotenv import load_dotenv

load_dotenv()

def test_auth():
    print(f"Checking credentials via GOOGLE_APPLICATION_CREDENTIALS...")
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    print(f"Path: {creds_path}")
    
    if creds_path and os.path.exists(creds_path):
        print("✅ Credentials file found.")
    else:
        print("❌ Credentials file NOT found or path not set.")
        return

    try:
        credentials, project = default()
        print(f"✅ Successfully loaded default credentials.")
        print(f"Project ID: {project}")
        
        # Try to initialize aiplatform (this checks if the project matches)
        aiplatform.init(project=project, credentials=credentials)
        print("✅ Vertex AI initialized successfully.")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")

if __name__ == "__main__":
    test_auth()
