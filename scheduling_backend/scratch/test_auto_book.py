import sys, os
sys.path.insert(0, os.getcwd())
from src.dependencies import get_ai_agent
agent = get_ai_agent()
res = agent._single_person_auto_book('schedule meet with sitaram', {}, 'test')
print('FAST:', res)
