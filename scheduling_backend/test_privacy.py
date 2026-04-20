import urllib.request
import urllib.error
import json
import uuid

def test_chat(prompt, user_id="101"):
    url = 'http://127.0.0.1:8000/agent/process'
    data = {"prompt": prompt, "session_id": str(uuid.uuid4()), "user_id": user_id}
    data_bytes = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=data_bytes, headers={'Content-Type': 'application/json'})
    
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            resp_data = json.loads(response.read().decode('utf-8'))
            print(f"\\n--- PROMPT (User: {user_id}) ---")
            print(prompt)
            print("--- RESPONSE ---")
            print(resp_data.get("response", ""))
    except urllib.error.HTTPError as e:
        print(f"\\n--- PROMPT (User: {user_id}) ---")
        print(f"HTTPError: {e.code}")
        print(e.read().decode('utf-8'))
    except Exception as e:
        print(f"Error: {e}")

# Test 1: Rithwika checking her own schedule 
test_chat("What meetings do I have scheduled for April 18?", user_id="101")

# Test 2: Rithwika checking Poojitha's schedule
test_chat("What meetings does Poojitha have scheduled for April 18?", user_id="101")
