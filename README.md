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
  - `StatsService`: Lưu lượt làm và tổng hợp thống kê trên trình duyệt bằng `localStorage`.
  - Với đề PDF đã lưu trong backend, `QuizContext` cũng gửi lượt làm đến API để ghi vào SQLite.

### 3.4. Luồng từ lúc tải PDF đến khi làm bài

```mermaid
flowchart TD
    A[Người dùng chọn PDF và mô hình OCR] --> B[Frontend gửi multipart POST /api/upload]
    B --> C[Backend kiểm tra PDF, dung lượng và mô hình]
    C --> D[PaddleOCR xử lý tài liệu theo trang]
    D --> E[Parser tách câu hỏi, lựa chọn và số trang]
    E --> F[Giữ số câu gốc, gán ID duy nhất cho dữ liệu]
    F --> G{Nội dung parse thiếu hoặc chưa tin cậy?}
    G -- Có --> H[PyMuPDF crop câu hỏi; OCR bbox làm fallback]
    G -- Không --> I[Cho phép tạo ảnh crop sau từ thẻ câu hỏi]
    H --> J[Lưu PDF, OCR và câu hỏi vào file storage và SQLite]
    I --> J
    J --> K[Frontend chuyển đến /quiz/{exam_id}]
    K --> L[GET /api/exams/{exam_id} và hiển thị Question Card]
    L --> M[Người dùng chọn, sửa câu trả lời và chuyển câu]
    M --> N{Nộp bài?}
    N -- Chưa --> M
    N -- Nộp --> O[Chấm ở frontend và hiển thị kết quả]
    O --> P[Lưu thống kê trình duyệt và lượt làm SQLite]
    P --> Q[Lịch sử PDF cho phép xem lại hoặc làm lại]
```

**Chi tiết các bước:**

1. Ở trang chủ, người dùng chọn PDF và mô hình `PaddleOCR-VL-1.6` hoặc `PP-OCRv6`. Frontend gửi tệp bằng `multipart/form-data` tới `POST /api/upload`; token PaddleOCR chỉ nằm ở backend.
2. Backend kiểm tra định dạng PDF, dung lượng tối đa 30 MB và mô hình được phép dùng, rồi gọi PaddleOCR. VL phù hợp hơn với tài liệu có bố cục, bảng và công thức; PP-OCRv6 tập trung nhận dạng văn bản.
3. Parser xử lý nội dung theo trang, tách câu hỏi đánh số và các lựa chọn A–H. Hệ thống lưu trang nguồn và số thứ tự câu gốc. Nếu PDF lặp số câu giữa các trang, backend vẫn giữ số gốc để định vị trên PDF và cấp ID câu nội bộ không trùng trong đề.
4. Câu hỏi thiếu nội dung hoặc thiếu cấu trúc được crop tự động bằng PyMuPDF. Nếu PDF không có text layer phù hợp, crop có thể dùng bounding box OCR; nếu ranh giới không rõ, hệ thống giữ fallback rộng hơn. Với câu đã parse đầy đủ, người dùng vẫn có thể tạo crop từ công cụ trong thẻ câu hỏi.
5. Backend lưu PDF gốc và ảnh crop trong `STORAGE_DIR`; thông tin đề, câu hỏi, lựa chọn, dữ liệu OCR và metadata ảnh được lưu trong SQLite. Khi lưu thành công, frontend chuyển sang `/quiz/{exam_id}` và lấy câu hỏi qua `GET /api/exams/{exam_id}`.
6. Trong Question Card, người dùng có thể đổi lựa chọn trắc nghiệm, chỉnh câu trả lời điền, hoặc sửa nối đáp án trước khi nộp. Thanh tiến độ và lưới điều hướng cho phép quay lại câu trước để chỉnh. Sau khi nộp, các ô trả lời bị khóa và giao diện hiển thị kết quả/đáp án theo dữ liệu hiện có.
7. Kết quả lần làm được lưu vào thống kê trong `localStorage`; với đề PDF đã xử lý, frontend đồng thời gửi lượt làm tới `POST /api/exams/{exam_id}/attempts` để lưu trong SQLite. Trang **Lịch sử PDF** gọi `GET /api/exams`, cho phép xem lại câu hỏi hoặc bắt đầu một lượt làm mới.

**Giới hạn chấm điểm hiện tại:** parser OCR chỉ tạo câu hỏi trắc nghiệm `mcp` hoặc điền `fill_in`; giao diện có hỗ trợ thêm đúng/sai và nối đáp án nhưng parser PDF chưa tự chuyển các dạng đó. Parser cũng không tự suy đoán đáp án đúng: lựa chọn OCR được lưu `is_correct = false`, câu điền chưa có danh sách đáp án chấp nhận. Vì vậy điểm tự chấm chỉ có ý nghĩa khi đề có đáp án phù hợp. Với câu trắc nghiệm, có thể bật nhận diện dấu đỏ trên từng câu rồi xác nhận đáp án; đáp án xác nhận được dùng riêng khi chấm, không ghi đè dữ liệu gốc. Hiện chưa có màn hình rà soát toàn bộ nội dung OCR trước khi bắt đầu làm bài.

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
- [x] Nhận PDF, gọi PaddleOCR API từ backend và lưu quiz, OCR, lượt làm vào SQLite.
- [x] Lịch sử đề đã xử lý, xem lại đề, ghi nhận lượt làm.
- [x] Crop ảnh câu hỏi bằng PyMuPDF và nhận diện dấu đỏ theo câu bằng OpenCV.
- [x] Đọc đề đã tạo qua `GET /api/exams/:examId` theo contract frontend.
- [x] Nhập dữ liệu từ `generated_exams.json` một lần vào SQLite khi khởi động.
- [ ] Chuyển SQLite sang dịch vụ database phù hợp nếu cần nhiều instance/ghi đồng thời.
- [ ] Bổ sung màn hình rà soát/chỉnh sửa câu hỏi và đáp án OCR trước khi xuất bản.
- [ ] Viết API Endpoints:
  - `GET /api/subjects` (Danh sách môn)
  - `GET /api/subjects/:id/exams` (Danh sách đề theo môn)
  - `POST /api/exams/:id/submit` (Chấm điểm server-side)
  - `GET /api/attempts/stats` (Thống kê lượt làm từ backend)
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

Mặc định upload dùng `PaddleOCR-VL-1.6` để phân tích bố cục tài liệu, công thức, bảng và trả kết quả Markdown theo trang; có thể chọn `PP-OCRv6` khi ưu tiên nhận dạng văn bản nhanh. PP-OCRv6 là pipeline OCR text detection/recognition, không thay thế phân tích cấu trúc tài liệu của VL. Backend gửi job đến PaddleOCR hosted API (`/api/v2/ocr/jobs`), đợi kết quả và chuẩn hóa phản hồi JSON theo trang; response gốc được giữ trong `exams[].extra.ocr_response` và markdown trong `exams[].extra.ocr_markdown`. Cách gọi REST này tránh đưa bộ SDK suy luận PaddleOCR cùng các dependency ML của nó vào serverless bundle.

Parser hiện nhận dạng câu hỏi đánh số (ví dụ `Câu 1`, `Question 1`, `1.`) và đáp án lựa chọn A–H. Nó không suy đoán đáp án đúng; lựa chọn OCR được lưu với `is_correct = false` cho tới khi người dùng xác nhận đáp án bằng công cụ nhận diện dấu đỏ. Các câu không có lựa chọn được lưu tạm thành `fill_in`.

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

### Triển khai thử trên Vercel Services

Repository có `vercel.json` với hai service: `backend` (FastAPI, thư mục `backend`) và `frontend` (Vite, thư mục `interact/frontend`). Rewrite public chuyển `/api/*` đến backend và các đường dẫn còn lại đến frontend. Frontend production gọi API tương đối qua `/api`; khi chạy local vẫn dùng `http://localhost:8000`. Không có service binding vì frontend là static app chạy trong trình duyệt và gọi API qua public rewrite.

Trong Vercel, chọn framework **Services** (tính năng đang ở Private Beta, cần quyền truy cập) và đặt biến môi trường backend `PADDLEOCR_ACCESS_TOKEN`. Không đặt token trong frontend hay trong `vercel.json`. Để `VITE_API_BASE_URL` trống khi frontend/backend dùng chung domain.

Đây mới là cấu hình demo, chưa phù hợp để lưu dữ liệu bền vững hoặc nhận PDF lớn: Vercel Function giới hạn body request/response 4.5 MB, dù ứng dụng hiện cho phép file đến 30 MB. Trên Vercel, ứng dụng dùng `/tmp/quizpdf` để tránh ghi vào filesystem chỉ đọc; dữ liệu ở đó tạm thời, có thể mất và không được chia sẻ giữa các instance. Muốn dùng ổn định cần chuyển database sang dịch vụ SQL và file sang object storage; để nhận PDF lớn cần upload trực tiếp từ trình duyệt lên object storage rồi gửi đường dẫn cho backend. Thời gian OCR cũng phải nằm trong giới hạn Function của plan. Xem [giới hạn Function](https://vercel.com/docs/functions/limitations), [upload file lớn](https://vercel.com/docs/vercel-blob/server-upload) và [Vercel Services](https://vercel.com/docs/services).
