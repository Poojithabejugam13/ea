import redis
import json
import sys

def view_redis():
    try:
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        
        # 1. Get key from args or default
        if len(sys.argv) > 1:
            key = sys.argv[1]
        else:
            print("\n--- Available Redis Keys ---")
            keys = r.keys("*")
            for i, k in enumerate(sorted(keys)):
                print(f"{i+1}. {k}")
            
            choice = input("\nEnter key name (or number) to view: ").strip()
            if choice.isdigit() and int(choice) <= len(keys):
                key = sorted(keys)[int(choice)-1]
            else:
                key = choice

        if not key:
            return

        # 2. Fetch and Format
        data = r.get(key)
        if data:
            print(f"\n--- Data for '{key}' ---")
            try:
                # Try to parse as JSON
                parsed = json.loads(data)
                print(json.dumps(parsed, indent=4))
            except json.JSONDecodeError:
                # Fallback to raw if not JSON
                print(data)
        else:
            print(f"\n❌ Key '{key}' not found.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    view_redis()
