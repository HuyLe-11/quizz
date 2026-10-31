# QuizPDF - AI-Powered Quiz Generation & Testing Platform

Nền tảng tạo và làm bài thi trắc nghiệm từ file PDF, hỗ trợ đa dạng cấu trúc câu hỏi, KaTeX (toán học), Highlight.js (code), Dark Mode và hệ thống phân tích kết quả học tập (Analytics Dashboard).

> 🤖 **Dành cho AI / Developers**: Tài liệu này đóng vai trò là **Project Source of Truth (SOT)**. Bất kỳ AI Assistant nào tiếp nhận dự án đều có thể đọc file này để nắm bắt đầy đủ kiến trúc, dữ liệu contract, tiến độ và các bước cần triển khai tiếp theo.

---

## 1. Tổng Quan Kiến Trúc (Architecture Overview)

```
quiz/
├── README.md                      # [Root] Toàn bộ tài liệu, plans, specs & task tracker
├── backend/                       # FastAPI Backend
│   ├── data/                      # Database schemas & mock queries (demo.sql, schema1.sql)
│   ├── engine/                    # PDF Processing, OCR & Question Extraction Engine
│   └── network/                   # FastAPI Endpoints, Routers, Business Logic
├── interact/                      # Frontend Application Workspace
│   └── frontend/                  # React 19 + TypeScript + Vite + Tailwind CSS v4
│       ├── src/
│       │   ├── components/        # Layout, UI & Quiz interaction components
│       │   ├── contexts/          # State management (ThemeContext, QuizContext)
│       │   ├── data/              # Mock data (subjects, exams, questions)
│       │   ├── pages/             # HomePage, QuizPage, StatsPage, AttemptReview
│       │   ├── services/          # api.ts (Backend caller), stats.ts (Analytics & Storage)
│       │   └── types/             # Core TypeScript Data Contracts
└── v5.1/                          # Legacy tools & data extraction scripts (reference)
```

### Công Nghệ Sử Dụng (Tech Stack)
- **Frontend**: React 19, TypeScript (.tsx), Vite, React Router v7.
- **Styling**: Tailwind CSS v4 với CSS Custom Properties (Theming Light/Dark mode), phong cách tối giản (Minimalist, lấy cảm hứng từ Shadcn UI).
- **Math & Code Rendering**: KaTeX (`$...$` và `$$...$$`), Highlight.js (syntax highlight cho code block).
- **Backend**: FastAPI, SQLite, PaddleOCR API, PyMuPDF và OpenCV.

---

## 2. Đặc Tả Dữ Liệu & Question Types (`QuestionType`)

Contract chính xác định dạng câu hỏi nằm tại `interact/frontend/src/types/types.ts`:

| Loại Câu Hỏi (`type`) | Mô tả chi tiết | Các trường dữ liệu liên quan |
| :--- | :--- | :--- |
| **`mcp`** | Multiple Choice. Hỗ trợ chọn 1 (`multiple_choice: false`) hoặc chọn nhiều đáp án (`multiple_choice: true`). | `options: Option[]`, `option_answer: string[]` |
| **`true_false_ng`** | True / False / Not Given. Đánh giá tính đúng sai của nhận định. | `option_answer: ["true" \| "false" \| "ng"]` |
| **`matching`** | Ghép nối: Nối mô tả (trái) với đáp án (phải). Số lượng đáp án có thể nhiều hơn mô tả (distractors). | `matches: Match[]` (`answer`, `description`) |
| **`fill_in`** | Điền từ / Tự luận ngắn: Người dùng nhập input văn bản/công thức. | `fill_in_answers: string[]` |

Tất cả nội dung câu hỏi, mô tả, giải thích đều hỗ trợ: văn bản thường, LaTeX, code blocks và markdown image (`![alt](url)`).

---

## 3. Bản Đồ Chức Năng Đã Hoàn Thành (Implementation Walkthrough)

### 3.1. Giao Diện & Trải Nghiệm (UI & Theming)
- **Dark Mode / Light Mode**: Tích hợp biến theme trong `index.css`, DarkModeToggle hỗ trợ animation và ghi nhớ lựa chọn vào `localStorage`.
- **Contrast & Legibility Fixes**: Xóa bỏ màu chữ cố định trong `QuestionContent.tsx` để hỗ trợ kế thừa động (`text-inherit`). Tất cả trạng thái Selected, Correct, Wrong trong `McpQuestion`, `TrueFalseQuestion`, `MatchingQuestion`, `FillInQuestion` đều có độ tương phản cao trong Dark Mode (`dark:bg-indigo-950/40`, `dark:bg-emerald-950/30`, v.v.).

### 3.2. Màn Hình (Pages)
- **`HomePage.tsx`** (`/`):
  - Kéo thả file PDF để tạo đề (`FileUpload.tsx`).
  - Lưới môn học có sẵn (`SubjectGrid.tsx` + `SubjectCard.tsx`), chọn môn sẽ mở modal danh sách đề thi.
- **`QuizPage.tsx`** (`/quiz/:examId`):
  - Thanh tiến độ (`QuizProgress.tsx`), đếm số câu X/Y.
  - Lưới điều hướng câu hỏi (`QuizNavigation.tsx`), hỗ trợ nhảy câu nhanh.
  - Sau khi nộp bài: Hiển thị kết quả đúng/sai từng câu và hộp giải thích chi tiết (`ExplanationBox.tsx`).
- **`StatsPage.tsx`** (`/stats`):
  - 4 Key Metrics: Tổng bài đã làm, Điểm trung bình (Conic Gradient Ring), Tổng thời gian học, Tỷ lệ đúng.
  - Phân tích điểm mạnh/yếu (Weaknesses & Strengths) theo từng Question Type.
  - Biểu đồ tiến trình điểm số (SVG Line Chart): Có tính năng phóng to (Expand Modal), rê chuột xem chi tiết số câu đúng/sai từng loại.
  - Bảng lịch sử các lần thi theo thứ tự mới nhất.
- **`AttemptReview.tsx`** (`/attempt/:attemptId`):
  - Xem lại chi tiết từng câu trong một lần thi cụ thể.

### 3.3. Dịch Vụ Phân Tích & Lưu Trữ (Stats Engine)
- File `interact/frontend/src/services/stats.ts`:
  - `checkQuestionCorrectness`: Thuật toán tự động chấm điểm cả 4 loại câu hỏi.
  - `StatsService`: Quản lý lưu trữ attempt logs và tổng hợp số liệu phân tích.
  - Đã có tài liệu hướng dẫn cụ thể về việc chuyển đổi sang FastAPI + PostgreSQL trong code comment.

---

## 4. Danh Sách Công Việc & Tiến Độ (Task Tracker)

### Frontend
- [x] Khởi tạo dự án React + TypeScript + Vite + Tailwind CSS v4.
- [x] Khai báo TypeScript types (`types.ts`).
- [x] Tạo Theme Context (Light/Dark mode) & Quiz Context.
- [x] Xây dựng UI: Header, DarkModeToggle, FileUpload, SubjectCard/Grid.
- [x] Xây dựng 4 dạng câu hỏi: Mcp, TrueFalse, Matching, FillIn.
- [x] Xây dựng `QuestionContent` (KaTeX + Highlight.js + Markdown Image).
- [x] Xây dựng Quiz navigation, Progress bar, Explanation box.
- [x] Sửa lỗi Dark Mode contrast khi chọn đáp án.
- [x] Xây dựng Service thống kê (`stats.ts`) lưu trữ LocalStorage.
- [x] Xây dựng màn hình `StatsPage.tsx` và `AttemptReview.tsx`.
- [x] Kết nối tự động lưu kết quả vào thống kê khi Submit bài thi.

### Backend
- [x] Thiết lập FastAPI app trong `backend/network/main.py`.
- [x] Nhận PDF, gọi PaddleOCR official API từ backend và lưu quiz vào SQLite.
- [x] Lịch sử đề đã xử lý, xem lại đề, ghi nhận lượt làm.
- [x] Crop ảnh câu hỏi bằng PyMuPDF và nhận diện dấu đỏ theo câu bằng OpenCV.
- [x] Đọc đề đã tạo qua `GET /api/exams/:examId` theo contract frontend.
- [ ] Thay JSON file store bằng PostgreSQL theo `schema1.sql` khi triển khai production.
- [ ] Bổ sung màn hình rà soát/chỉnh sửa câu hỏi và đáp án OCR trước khi xuất bản.
- [ ] Viết API Endpoints:
  - `GET /api/subjects` (Danh sách môn)
  - `GET /api/subjects/:id/exams` (Danh sách đề theo môn)
  - `GET /api/exams/:id` (Chi tiết đề & câu hỏi)
  - `POST /api/exams/:id/submit` (Chấm điểm server-side)
  - `POST /api/upload` (đã có; upload PDF & kích hoạt OCR)
  - `GET /api/attempts` & `GET /api/attempts/stats` (Truy vấn lịch sử thi)
- [x] Tích hợp PaddleOCR API adapter và parser câu hỏi cơ bản trong `backend/engine/`.

### Chạy backend OCR

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Đặt `PADDLEOCR_ACCESS_TOKEN` trong `backend/.env` (lấy từ AI Studio Access Token), sau đó chạy ở thư mục gốc dự án:

```bash
uvicorn backend.network.main:app --reload
```

Để gọi thử PaddleOCR độc lập bằng một PDF (dùng token trong `backend/.env`):

```bash
python scripts/test_paddleocr_api.py /path/to/sample.pdf --model PaddleOCR-VL-1.6
```

Đổi model thành `PP-OCRv6` để thử OCR văn bản. Có thể thêm `--save-response /tmp/paddle-response.json` để lưu response JSON đầy đủ; không chia sẻ file này nếu tài liệu PDF có nội dung riêng tư.

Frontend mặc định gọi `http://localhost:8000/api`. Có thể cấu hình CORS bằng `CORS_ORIGINS` (phân cách nhiều origin bằng dấu phẩy). Không đưa token vào frontend.

Mặc định upload dùng `PaddleOCR-VL-1.6` để phân tích bố cục tài liệu, công thức, bảng và trả kết quả Markdown theo trang; có thể chọn `PP-OCRv6` khi ưu tiên nhận dạng văn bản nhanh. PP-OCRv6 là pipeline OCR text detection/recognition, không thay thế phân tích cấu trúc tài liệu của VL. PaddleOCR official API SDK hỗ trợ `parse_document(file_path=..., model="PaddleOCR-VL-1.6")` và `ocr(file_path=..., model="PP-OCRv6")`; kết quả document parsing có `pages`/`markdownText` hoặc `markdown.text`, còn OCR có dữ liệu theo trang. Adapter giữ response gốc trong `exams[].extra.ocr_response` và markdown trong `exams[].extra.ocr_markdown` để tiện kiểm tra.

Parser hiện nhận dạng câu hỏi đánh số (ví dụ `Câu 1`, `Question 1`, `1.`) và đáp án lựa chọn A–H. Nó không suy đoán đáp án đúng; do đó `is_correct` sẽ là `false` cho tới khi bổ sung answer key hoặc người dùng hiệu chỉnh. Các câu không có lựa chọn được lưu tạm thành `fill_in`.

### SQLite, lịch sử và triển khai demo trên Render

SQLite tự khởi tạo ở `backend/data/quiz.sqlite3`; PDF gốc và ảnh crop nằm trong `backend/data/storage`. JSON cũ (`generated_exams.json`) được nhập một lần khi khởi động và được giữ nguyên. Có thể đổi đường dẫn bằng `DATABASE_PATH`, `STORAGE_DIR`, `QUIZ_DATA_FILE`.

Trên Render, triển khai backend FastAPI dưới dạng Web Service, frontend dưới dạng Static Site. Gắn Persistent Disk cho backend (ví dụ mount `/var/data`) và đặt:

```text
DATABASE_PATH=/var/data/quiz.sqlite3
STORAGE_DIR=/var/data/storage
PADDLEOCR_ACCESS_TOKEN=<token>
CORS_ORIGINS=https://<frontend-domain>
```

Ở Static Site đặt `VITE_API_BASE_URL=https://<backend-domain>` (chỉ origin, không thêm `/api`). Build frontend bằng `npm run build`, publish `dist`; rewrite mọi đường dẫn về `/index.html` để hỗ trợ React Router. Persistent Disk cần thiết vì filesystem mặc định có thể mất dữ liệu sau deploy/restart. Demo chưa có đăng nhập: nội dung/PDF/ảnh có thể được truy cập bởi người biết URL; không dùng cho tài liệu riêng tư.

### Module nhận diện dấu đỏ và ảnh câu hỏi

- `POST /api/questions/{question_record_id}/detect-red-mark`: chỉ chạy khi bật toggle; `?force=true` để thử lại. Cache detection theo file PDF, trang và cấu hình HSV.
- `POST /api/questions/{question_record_id}/crop-image`: tạo lại ảnh với `?force=true`.
- `PATCH /api/questions/{question_record_id}/red-mark-confirmation`: lưu đáp án người dùng tách biệt đáp án gốc.
- Ảnh phục vụ dưới `/api/assets/...`; detection, tọa độ crop và xác nhận nằm trong SQLite.

HSV, morphology và ngưỡng confidence cấu hình tại `backend/engine/pdf_tools.py` qua biến `RED_MARK_*`. Dấu đỏ là dấu được tìm thấy, không mặc định là đáp án đúng. Với PDF scan thiếu OCR bbox/đánh số câu, hệ thống dùng crop trang fallback; nhận diện đáp án phụ thuộc vị trí lựa chọn định vị được.

Chạy kiểm thử từ thư mục gốc:

```bash
python -m unittest discover -s backend/tests -v
```

---

## 5. Hướng Dẫn Dành Cho AI / Lập Trình Viên Mới Bắt Đầu

Khi tiếp tục phát triển dự án này:

1. **Không thay đổi Data Contracts trừ khi thảo luận trước**: Kiểm tra `interact/frontend/src/types/types.ts` trước khi can thiệp vào cấu trúc dữ liệu.
2. **Nguyên tắc UI**:
   - Sử dụng CSS Variables được định nghĩa tại `interact/frontend/src/index.css`.
   - Giữ phong cách tối giản, code comments bằng tiếng Anh rõ ràng, không dùng emoji trong code comments.
3. **Cách chạy Frontend**:
   ```bash
   cd interact/frontend
   npm install
   npm run dev
   ```
4. **Backend FastAPI Integration**:
   - Tham khảo các ghi chú chi tiết trong `interact/frontend/src/services/api.ts` và `interact/frontend/src/services/stats.ts` để triển khai đúng format REST API mà Frontend mong đợi.
