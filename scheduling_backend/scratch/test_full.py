import sys, os, asyncio
sys.path.insert(0, os.getcwd())
from src.dependencies import get_ai_agent

async def main():
    try:
        agent = get_ai_agent()
        agent.session_mgr.set_session('test', {})
        res = await agent.process_prompt('Select attendee: 102 | Rithwika Sharma (EID: 102)', 'test')
        print("RESULT:")
        import json
        print(json.dumps(res, indent=2))
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
