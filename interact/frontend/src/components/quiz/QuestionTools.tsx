import { useEffect, useState } from "react";
import { confirmRedAnswer, cropQuestionImage, detectRedMark } from "../../services/api";
import type { Question, RedMarkDetection } from "../../types/types";

interface QuestionToolsProps {
  question: Question;
}

const statusText: Record<RedMarkDetection["status"], string> = {
  detected: "Đã nhận diện dấu đỏ",
  uncertain: "Kết quả chưa chắc chắn, cần xác nhận",
  not_found: "Không tìm thấy dấu đỏ phù hợp",
};

export function QuestionTools({ question }: QuestionToolsProps) {
  const [isDetectionEnabled, setIsDetectionEnabled] = useState(false);
  const [isDetecting, setIsDetecting] = useState(false);
  const [detection, setDetection] = useState<RedMarkDetection | null>(question.red_mark_detection ?? null);
  const [selectedAnswer, setSelectedAnswer] = useState(
    question.user_confirmed_answer ?? question.red_mark_detection?.detected_answer ?? "",
  );
  const [isSavingAnswer, setIsSavingAnswer] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(question.question_image_url ?? null);
  const [isCropping, setIsCropping] = useState(false);
  const [isImageOpen, setIsImageOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetection(question.red_mark_detection ?? null);
    setSelectedAnswer(question.user_confirmed_answer ?? question.red_mark_detection?.detected_answer ?? "");
    setImageUrl(question.question_image_url ?? null);
  }, [question.question_record_id, question.red_mark_detection, question.question_image_url, question.user_confirmed_answer]);

  const canDetect = question.type === "mcp" && (question.options?.length ?? 0) > 0;
  const hasStoredPdf = question.has_source_pdf !== false && question.question_record_id !== undefined;

  const runDetection = async (force = false) => {
    if (!question.question_record_id) return;
    setIsDetecting(true);
    setError(null);
    try {
      const result = await detectRedMark(question.question_record_id, force);
      setDetection(result);
      setSelectedAnswer(result.user_confirmed_answer ?? result.detected_answer ?? "");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Không thể nhận diện dấu đỏ.");
    } finally {
      setIsDetecting(false);
    }
  };

  const handleDetectionToggle = (enabled: boolean) => {
    setIsDetectionEnabled(enabled);
    if (enabled) void runDetection();
  };

  const saveConfirmation = async () => {
    if (!question.question_record_id || !selectedAnswer) return;
    setIsSavingAnswer(true);
    setError(null);
    try {
      const saved = await confirmRedAnswer(question.question_record_id, selectedAnswer);
      setDetection(saved);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Không thể lưu đáp án xác nhận.");
    } finally {
      setIsSavingAnswer(false);
    }
  };

  const createCrop = async (force = false) => {
    if (!question.question_record_id) return;
    setIsCropping(true);
    setError(null);
    try {
      const result = await cropQuestionImage(question.question_record_id, force);
      setImageUrl(result.image_url);
      setIsImageOpen(true);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Không thể tạo ảnh câu hỏi.");
    } finally {
      setIsCropping(false);
    }
  };

  return (
    <section className="mt-6 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4" aria-label="Công cụ hỗ trợ câu hỏi">
      <div className="flex flex-wrap items-center gap-3">
        <label className={`inline-flex items-center gap-3 ${canDetect && hasStoredPdf ? "cursor-pointer" : "cursor-not-allowed opacity-60"}`}>
          <input
            type="checkbox"
            role="switch"
            checked={isDetectionEnabled}
            disabled={!canDetect || !hasStoredPdf || isDetecting}
            onChange={(event) => handleDetectionToggle(event.target.checked)}
            className="h-4 w-4 accent-[var(--color-primary)]"
          />
          <span className="text-sm font-medium text-[var(--color-text)]">Nhận diện đáp án được đánh dấu đỏ</span>
        </label>
        {isDetecting && <span className="text-sm text-[var(--color-text-secondary)]">Đang nhận diện...</span>}

        {imageUrl ? (
          <button
            type="button"
            onClick={() => setIsImageOpen(true)}
            className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
          >
            Xem ảnh câu hỏi
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void createCrop()}
            disabled={!hasStoredPdf || isCropping}
            className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isCropping ? "Đang tạo ảnh..." : "Tạo ảnh câu hỏi"}
          </button>
        )}
      </div>

      {!canDetect && (
        <p className="mt-2 text-xs text-[var(--color-text-muted)]">Nhận diện dấu đỏ hiện hỗ trợ câu hỏi trắc nghiệm có các lựa chọn.</p>
      )}
      {!hasStoredPdf && question.question_record_id && (
        <p className="mt-2 text-xs text-[var(--color-text-muted)]">Đề cũ không còn PDF gốc để tạo ảnh hoặc nhận diện dấu.</p>
      )}

      {isDetectionEnabled && detection && (
        <div className="mt-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <p className="text-sm font-medium text-[var(--color-text)]">
            {statusText[detection.status]} · trang {detection.page_number} · độ tin cậy {Math.round(detection.confidence * 100)}%
          </p>
          {detection.detected_answer && (
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              Đáp án được đánh dấu: <strong>{detection.detected_answer}</strong>
            </p>
          )}
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="text-sm text-[var(--color-text-secondary)]">
              Đáp án xác nhận
              <select
                value={selectedAnswer}
                onChange={(event) => setSelectedAnswer(event.target.value)}
                className="ml-2 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[var(--color-text)]"
              >
                <option value="">Chưa chọn</option>
                {question.options?.map((option) => (
                  <option key={option.label} value={option.label}>{option.label}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => void saveConfirmation()}
              disabled={!selectedAnswer || isSavingAnswer || selectedAnswer === detection.user_confirmed_answer}
              className="rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSavingAnswer ? "Đang lưu..." : detection.user_confirmed_answer ? "Đã xác nhận" : "Xác nhận đáp án"}
            </button>
            <button type="button" onClick={() => void runDetection(true)} disabled={isDetecting} className="text-sm text-[var(--color-primary)] disabled:opacity-50">
              Thử nhận diện lại
            </button>
          </div>
          <p className="mt-2 text-xs text-[var(--color-text-muted)]">Đáp án gốc không bị thay đổi. Đáp án đã xác nhận được lưu riêng và dùng khi chấm bài.</p>
        </div>
      )}

      {error && <p role="alert" className="mt-3 text-sm text-[var(--color-danger)]">{error}</p>}

      {isImageOpen && imageUrl && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Ảnh câu hỏi">
          <div className="relative max-h-[92vh] max-w-5xl overflow-auto rounded-xl bg-[var(--color-surface)] p-4">
            <div className="mb-3 flex justify-end gap-3">
              <button type="button" onClick={() => void createCrop(true)} disabled={isCropping} className="text-sm text-[var(--color-primary)]">
                {isCropping ? "Đang tạo lại..." : "Tạo lại ảnh"}
              </button>
              <button type="button" onClick={() => setIsImageOpen(false)} className="text-sm text-[var(--color-text)]">Đóng</button>
            </div>
            <img src={imageUrl} alt={`Ảnh crop câu ${question.question_id}`} className="mx-auto max-h-[80vh] max-w-full object-contain" />
          </div>
        </div>
      )}
    </section>
  );
}
