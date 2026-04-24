import google.auth
try:
    credentials, project = google.auth.default()
    print(f"Detected Project: {project}")
except Exception as e:
    print(f"Error: {e}")
