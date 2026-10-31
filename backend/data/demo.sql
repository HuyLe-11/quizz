-- ============================================================
--  SEED DATA — demo đủ 4 loại câu hỏi
--  Chạy SAU 01_schema.sql
-- ============================================================

-- -------------------------------------------------------
-- EXAM
-- -------------------------------------------------------
INSERT INTO exams (id, name, subject, year, author, uploaded_by, duration_minutes, source_file)
VALUES (
    'aaaaaaaa-0000-0000-0000-000000000001',
    'Đề thi Vật lý HK2 2024 – Mã 001',
    'Vật lý',
    2024,
    'GV Nguyễn Văn A',
    'admin@school.edu.vn',
    45,
    'vat_ly_hk2_2024_ma001.pdf'
);

-- -------------------------------------------------------
-- Helper: composite_id = "vat_ly_hk2_2024_ma001_Q<nn>"
-- -------------------------------------------------------

-- ============================================================
-- CÂU 1 — MCP (1 đáp án đúng)
-- ============================================================
INSERT INTO questions (id, composite_id, exam_id, question_id, type, multiple_choice, question_content, explanation_content)
VALUES (
    'bbbbbbbb-0001-0000-0000-000000000001',
    'vat_ly_hk2_2024_ma001_Q01',
    'aaaaaaaa-0000-0000-0000-000000000001',
    1,
    'mcp',
    FALSE,
    '[
        {"type":"text",  "value":"Một vật khối lượng 2 kg chuyển động với vận tốc 3 m/s. Động năng của vật là:"},
        {"type":"latex", "value":"E_k = \\\\frac{1}{2}mv^2"}
    ]',
    '[
        {"type":"text",  "value":"Áp dụng công thức động năng:"},
        {"type":"latex", "value":"E_k = \\\\frac{1}{2} \\\\times 2 \\\\times 3^2 = 9 \\\\text{ J}"}
    ]'
);

INSERT INTO mcp_options (question_id, label, content, is_correct, order_index) VALUES
    ('bbbbbbbb-0001-0000-0000-000000000001', 'A', '[{"type":"latex","value":"6 \\\\text{ J}"}]',  FALSE, 0),
    ('bbbbbbbb-0001-0000-0000-000000000001', 'B', '[{"type":"latex","value":"9 \\\\text{ J}"}]',  TRUE,  1),
    ('bbbbbbbb-0001-0000-0000-000000000001', 'C', '[{"type":"latex","value":"12 \\\\text{ J}"}]', FALSE, 2),
    ('bbbbbbbb-0001-0000-0000-000000000001', 'D', '[{"type":"latex","value":"18 \\\\text{ J}"}]', FALSE, 3);

-- ============================================================
-- CÂU 2 — MCP MULTIPLE (nhiều đáp án đúng)
-- ============================================================
INSERT INTO questions (id, composite_id, exam_id, question_id, type, multiple_choice, question_content, explanation_content)
VALUES (
    'bbbbbbbb-0002-0000-0000-000000000001',
    'vat_ly_hk2_2024_ma001_Q02',
    'aaaaaaaa-0000-0000-0000-000000000001',
    2,
    'mcp',
    TRUE,
    '[{"type":"text","value":"Chọn TẤT CẢ các đơn vị đo lực hợp lệ trong hệ SI:"}]',
    '[{"type":"text","value":"Newton (N) là đơn vị đo lực. 1 N = 1 kg·m/s². kgf và dyne không thuộc hệ SI."}]'
);

INSERT INTO mcp_options (question_id, label, content, is_correct, order_index) VALUES
    ('bbbbbbbb-0002-0000-0000-000000000001', 'A', '[{"type":"text","value":"Newton (N)"}]',    TRUE,  0),
    ('bbbbbbbb-0002-0000-0000-000000000001', 'B', '[{"type":"text","value":"kilogram-force"}]', FALSE, 1),
    ('bbbbbbbb-0002-0000-0000-000000000001', 'C', '[{"type":"latex","value":"\\\\text{kg·m/s}^2"}]', TRUE, 2),
    ('bbbbbbbb-0002-0000-0000-000000000001', 'D', '[{"type":"text","value":"dyne"}]',           FALSE, 3);

-- ============================================================
-- CÂU 3 — TRUE / FALSE / NOT GIVEN  (3 lựa chọn, tuỳ đề)
-- ============================================================
INSERT INTO questions (id, composite_id, exam_id, question_id, type, multiple_choice, question_content, explanation_content)
VALUES (
    'bbbbbbbb-0003-0000-0000-000000000001',
    'vat_ly_hk2_2024_ma001_Q03',
    'aaaaaaaa-0000-0000-0000-000000000001',
    3,
    'true_false_ng',
    FALSE,
    '[{"type":"text","value":"\"Nhiệt độ của nước đá đang tan ở áp suất chuẩn luôn bằng 0°C.\""}]',
    '[{"type":"text","value":"Đây là định nghĩa của điểm băng trong thang Celsius — luôn đúng ở áp suất chuẩn (1 atm)."}]'
);

-- 3 lựa chọn (tuỳ đề — ở đây là True/False/Not Given)
INSERT INTO mcp_options (question_id, label, content, is_correct, order_index) VALUES
    ('bbbbbbbb-0003-0000-0000-000000000001', 'True',      '[{"type":"text","value":"True"}]',      TRUE,  0),
    ('bbbbbbbb-0003-0000-0000-000000000001', 'False',     '[{"type":"text","value":"False"}]',     FALSE, 1),
    ('bbbbbbbb-0003-0000-0000-000000000001', 'Not Given', '[{"type":"text","value":"Not Given"}]', FALSE, 2);

-- ============================================================
-- CÂU 4 — MATCHING  (3 descriptions, 5 answers — 2 distractor)
-- ============================================================
INSERT INTO questions (id, composite_id, exam_id, question_id, type, multiple_choice, question_content, explanation_content)
VALUES (
    'bbbbbbbb-0004-0000-0000-000000000001',
    'vat_ly_hk2_2024_ma001_Q04',
    'aaaaaaaa-0000-0000-0000-000000000001',
    4,
    'matching',
    FALSE,
    '[{"type":"text","value":"Nối mỗi đại lượng với đơn vị đo tương ứng (có đáp án dư):"}]',
    '[{"type":"text","value":"Lực → Newton, Công → Joule, Công suất → Watt. Pascal (áp suất) và Coulomb (điện tích) là đáp án dư."}]'
);

-- 3 cặp chính
INSERT INTO match_pairs (question_id, description_content, answer_content, is_distractor, order_index) VALUES
(
    'bbbbbbbb-0004-0000-0000-000000000001',
    '[{"type":"text","value":"Lực (Force)"}]',
    '[{"type":"text","value":"Newton (N)"}]',
    FALSE, 0
),
(
    'bbbbbbbb-0004-0000-0000-000000000001',
    '[{"type":"text","value":"Công (Work / Energy)"}]',
    '[{"type":"text","value":"Joule (J)"}]',
    FALSE, 1
),
(
    'bbbbbbbb-0004-0000-0000-000000000001',
    '[{"type":"text","value":"Công suất (Power)"}]',
    '[{"type":"text","value":"Watt (W)"}]',
    FALSE, 2
),
-- 2 distractor (answer dư — description = NULL)
(
    'bbbbbbbb-0004-0000-0000-000000000001',
    NULL,
    '[{"type":"text","value":"Pascal (Pa)"}]',
    TRUE, 3
),
(
    'bbbbbbbb-0004-0000-0000-000000000001',
    NULL,
    '[{"type":"text","value":"Coulomb (C)"}]',
    TRUE, 4
);

-- ============================================================
-- CÂU 5 — FILL_IN  (2 đáp án chấp nhận được)
-- ============================================================
INSERT INTO questions (id, composite_id, exam_id, question_id, type, multiple_choice, question_content, explanation_content)
VALUES (
    'bbbbbbbb-0005-0000-0000-000000000001',
    'vat_ly_hk2_2024_ma001_Q05',
    'aaaaaaaa-0000-0000-0000-000000000001',
    5,
    'fill_in',
    FALSE,
    '[
        {"type":"text",  "value":"Hoàn thành công thức tính vận tốc khi biết gia tốc và thời gian:"},
        {"type":"latex", "value":"v = v_0 + \\\\underline{\\\\hspace{2cm}}"}
    ]',
    '[{"type":"latex","value":"v = v_0 + at"}]'
);

-- 2 cách viết đều chấp nhận
INSERT INTO fill_in_answers (question_id, content, order_index) VALUES
    ('bbbbbbbb-0005-0000-0000-000000000001', '[{"type":"latex","value":"at"}]', 0),
    ('bbbbbbbb-0005-0000-0000-000000000001', '[{"type":"text","value":"a*t"}]',  1);