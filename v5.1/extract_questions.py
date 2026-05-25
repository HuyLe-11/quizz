#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import re
from pathlib import Path

def parse_result_file(file_path):
    """Parse result.txt using simple line-by-line parsing"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    questions = []

    # Split by "Câu Hỏi" followed by a number
    parts = re.split(r'(?=Câu Hỏi\s+\d+)', content)

    for part in parts[1:]:  # Skip the first part before any question
        question = parse_single_question(part)
        if question:
            questions.append(question)

    return questions

def clean_text(text):
    """Clean OCR text"""
    # Remove coordinates
    text = re.sub(r'\(\d+,\d+\)', '', text)
    # Remove checkmarks
    text = re.sub(r'√', '', text)
    # Remove footer text
    text = re.sub(r'Downloaded by.*', '', text)
    text = re.sub(r'Dit document.*', '', text)
    # Multiple spaces to single
    text = re.sub(r' +', ' ', text)
    return text.strip()

def parse_single_question(block_text):
    """Parse one question block"""

    # Extract question ID
    id_match = re.search(r'Câu Hỏi\s+(\d+)', block_text)
    if not id_match:
        return None
    question_id = int(id_match.group(1))

    # Clean text
    block_text = clean_text(block_text)

    # Extract status
    status_match = re.search(r'(Đúng|Sai|Đúng một phần)', block_text)
    status = status_match.group(1) if status_match else ""

    # Extract question text - from after status to before first option
    # Look for the pattern: status, then question text, then first option
    question_pattern = r'(?:Đúng|Sai|Đúng một phần)(.*?)(?=A[\s\.\,:]|A\)|B[\s\.\,:]|B\)|DANH SÁCH|Chọn)'
    question_match = re.search(question_pattern, block_text, re.DOTALL)
    if not question_match:
        return None

    question_text = question_match.group(1).strip()
    question_text = question_text.lstrip(',').strip()  # Remove leading commas
    if len(question_text) < 5:
        return None

    # Detect question type - matching questions have "Ghép"
    is_matching = "Ghép" in question_text or "ghép" in block_text.lower()
    is_multi = "Chọn tất cả" in block_text or "Select all" in block_text.lower()

    # Extract options
    options = extract_options(block_text)
    if not options:
        return None

    # Determine if multi-choice (more than 4 options or explicit marker)
    if not is_multi and len(options) > 4:
        is_multi = True

    question_type = "matching" if is_matching else "mcq"

    # Extract answers
    if is_matching:
        answer = extract_matching_answers(block_text, options)
    else:
        answer = extract_mcq_answers(block_text, options, is_multi)

    # Extract explanation
    explanation = extract_explanation(block_text)

    return {
        "id": question_id,
        "week": 7,
        "type": question_type,
        "question": question_text,
        "image": None,
        "options": options,
        "answer": answer,
        "multi": is_multi,
        "explanation": explanation
    }

def extract_options(content):
    """Extract options from content"""
    options = []

    # Find where options section starts (after question, before answer marker)
    # Options section is between question and "Câu trả lời" or answer markers

    answer_markers = ["Câu trả lời của bạn", "The correct", "correct answer", "Giải thích"]
    options_end = len(content)
    for marker in answer_markers:
        idx = content.find(marker)
        if idx >= 0:
            options_end = min(options_end, idx)

    # Look for "DANH SÁCH TRẢ LỜI:" which marks options section for matching
    danh_sach_idx = content.find("DANH SÁCH TRẢ LỜI:")
    if danh_sach_idx >= 0:
        options_start = danh_sach_idx
    else:
        # For regular MCQ, options start after question
        # Find first option letter pattern "A" followed by . or ,
        first_opt = re.search(r'[A-H]\s*[\.,]', content)
        options_start = first_opt.start() if first_opt else 0

    options_section = content[options_start:options_end]

    # Parse options - more carefully to avoid picking up stray letters
    # Pattern: letter + punctuation at line/word boundary, followed by text until next option
    opt_pattern = r'\b([A-H])\s*[\.,:]\s*(.{0,200}?)(?=\b[A-H]\s*[\.,:]\s|\nC|$)'

    matches = re.finditer(opt_pattern, options_section, re.DOTALL)
    seen_letters = set()

    for match in matches:
        letter = match.group(1).upper()

        # Skip if we've already seen this letter
        if letter in seen_letters:
            continue

        text = match.group(2).strip()

        # Clean up the text
        text = re.sub(r'\.{3,}|\s+', lambda m: ' ' if m.group() != '...' else ' ', text)
        text = re.sub(r' +', ' ', text).strip()
        text = text.rstrip(',.').strip()

        # Skip empty or very short options
        if text and len(text) > 1:
            options.append({
                "key": letter,
                "text": text
            })
            seen_letters.add(letter)

    return options

def extract_matching_answers(block_text, options):
    """Extract answers for matching questions - returns dict"""
    answer_dict = {}

    # For matching questions, the structure is:
    # Description1 → Letter1
    # Description2 → Letter2
    # Find this pattern in the "The correct answer is" section

    correct_section_match = re.search(
        r'(?:The correct answer is|Câu trả lời đúng là)(.*?)(?:Câu hỏi|Downloaded|Dit|$)',
        block_text,
        re.IGNORECASE | re.DOTALL
    )

    if not correct_section_match:
        return answer_dict

    answer_section = correct_section_match.group(1)

    # Extract all mappings: description → letter
    # Pattern: any text ending with → followed by letter
    mapping_pattern = r'(.+?)\s*→\s*([A-H])(?:[,\.\)]|$)'
    matches = re.finditer(mapping_pattern, answer_section, re.DOTALL)

    for match in matches:
        description = match.group(1).strip()
        letter = match.group(2)

        # Clean description - remove leading colons, commas, spaces
        description = re.sub(r'^[:,\s]+', '', description)
        description = re.sub(r' +', ' ', description)
        description = description.rstrip(',.').strip()

        # Limit to 150 chars
        if len(description) > 150:
            description = description[:147] + "..."

        if description and len(description) > 3:
            answer_dict[description] = letter

    return answer_dict

def extract_mcq_answers(block_text, options, is_multi):
    """Extract answers for MCQ questions - returns list"""
    answers = []

    # Find the answer section
    answer_section_match = re.search(
        r'(?:Câu trả lời đúng là|The correct answer is|The correct answers are|correct answer)[\s:]*(.{0,500}?)(?:Câu hỏi|Downloaded|Dit|$)',
        block_text,
        re.IGNORECASE | re.DOTALL
    )

    if not answer_section_match:
        return []

    answer_section = answer_section_match.group(1)

    # Extract all letters that appear as answer indicators
    # Look for: just a letter, arrow + letter, or letter at word boundary
    letter_patterns = [
        r'→\s*([A-H])',  # Arrow pattern
        r'\b([A-H])\b(?=[,\.\)\s]|$)',  # Single letter followed by punctuation or space
        r'đáp án[\s:]*([A-H])',  # Vietnamese answer marker
        r'answer[\s:]*([A-H])',  # English answer marker
    ]

    seen = set()
    for pattern in letter_patterns:
        matches = re.finditer(pattern, answer_section, re.IGNORECASE)
        for match in matches:
            letter = match.group(1).upper()
            if letter not in seen:
                answers.append(letter)
                seen.add(letter)
                # For non-multi questions, stop after first answer
                if not is_multi:
                    return answers

    # If still no answers, try looking in a specific section before options section
    # Sometimes the answer is just the option number repeated
    if not answers and not is_multi:
        # For true/false or simple questions, look for option text that repeats
        answer_text_match = re.search(
            r'(?:Câu trả lời đúng là|The correct answer is)[\s:]*(.+?)(?:Câu hỏi|Câu trả lời của|$)',
            block_text,
            re.IGNORECASE | re.DOTALL
        )
        if answer_text_match:
            answer_text = answer_text_match.group(1)
            # Find which option this text matches
            for opt in options:
                if opt['text'][:30].lower() in answer_text.lower()[:100]:
                    return [opt['key']]

    return answers

def extract_explanation(block_text):
    """Extract explanation from block"""

    # Find answer/explanation section
    markers_list = [
        "Câu trả lời đúng là",
        "The correct answer is",
        "The correct answers are",
        "correct answer",
        "Giải thích"
    ]

    answer_idx = len(block_text)
    for marker in markers_list:
        idx = block_text.find(marker)
        if idx >= 0:
            answer_idx = idx
            break

    if answer_idx >= len(block_text) - 10:
        return ""

    # Get text after marker
    explanation_section = block_text[answer_idx:answer_idx + 500]

    # Try to extract meaningful explanation
    # Pattern 1: Text after answer letter
    match = re.search(
        r'(?:là|is)[\s:]*[A-H](?:[,\.\)]|\s|$)(.*?)(?:Câu hỏi|Câu|$)',
        explanation_section,
        re.DOTALL
    )

    if match:
        explanation = match.group(1).strip()
        # Clean up
        explanation = re.sub(r'\n+', ' ', explanation)
        explanation = re.sub(r' +', ' ', explanation)
        explanation = explanation.rstrip(',').strip()
        explanation = re.sub(r'^[:,\s]+', '', explanation)  # Remove leading colons/commas

        if explanation and len(explanation) > 10:
            if len(explanation) > 300:
                explanation = explanation[:297] + "..."
            return explanation

    # Pattern 2: Just get text after marker
    if answer_idx < len(block_text):
        text_after = block_text[answer_idx + 20:]
        # Get first sentence or first 200 chars
        first_sentence = re.search(r'([^.!?]+[.!?])', text_after)
        if first_sentence:
            explanation = first_sentence.group(1).strip()
            explanation = re.sub(r' +', ' ', explanation)
            explanation = re.sub(r'^[:,\s]+', '', explanation)
            if len(explanation) > 10 and len(explanation) < 300:
                return explanation

    return ""

def main():
    input_file = Path("/Users/huyle/Documents/UET/network_quiz_dataset/v5.1/data/result92106.txt")
    output_file = Path("/Users/huyle/Documents/UET/network_quiz_dataset/v5.1/data/extracted_questions92106.json")

    print(f"Reading from: {input_file}")

    if not input_file.exists():
        print(f"Error: File not found")
        return

    questions = parse_result_file(input_file)

    print(f"✓ Extracted {len(questions)} questions")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)

    print(f"✓ Saved to: {output_file}")

    # Show statistics
    print(f"\nStatistics:")
    print(f"  Total questions: {len(questions)}")
    mcq_count = sum(1 for q in questions if q['type'] == 'mcq')
    matching_count = sum(1 for q in questions if q['type'] == 'matching')
    answers_count = sum(1 for q in questions if q['answer'])
    explained_count = sum(1 for q in questions if q['explanation'])

    print(f"  MCQ questions: {mcq_count}")
    print(f"  Matching questions: {matching_count}")
    print(f"  Questions with answers: {answers_count}")
    print(f"  Questions with explanations: {explained_count}")

if __name__ == "__main__":
    main()
