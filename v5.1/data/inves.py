import json 

with open('//Users/huyle/Documents/UET/network_quiz_dataset/v3copy2/deepseek_json_20260515_f7d72e.json', 'r') as f:
    data = json.load(f)

type = set()

for item in data['questions']:
    # print(item.keys())
    type.update(item.keys())

print(type)