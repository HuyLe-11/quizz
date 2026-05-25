python3 << 'PYEOF'
import json, re

with open('./deepseek_json_20260516_c44c44.json') as f:
    raw = json.load(f)

qs = raw['questions']

def parse_tf_items(question_text):
    """Parse 'prefix: a. text; b. text' into (main_q, [(key, text), ...])"""
    # Split at first colon that precedes 'a.'
    m = re.search(r':\s*(?=[a-j][.:])', question_text)
    if not m:
        return question_text, []
    main_q = question_text[:m.start()].strip()
    items_text = question_text[m.end():]
    items = re.split(r';\s*(?=[a-j][.:]\s)', items_text)
    parsed = []
    for item in items:
        im = re.match(r'^([a-j])[.:]\s*(.+?)\.?\s*$', item.strip(), re.IGNORECASE)
        if im:
            parsed.append({'key': im.group(1).lower(), 'text': im.group(2).strip()})
    return main_q, parsed

def get_item_label(match_item):
    """Get the displayable item text from a matching question item"""
    for key in ['scenario', 'description', 'layer', 'statement', 'item', 'term']:
        if key in match_item:
            return match_item[key]
    # Fallback: first non-answer key
    for k, v in match_item.items():
        if k != 'answer':
            return v
    return ''

def extract_option_letter(option_str):
    """Extract letter from 'A. text' or 'A: text'"""
    m = re.match(r'^([A-J])[.:\s]\s*', option_str)
    return m.group(1) if m else option_str[0]

def extract_option_text(option_str):
    """Extract text from 'A. text'"""
    m = re.match(r'^[A-J][.:\s]\s*(.+)$', option_str)
    return m.group(1) if m else option_str

# Process all questions
processed = []
weeks_set = set()

for q in qs:
    week = q['week']
    weeks_set.add(week)
    qtype = q['type']
    
    pq = {
        'id': q['id'],
        'week': week,
        'type': qtype,
        'question': q['question'],
    }
    
    if qtype == 'multiple_choice' or qtype == 'calculation':
        opts = q.get('options', [])
        pq['options'] = [{'key': extract_option_letter(o), 'text': extract_option_text(o)} for o in opts]
        ans = q.get('answer', '')
        if isinstance(ans, list):
            pq['answer'] = [a.strip() for a in ans]
            pq['multi'] = True
        else:
            pq['answer'] = [ans.strip()] if ans else []
            pq['multi'] = False
        pq['explanation'] = q.get('explanation', '')
        pq['subtype'] = 'calculation' if qtype == 'calculation' else 'mcq'
        pq['type'] = 'mcq'
    
    elif qtype == 'true_false':
        ans = q.get('answer', '')
        if isinstance(ans, dict):
            # Multi-item true/false
            main_q, items = parse_tf_items(q['question'])
            pq['question'] = main_q
            pq['items'] = items
            # Normalize answers
            pq['answer'] = {k: v for k, v in ans.items()}
            pq['subtype'] = 'multi_tf'
        else:
            # Simple true/false
            pq['answer'] = str(ans).strip()
            pq['subtype'] = 'simple_tf'
    
    elif qtype == 'matching':
        matches = q.get('matches', [])
        pq['matches'] = []
        for m in matches:
            item_text = get_item_label(m)
            ans_raw = m.get('answer', '')
            # Parse answer like 'C (Packet switching)' → key='C', label='Packet switching'
            ans_m = re.match(r'^([A-J])\s*[\(\-]?\s*(.+?)\)?$', ans_raw)
            pq['matches'].append({
                'item': item_text,
                'answer_key': ans_m.group(1) if ans_m else ans_raw[0],
                'answer_label': ans_m.group(2).strip() if ans_m else ans_raw,
            })
        # Build choices list from answers
        choices_seen = {}
        for m in pq['matches']:
            k = m['answer_key']
            if k not in choices_seen:
                choices_seen[k] = m['answer_label']
        pq['choices'] = [{'key': k, 'label': v} for k, v in sorted(choices_seen.items())]
    
    elif qtype == 'fill_in':
        answers = q.get('answers', q.get('answer', ''))
        if isinstance(answers, dict):
            pq['answers'] = answers
        else:
            pq['answers'] = {'answer': str(answers)}
        pq['subtype'] = 'fill_in'
    
    processed.append(pq)

# Group by week
week_map = {}
for q in processed:
    w = str(q['week'])
    if w not in week_map:
        week_map[w] = []
    week_map[w].append(q)

# Map week 1 → label as 'Ôn tập' or week 6/7
# Keep original weeks: 1, 4, 5, 8
# Rename week 1 → 'Tổng hợp' 
week_info = {
    '1': {'label': 'Tổng hợp', 'desc': 'Câu hỏi tổng hợp & ôn thi'},
    '4': {'label': 'Tuần 4', 'desc': 'Giới thiệu mạng máy tính'},
    '5': {'label': 'Tuần 5', 'desc': 'Tầng vận chuyển & ứng dụng'},
    '8': {'label': 'Tuần 8', 'desc': 'Tầng liên kết & LAN'},
}

compact = json.dumps({'weeks': week_map, 'week_info': week_info}, ensure_ascii=False, separators=(',', ':'))
print(f'Processed {len(processed)} questions across weeks {sorted(weeks_set)}')
print(f'Compact size: {len(compact)//1024} KB')
for w in sorted(week_map.keys()):
    wqs = week_map[w]
    types = {}
    for q in wqs:
        types[q['type']] = types.get(q['type'], 0) + 1
    print(f"  Week {w} ({week_info.get(w,{}).get('label',w)}): {len(wqs)} questions | {types}")

# Save for embedding
with open('quiz_processed.json', 'w', encoding='utf-8') as f:
    f.write(compact)
PYEOF

