import re
import json

def clean_line(line):
    """Loại bỏ tọa độ dạng (123,456) và các số kèm dấu ngoặc đơn không phải từ ngữ"""
    # Xóa các pattern tọa độ
    cleaned = re.sub(r'\(\d{1,4},\d{1,4}\)', '', line)
    cleaned = re.sub(r'\(-?\d{1,4},-?\d{1,4}\)', '', cleaned)
    # Xóa các dấu ngoặc đơn chỉ chứa số (nếu còn)
    cleaned = re.sub(r'\(\d+\)', '', cleaned)
    # Xóa các tọa độ dạng (x,y) bị OCR lẫn
    cleaned = re.sub(r'\(\d+,\d+\)', '', cleaned)
    # Xóa các số và dấu phẩy, ngoặc còn sót
    cleaned = re.sub(r'^\s*[\d,\(\)]+\s*$', '', cleaned)
    return cleaned.strip()

def is_junk_line(line):
    """Kiểm tra dòng rác (tọa độ, số, studocu)"""
    if not line.strip():
        return True
    # Chỉ toàn số, dấu cách, dấu ngoặc
    if re.match(r'^[\d\s\(\)\,]+$', line.strip()):
        return True
    # Chứa studocu, downloaded, etc
    if re.search(r'studeersnel|Downloaded by|Dit document', line, re.IGNORECASE):
        return True
    # Dòng chỉ có dấu √ hoặc ✓
    if line.strip() in ['√', '✓']:
        return True
    return False

def parse_matching_block(text):
    """Xử lý câu hỏi dạng matching (ghép)"""
    pairs = {}
    # Tìm các mô tả và đáp án (dấu √ thường nằm cạnh key)
    # Cấu trúc: "Mô tả ... key (ví dụ B) √"
    # Hoặc "Mô tả ... → key"
    
    # Cách 1: tìm dòng có dấu √ và key trước đó
    lines = text.split('\n')
    current_desc = ""
    for i, line in enumerate(lines):
        if is_junk_line(line):
            continue
        line = line.strip()
        # Nếu dòng bắt đầu bằng chữ cái in hoa và dấu phẩy? Ví dụ "A, Over The Top (OTT)"
        # Đó là danh sách lựa chọn, bỏ qua
        if re.match(r'^[A-F],\s', line):
            continue
        # Tìm dấu √ hoặc ✓
        if '√' in line or '✓' in line:
            # Tìm key (có thể ở dòng trước hoặc cùng dòng)
            # key thường là chữ cái in hoa đứng trước dấu √
            key_match = re.search(r'([A-F])\s*[√✓]', line)
            if not key_match and i > 0:
                # tìm ở dòng trước
                prev = lines[i-1].strip()
                key_match = re.search(r'([A-F])\s*$', prev)
            if key_match:
                key = key_match.group(1)
                # tìm mô tả: dòng trước đó (có thể vài dòng)
                desc = ""
                for j in range(i-1, max(0,i-4), -1):
                    if not is_junk_line(lines[j]):
                        desc_candidate = clean_line(lines[j])
                        if desc_candidate and not re.match(r'^[A-F],', desc_candidate):
                            desc = desc_candidate
                            break
                if desc and key:
                    pairs[desc] = key
    # Nếu không tìm thấy theo √, thử theo mẫu "→"
    if not pairs:
        arrow_matches = re.findall(r'(.+?)\s*→\s*([A-F])', text)
        for desc, key in arrow_matches:
            desc = clean_line(desc)
            if desc:
                pairs[desc] = key
    return pairs

def parse_mcq_block(text):
    """Xử lý câu hỏi trắc nghiệm thông thường"""
    # Tìm đáp án
    answer = []
    # Dòng "Câu trả lời đúng là:" hoặc "The correct answer is:"
    ans_match = re.search(r'(?:Câu trả lời đúng là:|The correct answer is:?)\s*(.+?)(?=\n|$)', text, re.IGNORECASE)
    if ans_match:
        ans_text = ans_match.group(1).strip()
        # Lấy các chữ cái A, B, C,... hoặc cả cụm
        # Nếu có dấu phẩy như "A, C" -> nhiều đáp án
        if ',' in ans_text and re.match(r'[A-F](?:\s*,\s*[A-F])+', ans_text):
            answer = [x.strip() for x in ans_text.split(',')]
        else:
            # Có thể là một chữ cái hoặc một cụm text
            letter_match = re.match(r'^([A-F])(?:\.|\s|$)', ans_text)
            if letter_match:
                answer = [letter_match.group(1)]
            else:
                answer = [ans_text]
    else:
        # Tìm dấu √ trong lựa chọn
        for line in text.split('\n'):
            if '√' in line or '✓' in line:
                opt_match = re.search(r'([A-F])\.', line)
                if opt_match:
                    answer.append(opt_match.group(1))
    # Loại bỏ trùng
    answer = list(dict.fromkeys(answer))
    return answer

def parse_true_false_block(text):
    """Xử lý câu đúng/sai"""
    # Tìm "Đáp án chính xác là \"Đúng\"" hoặc "Đáp án chính xác là \"Sai\""
    match = re.search(r'Đáp án chính xác là "?(Đúng|Sai)"?', text)
    if match:
        return [match.group(1)]
    # Hoặc tìm dấu √ ở Đúng hoặc Sai
    if 'Đúng.*?√' in text or 'Đúng.*?✓' in text:
        return ['Đúng']
    if 'Sai.*?√' in text or 'Sai.*?✓' in text:
        return ['Sai']
    return []

def extract_question_block(block_text):
    """Trích xuất một câu hỏi từ khối văn bản đã cleaned"""
    lines = block_text.split('\n')
    # Lọc bỏ dòng rác
    clean_lines = [clean_line(l) for l in lines if not is_junk_line(l) and clean_line(l)]
    if not clean_lines:
        return None
    
    text = '\n'.join(clean_lines)
    
    # Lấy ID câu hỏi
    q_id_match = re.search(r'C[âa]u H[oỏ]i (\d+)', text)
    if not q_id_match:
        return None
    q_id = int(q_id_match.group(1))
    
    # Trạng thái: Đúng, Sai, Đúng một phần
    status = ''
    if re.search(r'Đúng một phần', text):
        status = 'Đúng một phần'
    elif re.search(r'^Đúng\s', text) or re.search(r'\bĐúng\b', text[:50]):
        status = 'Đúng'
    elif re.search(r'^Sai\s', text) or re.search(r'\bSai\b', text[:50]):
        status = 'Sai'
    
    # Lấy nội dung câu hỏi (dòng đầu tiên sau "Câu Hỏi X")
    question_text = ''
    for i, line in enumerate(clean_lines):
        if re.search(r'C[âa]u H[oỏ]i \d+', line):
            if i+1 < len(clean_lines):
                question_text = clean_lines[i+1]
            break
    if not question_text and len(clean_lines) > 1:
        question_text = clean_lines[1]
    
    # Xác định loại câu hỏi
    q_type = 'mcq'
    if 'GHÉP' in text or 'Matching' in text or 'DANH SÁCH TRẢ LỜI' in text:
        q_type = 'matching'
    elif re.search(r'Chọn câu.*?(Đúng|Sai)', text, re.DOTALL):
        q_type = 'true_false'
    
    # Lấy options (nếu có)
    options = []
    opt_pattern = r'\n([A-F])\.\s*(.+?)(?=\n[A-F]\.|\nCâu trả lời|$|\nĐáp án)'
    matches = re.findall(opt_pattern, '\n' + text, re.DOTALL)
    for key, opt_text in matches:
        opt_text = opt_text.strip().replace('\n', ' ')
        options.append({'key': key, 'text': opt_text})
    
    # Lấy đáp án
    answer = None
    multi = False
    if q_type == 'matching':
        answer = parse_matching_block(text)
        multi = False
    elif q_type == 'true_false':
        answer = parse_true_false_block(text)
        multi = False
    else:
        answer = parse_mcq_block(text)
        multi = len(answer) > 1 if answer else False
    
    if not answer:
        answer = [] if q_type != 'matching' else {}
    
    return {
        'id': q_id,
        'week': 6,
        'type': q_type,
        'question': question_text,
        'options': options,
        'answer': answer,
        'multi': multi,
        'status': status
    }

def main():
    with open('/Users/huyle/Documents/UET/network_quiz_dataset/v5.1/data/result.txt', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Tách các khối ảnh
    blocks = re.split(r'={80,}', content)
    questions = []
    
    for block in blocks:
        if not block.strip():
            continue
        # Bỏ qua khối chỉ chứa header thông tin (không có "Câu Hỏi")
        if 'Câu Hỏi' not in block and 'Cau Hoi' not in block:
            continue
        # Bỏ qua khối có "studeersnel" và không có câu hỏi
        if 'studeersnel' in block and 'Câu Hỏi' not in block:
            continue
        
        # Làm sạch block: loại bỏ các dòng rác
        lines = block.split('\n')
        cleaned_lines = []
        for line in lines:
            if is_junk_line(line):
                continue
            cleaned = clean_line(line)
            if cleaned:
                cleaned_lines.append(cleaned)
        
        if not cleaned_lines:
            continue
        
        cleaned_block = '\n'.join(cleaned_lines)
        q_obj = extract_question_block(cleaned_block)
        if q_obj:
            questions.append(q_obj)
    
    # Sắp xếp theo id
    questions.sort(key=lambda x: x['id'])
    
    # Ghi JSON
    with open('questions.json', 'w', encoding='utf-8') as out:
        json.dump(questions, out, indent=2, ensure_ascii=False)
    
    print(f"Đã trích xuất {len(questions)} câu hỏi vào file questions.json")

if __name__ == '__main__':
    main()