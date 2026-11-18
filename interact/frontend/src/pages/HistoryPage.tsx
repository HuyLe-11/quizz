import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuiz } from "../contexts/QuizContext";
import { getProcessedExams } from "../services/api";
import type { ProcessedExamSummary } from "../types/types";

export function HistoryPage() {
  const [exams, setExams] =
      useState<ProcessedExamSummary[]>([]);

  const [loading, setLoading] =
      useState(true);

  const [error, setError] =
      useState<string | null>(null);

  const [retryCount, setRetryCount] =
      useState(0);

  const navigate = useNavigate();

  const { resetQuiz } = useQuiz();

  useEffect(() => {
    let ignore = false;

    async function loadHistory() {
      try {
        const result =
            await getProcessedExams();

        if (!ignore) {
          setExams(result);
        }
      } catch (requestError) {
        if (!ignore) {
          setError(
              requestError instanceof Error
                  ? requestError.message
                  : "Không thể tải lịch sử xử lý.",
          );
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    }

    void loadHistory();

    return () => {
      ignore = true;
    };
  }, [retryCount]);

  const retryHistory = () => {
    setLoading(true);
    setError(null);
    setRetryCount((previous) => previous + 1);
  };

  const redoExam = (examId: string) => {
    resetQuiz();
    navigate(`/quiz/${examId}`);
  };

  return (
      <div className="mx-auto min-h-[calc(100vh-4rem)] w-full max-w-5xl px-6 py-10 text-[var(--color-text)]">
        <div className="mb-8">
          <p className="text-sm uppercase tracking-[0.25em] text-[var(--color-text-secondary)]">
            Thư viện đề đã xử lý
          </p>

          <h1 className="mt-2 text-3xl font-semibold">
            Lịch sử PDF
          </h1>

          <p className="mt-2 text-[var(--color-text-secondary)]">
            Mở lại câu hỏi đã trích xuất hoặc làm lại
            bài trắc nghiệm.
          </p>
        </div>

        {loading ? (
            <p
                role="status"
                className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-6"
            >
              Đang tải lịch sử...
            </p>
        ) : error ? (
            <div
                role="alert"
                className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-6"
            >
              <p className="text-[var(--color-danger)]">
                {error}
              </p>

              <button
                  type="button"
                  onClick={retryHistory}
                  className="mt-3 rounded-md bg-[var(--color-primary)] px-4 py-2 text-white"
              >
                Thử lại
              </button>
            </div>
        ) : exams.length === 0 ? (
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center">
              <p className="text-lg font-medium">
                Chưa có PDF nào được xử lý.
              </p>

              <button
                  type="button"
                  onClick={() => navigate("/")}
                  className="mt-4 rounded-md bg-[var(--color-primary)] px-4 py-2 text-white"
              >
                Tải PDF lên
              </button>
            </div>
        ) : (
            <div className="space-y-4">
              {exams.map((exam) => (
                  <article
                      key={exam.id}
                      className="flex flex-col gap-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0">
                      <h2 className="truncate text-lg font-semibold">
                        {exam.name}
                      </h2>

                      <p className="mt-1 truncate text-sm text-[var(--color-text-secondary)]">
                        {exam.source_file || "Tệp PDF"}
                      </p>

                      <p className="mt-1 text-sm text-[var(--color-text-muted)]">
                        {exam.question_count} câu hỏi ·{" "}
                        {new Date(
                            exam.uploaded_at,
                        ).toLocaleString("vi-VN")}

                        {!exam.has_source_pdf &&
                            " · Không còn PDF gốc"}
                      </p>
                    </div>

                    <div className="flex shrink-0 flex-wrap gap-2">
                      <button
                          type="button"
                          onClick={() =>
                              navigate(`/exams/${exam.id}`)
                          }
                          className="rounded-md border border-[var(--color-border)] px-4 py-2 text-sm hover:bg-[var(--color-surface-hover)]"
                      >
                        Xem câu hỏi
                      </button>

                      <button
                          type="button"
                          onClick={() => redoExam(exam.id)}
                          className="rounded-md bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
                      >
                        Làm lại
                      </button>
                    </div>
                  </article>
              ))}
            </div>
        )}
      </div>
  );
}
