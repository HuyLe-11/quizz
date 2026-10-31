import { useParams, Link } from "react-router-dom";
import { StatsService } from "../services/stats";
import { MOCK_EXAMS } from "../data/mockData";
import type { QuizAttempt } from "../types/types";

export function AttemptReview() {
  const { attemptId } = useParams<{ attemptId: string }>();
  const attempts = StatsService.getAttempts();
  const attempt = attempts.find((a) => a.id === attemptId) as QuizAttempt | undefined;
  const questionTypeNames: Record<string, string> = {
    mcp: "Trắc nghiệm",
    true_false_ng: "Đúng / Sai / Không có thông tin",
    matching: "Nối đáp án",
    fill_in: "Điền vào chỗ trống",
  };

  if (!attempt) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <h2 className="text-2xl font-semibold">Không tìm thấy lần làm bài</h2>
        <p className="mt-2 text-sm text-[var(--color-text-secondary)]">Không tìm thấy dữ liệu của lần làm bài này.</p>
        <Link to="/stats" className="mt-4 inline-block text-[var(--color-primary)]">Quay lại thống kê</Link>
      </div>
    );
  }

  const exam = MOCK_EXAMS.find((e) => e.id === attempt.exam_id) || null;

  return (
    <div className="max-w-5xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--color-text)]">Xem lại: {attempt.exam_title}</h1>
          <p className="text-sm text-[var(--color-text-secondary)]">Điểm: {attempt.score}% — Thời gian: {Math.floor(attempt.time_spent/60)} phút {attempt.time_spent%60} giây</p>
        </div>
        <Link to="/stats" className="text-sm text-[var(--color-primary)]">Quay lại</Link>
      </div>

      <div className="space-y-4">
        {(attempt.question_results || []).map((qr) => {
          const question = exam?.questions?.find((q) => q.question_id === qr.question_id) || null;
          return (
            <div key={qr.question_id} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
              <div className="flex items-start justify-between">
                <div>
                  <div className="text-sm text-[var(--color-text-secondary)]">Câu {qr.question_id} — Dạng: {questionTypeNames[qr.type] || qr.type}</div>
                  {question && <div className="mt-2 text-[var(--color-text)]">{question.question}</div>}
                </div>
                <div className={`ml-4 font-semibold ${qr.is_correct ? 'text-emerald-400' : 'text-rose-400'}`}>{qr.is_correct ? 'Đúng' : 'Sai'}</div>
              </div>

              {question?.explanation && (
                <div className="mt-3 text-sm text-[var(--color-text-secondary)]">
                  <strong>Giải thích:</strong>
                  <div className="mt-1">{question.explanation}</div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
