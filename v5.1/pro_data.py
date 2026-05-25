#!/usr/bin/env python3
"""
process_quiz.py — Chuyển đổi file JSON câu hỏi sang biến DATA cho NetQuiz v2.

Hỗ trợ các loại câu hỏi đầu vào:
  - mcq                  → mcq (hoặc simple_tf nếu chỉ có 2 đáp án Đúng/Sai)
  - fill_in_multiple_blanks → matching (subtype: fill_blanks)
  - matching             → matching (subtype: matching)

Usage:
  python process_quiz.py input.json [output_data.js]

  # Nếu không truyền output_path, mặc định là data.js
  python process_quiz.py deepseek_json_20260516_c44c44.json data.js
"""

import json
import sys
import os
from pathlib import Path


# ─── Constants ───────────────────────────────────────────────────────────────

WEEK_LABELS = {
    '1': 'Tổng hợp',
    '4': 'Tuần 4',
    '5': 'Tuần 5',
    '6': 'Tuần 6',
    '7': 'Tuần 7',
    '8': 'Tuần 8',
}

WEEK_DESCS = {
    '1': 'Câu hỏi tổng hợp & ôn thi',
    '4': 'Giới thiệu mạng máy tính',
    '5': 'Tầng vận chuyển & ứng dụng',
    '6': 'Tầng mạng & định tuyến',
    '7': 'Tầng vận chuyển – UDP & RDT',
    '8': 'Tầng liên kết & LAN',
}

WEEK_ICONS = {
    '1': '📚', '4': '🌐', '5': '📡',
    '6': '🛰️', '7': '📦', '8': '🔗',
}

# Tập các cặp đáp án Đúng/Sai — để nhận dạng câu hỏi simple_tf
DUNG_SAI_SET = {'Đúng', 'Sai'}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def is_simple_tf(q: dict) -> bool:
    """
    Trả về True nếu đây là câu MCQ dạng Đúng/Sai đơn:
    - Đúng 2 lựa chọn, text là 'Đúng' và 'Sai'
    - Không phải multi-select
    """
    opts = q.get('options', [])
    if q.get('multi', False):
        return False
    if len(opts) != 2:
        return False
    return {o['text'].strip() for o in opts} == DUNG_SAI_SET


def key_to_text(key: str, options: list[dict]) -> str:
    """Lấy text của một option theo key."""
    for opt in options:
        if opt['key'] == key:
            return opt['text']
    return key  # fallback: trả về key nếu không tìm thấy


def normalize_options(options: list[dict]) -> list[dict]:
    """Chuẩn hoá options, chỉ giữ key và text."""
    return [{'key': o['key'], 'text': o['text']} for o in options]


# ─── Transformers per type ───────────────────────────────────────────────────

def transform_mcq(q: dict) -> dict:
    """
    Xử lý câu hỏi MCQ (bao gồm câu tính toán).
    Nếu chỉ có 2 đáp án Đúng/Sai → chuyển sang simple_tf.
    """
    if is_simple_tf(q):
        # Lấy text của đáp án đúng ("Đúng" hoặc "Sai")
        answer_key   = q['answer'][0]
        answer_text  = key_to_text(answer_key, q.get('options', []))
        return {
            'type':        'true_false',
            'subtype':     'simple_tf',
            'answer':      answer_text,    # "Đúng" | "Sai"
        }

    return {
        'type':        'mcq',
        'subtype':     'mcq',
        'options':     normalize_options(q.get('options', [])),
        'answer':      q.get('answer', []),   # list of option keys
        'multi':       bool(q.get('multi', False)),
    }


def transform_fill_in_multiple_blanks(q: dict) -> dict:
    """
    Chuyển fill_in_multiple_blanks → matching (subtype: fill_blanks).

    Đầu vào:
      options: [{key, text}]   — pool các lựa chọn
      answer:  {blank_label: choice_key, ...}

    Đầu ra (chuẩn matching của v2):
      matches: [{item, answer_key, answer_label}, ...]
      choices: [{key, label}, ...]
    """
    options      = q.get('options', [])
    answer_map   = q.get('answer', {})   # {label: key}
    option_index = {o['key']: o['text'] for o in options}

    matches = [
        {
            'item':         blank_label,
            'answer_key':   choice_key,
            'answer_label': option_index.get(choice_key, choice_key),
        }
        for blank_label, choice_key in answer_map.items()
    ]

    choices = [{'key': o['key'], 'label': o['text']} for o in options]

    return {
        'type':    'matching',
        'subtype': 'fill_blanks',
        'matches': matches,
        'choices': choices,
    }


def transform_matching(q: dict) -> dict:
    """
    Chuyển matching sang chuẩn v2.

    Đầu vào:
      options: [{key, text}]   — tập các câu trả lời
      answer:  {item_text: choice_key, ...}

    Đầu ra:
      matches: [{item, answer_key, answer_label}, ...]
      choices: [{key, label}, ...]
    """
    options      = q.get('options', [])
    answer_map   = q.get('answer', {})   # {item_text: key}
    option_index = {o['key']: o['text'] for o in options}

    matches = [
        {
            'item':         item_text,
            'answer_key':   choice_key,
            'answer_label': option_index.get(choice_key, choice_key),
        }
        for item_text, choice_key in answer_map.items()
    ]

    choices = [{'key': o['key'], 'label': o['text']} for o in options]

    return {
        'type':    'matching',
        'subtype': 'matching',
        'matches': matches,
        'choices': choices,
    }


# ─── Main transformer ────────────────────────────────────────────────────────

TRANSFORMERS = {
    'mcq':                    transform_mcq,
    'fill_in_multiple_blanks': transform_fill_in_multiple_blanks,
    'matching':               transform_matching,
}


def process_question(q: dict) -> dict:
    """
    Chuẩn hoá một câu hỏi từ raw JSON sang định dạng DATA v2.
    Ném ValueError nếu loại câu hỏi chưa được hỗ trợ.
    """
    qtype = q.get('type', '')

    # Các trường chung cho mọi loại câu hỏi
    base = {
        'id':          q['id'],
        'week':        q['week'],
        'question':    q['question'].strip(),
        'explanation': (q.get('explanation') or '').strip(),
        'image_url':   q.get('image') or None,
    }

    transformer = TRANSFORMERS.get(qtype)
    if transformer is None:
        raise ValueError(
            f"Câu hỏi id={q['id']}: loại '{qtype}' chưa được hỗ trợ. "
            f"Các loại hợp lệ: {list(TRANSFORMERS)}"
        )

    specific = transformer(q)
    return {**base, **specific}


# ─── Group & build DATA ──────────────────────────────────────────────────────

def build_data(questions: list[dict]) -> dict:
    """Nhóm câu hỏi theo tuần và xây dựng cấu trúc DATA."""
    weeks: dict[str, list] = {}
    for q in questions:
        w = str(q['week'])
        weeks.setdefault(w, []).append(q)

    week_info = {
        w: {
            'label': WEEK_LABELS.get(w, f'Tuần {w}'),
            'desc':  WEEK_DESCS.get(w, ''),
            'icon':  WEEK_ICONS.get(w, '📋'),
        }
        for w in sorted(weeks)
    }

    return {'weeks': weeks, 'week_info': week_info}


# ─── IO & entry point ────────────────────────────────────────────────────────

def process_file(input_path: str) -> dict:
    with open(input_path, encoding='utf-8') as f:
        raw = json.load(f)

    # Hỗ trợ cả hai định dạng:
    # - array trực tiếp: [...]
    # - object với key "questions": {"questions": [...]}
    if isinstance(raw, dict) and 'questions' in raw:
        raw = raw['questions']

    errors = []
    processed = []

    for q in raw:
        try:
            processed.append(process_question(q))
        except (ValueError, KeyError) as e:
            errors.append(str(e))

    if errors:
        print(f'\n⚠️  {len(errors)} lỗi trong quá trình xử lý:')
        for err in errors:
            print(f'   • {err}')
        print()

    return build_data(processed)


def render_data_js(data: dict, source_path: str) -> str:
    """Tạo nội dung file data.js."""
    json_str = json.dumps(data, ensure_ascii=False, indent=2)

    return f"""\
// AUTO-GENERATED — Không chỉnh sửa tay.
// Source  : {source_path}
// Script  : process_quiz.py
// Format  : NetQuiz v2 DATA
'use strict';

const DATA = {json_str};

// CommonJS export (Node.js / seed.js)
if (typeof module !== 'undefined') module.exports = DATA;
"""


def print_stats(data: dict) -> None:
    total = sum(len(v) for v in data['weeks'].values())
    print(f'\n✓ Tổng: {total} câu hỏi\n')
    for w in sorted(data['weeks']):
        qs    = data['weeks'][w]
        label = data['week_info'][w]['label']
        types: dict[str, int] = {}
        for q in qs:
            st = q.get('subtype') or q.get('type')
            types[st] = types.get(st, 0) + 1
        type_str = ' | '.join(f'{t}: {c}' for t, c in sorted(types.items()))
        print(f'  {label} (w{w}): {len(qs):3d} câu  [{type_str}]')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else 'data.js'

    if not os.path.exists(input_path):
        print(f'❌ File không tồn tại: {input_path}')
        sys.exit(1)

    print(f'📂 Đọc: {input_path}')
    data = process_file(input_path)

    js_content = render_data_js(data, Path(input_path).name)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(js_content)

    print_stats(data)
    print(f'\n✓ Đã lưu: {output_path}  ({len(js_content):,} bytes)\n')


if __name__ == '__main__':
    main()