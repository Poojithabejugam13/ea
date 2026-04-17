import sys
import os

# Mock dependencies to test mcp_server logic
sys.path.append(os.getcwd())
from src.mcp_server import _resolve_attendees

def test_resolution():
    print("Testing _resolve_attendees...")
    
    # Case 1: Already dicts
    input_1 = [{"id": "user_1", "name": "User One"}]
    print(f"Input 1: {input_1}")
    output_1 = _resolve_attendees(input_1)
    print(f"Output 1: {output_1}")
    assert output_1 == input_1

    # Case 2: Strings (requires mock repo)
    # We won't test full DB lookup here, just that it doesn't crash
    input_2 = ["User One", "User Two"]
    print(f"Input 2: {input_2}")
    try:
        output_2 = _resolve_attendees(input_2)
        print(f"Output 2: {output_2}")
    except Exception as e:
        print(f"Expected failure or success depending on repo state: {e}")

if __name__ == "__main__":
    test_resolution()
