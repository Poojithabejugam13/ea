import traceback
from src.dependencies import get_ai_agent

def main():
    try:
        agent = get_ai_agent()
        res = agent.process_prompt("schedule meet with radhakrishna", "test")
        print("SUCCESS:", res)
    except Exception as e:
        print("CAUGHT EXCEPTION:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
