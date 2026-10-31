-- ============================================================
--  QUIZ APP — CORE DATABASE SCHEMA  (PostgreSQL 14+)
--  Loại câu hỏi: mcp | true_false_ng | matching | fill_in
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "unaccent";   -- full-text search tiếng Việt

-- ============================================================
-- ENUM
-- ============================================================
CREATE TYPE question_type AS ENUM (
    'mcp',           -- multiple-choice problem (ABCD…)
    'true_false_ng', -- True / False / Not Given  (số lựa chọn tuỳ đề)
    'matching',      -- nối cột  (answers > descriptions được phép)
    'fill_in'        -- điền / tự luận
);

-- ============================================================
-- 1. EXAMS  — mỗi bộ đề thi
-- ============================================================
CREATE TABLE exams (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT        NOT NULL,           -- "Đề thi HK2 2024 – Mã 001"
    subject          TEXT,                           -- "Vật lý", "Toán", …
    year             SMALLINT,                       -- 2024
    author           TEXT,                           -- người tạo / nguồn
    uploaded_by      TEXT,                           -- account upload
    duration_minutes INT,                            -- thời gian làm bài
    source_file      TEXT,                           -- tên file gốc (PDF…)
    uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    extra            JSONB       NOT NULL DEFAULT '{}' -- metadata mở rộng
);

COMMENT ON TABLE exams IS
    'Mỗi row = 1 đề thi. Một file PDF có thể sinh ra nhiều exams.';

-- ============================================================
-- 2. QUESTIONS  — bảng trung tâm
-- ============================================================
--
-- composite_id  = "<exam_name_slug>_Q<zero-padded question_id>"
--   VD: "de_hk2_2024_ma001_Q03"
--   Đây là "id tổng" theo đặc tả — duy nhất toàn hệ thống.
--
-- question_content / explanation_content dùng JSONB để chứa
-- hỗn hợp text + LaTeX + hình ảnh + code trong cùng một trường.
-- Cấu trúc block: [{ "type": "text"|"latex"|"image"|"code", "value": "…" }]
-- ============================================================
CREATE TABLE questions (
    id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    composite_id        TEXT          NOT NULL UNIQUE,
    exam_id             UUID          NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    question_id         INT           NOT NULL,       -- số thứ tự trong đề
    type                question_type NOT NULL,
    multiple_choice     BOOLEAN       NOT NULL DEFAULT FALSE,

    -- Nội dung câu hỏi — array of content blocks
    -- [{"type":"text","value":"Câu hỏi…"},{"type":"latex","value":"x^2+y^2"},{"type":"image","value":"s3://…"}]
    question_content    JSONB         NOT NULL DEFAULT '[]',

    -- Giải thích đáp án (cùng định dạng block)
    explanation_content JSONB         NOT NULL DEFAULT '[]',

    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    UNIQUE (exam_id, question_id)
);

COMMENT ON COLUMN questions.composite_id     IS '"<exam_slug>_Q<nn>" — ID tổng theo đặc tả';
COMMENT ON COLUMN questions.question_content IS 'Array of blocks: [{type: text|latex|image|code, value: string}]';

-- ============================================================
-- 3. MCP_OPTIONS  — đáp án cho mcp  VÀ  true_false_ng
-- ============================================================
--  Dùng chung vì true_false_ng có số lựa chọn tuỳ đề:
--    True/False       → 2 rows  (label "True", "False")
--    True/False/NG    → 3 rows  (label "True", "False", "Not Given")
--    ABCD thường      → N rows
--  multiple_choice = TRUE → nhiều is_correct = TRUE được phép
-- ============================================================
CREATE TABLE mcp_options (
    id           UUID     PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id  UUID     NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    label        TEXT     NOT NULL,        -- "A","B","C" / "True","False","NG"
    content      JSONB    NOT NULL DEFAULT '[]',
    is_correct   BOOLEAN  NOT NULL DEFAULT FALSE,
    order_index  SMALLINT NOT NULL DEFAULT 0,

    UNIQUE (question_id, label)
);

COMMENT ON TABLE mcp_options IS
    'Đáp án ABCD cho mcp. Dùng lại cho true_false_ng — label và số lựa chọn tuỳ đề.';

-- ============================================================
-- 4. MATCH_PAIRS  — đáp án cho câu matching
-- ============================================================
--  Mỗi row = 1 answer (+ description nếu có).
--  Distractor: answer dư không cần nối → description_content = NULL, is_distractor = TRUE
--
--  VD: 3 descriptions, 5 answers → 3 rows + 2 distractor rows
-- ============================================================
CREATE TABLE match_pairs (
    id                  UUID     PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id         UUID     NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    description_content JSONB,             -- NULL nếu là distractor
    answer_content      JSONB    NOT NULL DEFAULT '[]',
    is_distractor       BOOLEAN  NOT NULL DEFAULT FALSE,
    order_index         SMALLINT NOT NULL DEFAULT 0
);

COMMENT ON COLUMN match_pairs.is_distractor IS
    'TRUE = answer dư, không có description cần nối.';

-- ============================================================
-- 5. FILL_IN_ANSWERS  — đáp án cho câu fill_in
-- ============================================================
--  Một câu có thể chấp nhận nhiều đáp án (VD: "H₂O" và "nước" đều đúng).
--  order_index = 0 là đáp án chính / model answer.
-- ============================================================
CREATE TABLE fill_in_answers (
    id           UUID     PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id  UUID     NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    content      JSONB    NOT NULL DEFAULT '[]',
    order_index  SMALLINT NOT NULL DEFAULT 0
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX idx_questions_exam      ON questions(exam_id);
CREATE INDEX idx_questions_type      ON questions(type);
CREATE INDEX idx_questions_composite ON questions(composite_id);
CREATE INDEX idx_mcp_question        ON mcp_options(question_id);
CREATE INDEX idx_match_question      ON match_pairs(question_id);
CREATE INDEX idx_fillin_question     ON fill_in_answers(question_id);

-- JSONB GIN — tìm kiếm full-text trong nội dung câu hỏi
CREATE INDEX idx_question_content_gin ON questions USING GIN (question_content);
CREATE INDEX idx_exams_extra_gin      ON exams      USING GIN (extra);