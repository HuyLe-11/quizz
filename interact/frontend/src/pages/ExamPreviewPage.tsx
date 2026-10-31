import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuiz } from "../contexts/QuizContext";
import { getExam } from "../services/api";
import { QuestionContent } from "../components/quiz/QuestionContent";
import type { Exam } from "../types/types";

export function ExamPreviewPage() {
  const { examId } = useParams<{ examId: string }>();
  const [exam, setExam] = useState<Exam | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const { resetQuiz } = useQuiz();

  useEffect(() => {
    if (!examId) return;
    let cancelled = false;
    getExam(examId).then((result) => {
      if (!cancelled) setExam(result);
    }).catch((requestError: Error) => {
      if (!cancelled) setError(requestError.message || "Không thể tải đề đã xử lý.");
    });
    return () => { cancelled = true; };
  }, [examId]);

  if (error) return <div role="alert" className="mx-auto max-w-4xl p-6 text-[var(--color-danger)]">{error}</div>;
  if (!exam) return <div role="status" className="mx-auto max-w-4xl p-6">Đang tải câu hỏi...</div>;

  const redoExam = () => {
    resetQuiz();
    navigate(`/quiz/${exam.id}`);
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-10 text-[var(--color-text)]">
      <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <div>
          <Link to="/history" className="text-sm text-[var(--color-primary)]">← Lịch sử PDF</Link>
          <h1 className="mt-2 text-3xl font-semibold">{exam.title}</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{exam.question_count} câu hỏi đã trích xuất</p>
        </div>
        <button onClick={redoExam} className="rounded-md bg-[var(--color-primary)] px-5 py-2.5 font-medium text-white">Làm lại bài này</button>
      </div>

      <div className="space-y-5">
        {exam.questions.map((question, index) => (
          <article key={`${question.question_record_id || question.question_id}`} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h2 className="mb-3 font-semibold">Câu {index + 1}</h2>
            <QuestionContent content={question.question} className="text-[var(--color-text)]" />
            {question.options && question.options.length > 0 && (
              <ol className="mt-4 space-y-2">
                {question.options.map((option) => (
                  <li key={option.label} className="flex gap-2 text-sm">
                    <strong>{option.label}.</strong>
                    <QuestionContent content={option.content} />
                  </li>
                ))}
              </ol>
            )}
            {question.question_image_url && (
              <details className="mt-4">
                <summary className="cursor-pointer text-sm text-[var(--color-primary)]">Xem ảnh câu hỏi gốc</summary>
                <img src={question.question_image_url} alt={`Ảnh câu ${index + 1}`} className="mt-3 max-h-[70vh] max-w-full rounded-lg border border-[var(--color-border)] object-contain" />
              </details>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
