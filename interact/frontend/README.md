# Quiz Frontend - App Root

Frontend của ứng dụng QuizPDF được xây dựng với React 19, TypeScript, Vite và Tailwind CSS v4.

> 📖 **Tài liệu toàn diện của dự án**: Vui lòng tham khảo file [`../../README.md`](../../README.md) tại thư mục gốc để nắm toàn bộ kiến trúc, data contracts, danh sách task và kế hoạch backend.

## Hướng Dẫn Phát Triển

### Chạy ứng dụng ở môi trường Local
```bash
npm install
npm run dev
```

### Build kiểm tra Types & Production
```bash
npm run build
```

## Cấu Trúc Thư Mục Frontend
- `src/components/`: Các components giao diện (UI layout, upload, các dạng câu hỏi quiz).
- `src/contexts/`: Quản lý state toàn cục (`ThemeContext.tsx` cho Dark Mode, `QuizContext.tsx` cho phiên thi).
- `src/data/`: Mock data cho phát triển frontend độc lập (`mockData.ts`).
- `src/pages/`: Các trang chính (`HomePage.tsx`, `QuizPage.tsx`, `StatsPage.tsx`, `AttemptReview.tsx`).
- `src/services/`:
  - `api.ts`: Kết nối REST API backend FastAPI.
  - `stats.ts`: Quản lý tính toán điểm và phân tích lịch sử làm bài (Local Storage demo, sẵn sàng kết nối DB).
- `src/types/types.ts`: Toàn bộ định nghĩa interface và types của dự án.
