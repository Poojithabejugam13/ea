"""
Probe which Gemini models are callable on this GCP project via Vertex AI.
Run from: scheduling_backend directory with venv active.
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv()

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
os.environ["GOOGLE_CLOUD_PROJECT"] = os.getenv("GCP_PROJECT_ID", "")
os.environ["GOOGLE_CLOUD_LOCATION"] = os.getenv("GCP_LOCATION", "us-central1")

from google import genai

MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-pro-preview-05-06",
    "gemini-2.5-pro-preview-03-25",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-1.5-pro-001",
]

client = genai.Client(vertexai=True, project=os.getenv("GCP_PROJECT_ID"), location=os.getenv("GCP_LOCATION", "us-central1"))

for model in MODELS:
    try:
        resp = client.models.generate_content(
            model=model,
            contents="Say OK"
        )
        print(f"  OK  {model!r}: {resp.text[:40]!r}")
    except Exception as e:
        msg = str(e)[:80]
        print(f"  FAIL {model!r}: {msg}")
