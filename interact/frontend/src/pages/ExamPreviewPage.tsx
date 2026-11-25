import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuiz } from "../contexts/QuizContext";
import {
  confirmRedAnswer,
  cropQuestionImage,
  detectRedMark,
  getExam,
} from "../services/api";
import { QuestionContent } from "../components/quiz/QuestionContent";
import type { Exam, Question } from "../types/types";

type ReviewFilter = "all" | "missing" | "answered";

type PendingAction = "crop" | "detect" | "confirm" | null;

const typeLabels: Record<Question["type"], string> = {
  mcp: "Trắc nghiệm",
  true_false_ng: "Đúng / Sai / Không có thông tin",
  matching: "Nối đáp án",
  fill_in: "Điền từ / Tự luận ngắn",
};

const secondaryButton =
    "rounded-md border border-[var(--color-border)] " +
    "px-3 py-2 text-sm font-medium " +
    "hover:bg-[var(--color-surface-hover)] " +
    "disabled:cursor-not-allowed disabled:opacity-50";

const primaryButton =
    "rounded-md bg-[var(--color-primary)] " +
    "px-4 py-2 text-sm font-medium text-white " +
    "hover:opacity-90 disabled:cursor-not-allowed " +
    "disabled:opacity-50";

function hasAnswer(question: Question): boolean {
  switch (question.type) {
    case "mcp": {
      const labels = new Set(
          question.options?.map((option) => option.label) ?? [],
      );

      if (
          !question.multiple_choice &&
          question.user_confirmed_answer &&
          labels.has(question.user_confirmed_answer)
      ) {
        return true;
      }

      const answers = question.option_answer ?? [];

      return (
          answers.length > 0 &&
          answers.every((answer) => labels.has(answer)) &&
          (question.multiple_choice || answers.length === 1)
      );
    }

    case "true_false_ng":
      return (
          question.option_answer?.length === 1 &&
          ["true", "false", "ng"].includes(
              question.option_answer[0],
          )
      );

    case "fill_in":
      return (
          question.fill_in_answers?.some(
              (answer) => answer.trim().length > 0,
          ) ?? false
      );

    case "matching":
      return (
          question.matches?.some(
              (match) =>
                  match.description.trim().length > 0 &&
                  match.answer.trim().length > 0,
          ) ?? false
      );

    default:
      return false;
  }
}

function errorMessage(
    error: unknown,
    fallback: string,
): string {
  return error instanceof Error
      ? error.message
      : fallback;
}

interface ReviewQuestionProps {
  question: Question;
  index: number;
  onUpdate: (changes: Partial<Question>) => void;
}

function ReviewQuestion({
                          question,
                          index,
                          onUpdate,
                        }: ReviewQuestionProps) {
  const [pending, setPending] =
      useState<PendingAction>(null);

  const [error, setError] =
      useState<string | null>(null);

  const [imageOpen, setImageOpen] =
      useState(false);

  const [selectedAnswer, setSelectedAnswer] =
      useState(
          question.user_confirmed_answer ??
          question.option_answer?.[0] ??
          "",
      );

  const recordId = question.question_record_id;
  const options = question.options ?? [];
  const detection = question.red_mark_detection;

  const sourceAvailable =
      question.has_source_pdf === true &&
      Boolean(recordId);

  const supportsConfirmation =
      question.type === "mcp" &&
      !question.multiple_choice &&
      options.length >= 2 &&
      Boolean(recordId);

  const answered = hasAnswer(question);

  const createImage = async () => {
    if (!recordId || pending) return;

    setPending("crop");
    setError(null);

    try {
      const result = await cropQuestionImage(recordId);

      if (!result.image_url) {
        throw new Error(
            "Backend không trả về ảnh câu hỏi.",
        );
      }

      onUpdate({
        question_image_url: result.image_url,
        crop_status: result.crop_status,
      });

      setImageOpen(true);
    } catch (requestError) {
      setError(
          errorMessage(
              requestError,
              "Không thể tạo ảnh câu hỏi.",
          ),
      );
    } finally {
      setPending(null);
    }
  };

  const runDetection = async () => {
    if (!recordId || pending) return;

    setPending("detect");
    setError(null);

    try {
      const result = await detectRedMark(recordId);

      onUpdate({
        red_mark_detection: result,
      });

      // A detected mark is evidence, not a confirmed answer.
      // Do not update user_confirmed_answer automatically.
      if (result.user_confirmed_answer) {
        setSelectedAnswer(
            result.user_confirmed_answer,
        );
      }
    } catch (requestError) {
      setError(
          errorMessage(
              requestError,
              "Không thể nhận diện dấu đỏ.",
          ),
      );
    } finally {
      setPending(null);
    }
  };

  const saveAnswer = async () => {
    if (
        !recordId ||
        !selectedAnswer ||
        !supportsConfirmation ||
        !detection ||
        pending
    ) {
      return;
    }

    const valid = options.some(
        (option) => option.label === selectedAnswer,
    );

    if (!valid) {
      setError(
          "Đáp án không tồn tại trong câu hỏi.",
      );

      return;
    }

    setPending("confirm");
    setError(null);

    try {
      const result = await confirmRedAnswer(
          recordId,
          selectedAnswer,
      );

      onUpdate({
        red_mark_detection: result,
        user_confirmed_answer:
            result.user_confirmed_answer ?? null,
      });
    } catch (requestError) {
      setError(
          errorMessage(
              requestError,
              "Không thể lưu đáp án.",
          ),
      );
    } finally {
      setPending(null);
    }
  };

  const clearConfirmation = async () => {
    if (!recordId || pending || !detection) return;

    setPending("confirm");
    setError(null);

    try {
      const result = await confirmRedAnswer(
          recordId,
          null,
      );

      onUpdate({
        red_mark_detection: result,
        user_confirmed_answer: null,
      });

      setSelectedAnswer(
          question.option_answer?.[0] ?? "",
      );
    } catch (requestError) {
      setError(
          errorMessage(
              requestError,
              "Không thể hủy xác nhận.",
          ),
      );
    } finally {
      setPending(null);
    }
  };

  return (
      <article className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h2 className="font-semibold">
            Câu {index + 1}
          </h2>

          <span className="text-xs text-[var(--color-text-secondary)]">
          {typeLabels[question.type]}
        </span>

          {question.page_number != null && (
              <span className="text-xs text-[var(--color-text-secondary)]">
            Trang {question.page_number}
          </span>
          )}

          <span
              className={
                  "ml-auto rounded-md border border-[var(--color-border)] " +
                  "px-2 py-1 text-xs " +
                  (answered
                      ? "text-[var(--color-primary)]"
                      : "text-[var(--color-text-secondary)]")
              }
          >
          {answered
              ? "Có đáp án"
              : "Chưa có đáp án"}
        </span>
        </div>

        <QuestionContent
            content={question.question}
            className="text-[var(--color-text)]"
        />

        {options.length > 0 && (
            <div className="mt-4 space-y-2">
              {options.map((option) => {
                const confirmed =
                    question.user_confirmed_answer ===
                    option.label;

                return (
                    <div
                        key={option.label}
                        className={
                            "flex items-start gap-3 rounded-lg " +
                            "border p-3 " +
                            (confirmed
                                ? "border-[var(--color-primary)]"
                                : "border-[var(--color-border)]")
                        }
                    >
                      {supportsConfirmation && (
                          <input
                              type="radio"
                              name={`answer-${recordId}`}
                              aria-label={`Phương án ${option.label}`}
                              checked={
                                  selectedAnswer === option.label
                              }
                              disabled={pending !== null}
                              onChange={() =>
                                  setSelectedAnswer(option.label)
                              }
                              className="mt-1 accent-[var(--color-primary)]"
                          />
                      )}

                      <strong className="text-sm">
                        {option.label}.
                      </strong>

                      <div className="min-w-0 flex-1 text-sm">
                        <QuestionContent
                            content={option.content}
                        />
                      </div>

                      {confirmed && (
                          <span className="shrink-0 text-xs font-medium text-[var(--color-primary)]">
                    Đã xác nhận
                  </span>
                      )}
                    </div>
                );
              })}
            </div>
        )}

        {question.type === "fill_in" &&
            question.fill_in_answers &&
            question.fill_in_answers.length > 0 && (
                <div className="mt-4 text-sm">
            <span className="font-medium">
              Đáp án:
            </span>

                  <p className="mt-1 text-[var(--color-text-secondary)]">
                    {question.fill_in_answers.join(" / ")}
                  </p>
                </div>
            )}

        {question.type === "true_false_ng" &&
            question.option_answer?.length === 1 && (
                <div className="mt-4 text-sm">
                  Đáp án: {question.option_answer[0]}
                </div>
            )}

        {question.type === "matching" &&
            question.matches &&
            question.matches.length > 0 && (
                <div className="mt-4 space-y-2">
                  {question.matches.map((match, matchIndex) => (
                      <div
                          key={matchIndex}
                          className="grid grid-cols-2 gap-3 rounded-md border border-[var(--color-border)] p-3 text-sm"
                      >
                        <QuestionContent
                            content={match.description}
                        />
                        <QuestionContent
                            content={match.answer}
                        />
                      </div>
                  ))}
                </div>
            )}

        <div className="mt-5 border-t border-[var(--color-border)] pt-4">
          <div className="flex flex-wrap items-center gap-2">
            {question.question_image_url ? (
                <button
                    type="button"
                    className={secondaryButton}
                    onClick={() =>
                        setImageOpen((current) => !current)
                    }
                >
                  {imageOpen
                      ? "Ẩn ảnh PDF"
                      : "Đối chiếu PDF"}
                </button>
            ) : (
                <button
                    type="button"
                    className={secondaryButton}
                    disabled={
                        !sourceAvailable || pending !== null
                    }
                    onClick={() => void createImage()}
                >
                  {pending === "crop"
                      ? "Đang tạo ảnh..."
                      : "Tạo ảnh câu hỏi"}
                </button>
            )}

            {supportsConfirmation && (
                <button
                    type="button"
                    className={secondaryButton}
                    disabled={
                        !sourceAvailable || pending !== null
                    }
                    onClick={() => void runDetection()}
                >
                  {pending === "detect"
                      ? "Đang nhận diện..."
                      : "Nhận diện dấu đỏ"}
                </button>
            )}

            {supportsConfirmation && (
                <button
                    type="button"
                    className={primaryButton}
                    disabled={
                        !detection ||
                        !selectedAnswer ||
                        pending !== null ||
                        selectedAnswer ===
                        question.user_confirmed_answer
                    }
                    onClick={() => void saveAnswer()}
                >
                  {pending === "confirm"
                      ? "Đang lưu..."
                      : "Xác nhận đáp án"}
                </button>
            )}

            {supportsConfirmation &&
                question.user_confirmed_answer && (
                    <button
                        type="button"
                        className={secondaryButton}
                        disabled={pending !== null}
                        onClick={() =>
                            void clearConfirmation()
                        }
                    >
                      Hủy xác nhận
                    </button>
                )}
          </div>

          {supportsConfirmation && !detection && (
              <p className="mt-3 text-xs text-[var(--color-text-secondary)]">
                Chạy nhận diện dấu đỏ trước khi xác nhận
                đáp án. Nếu không tìm thấy dấu đỏ, bạn
                vẫn có thể chọn đáp án sau khi đối chiếu.
              </p>
          )}

          {question.multiple_choice &&
              question.type === "mcp" && (
                  <p className="mt-3 text-xs text-[var(--color-text-secondary)]">
                    Xác nhận nhiều đáp án chưa được hỗ trợ
                    bởi API hiện tại.
                  </p>
              )}

          {!sourceAvailable &&
              !question.question_image_url && (
                  <p className="mt-3 text-xs text-[var(--color-text-secondary)]">
                    Không có PDF nguồn khả dụng để đối chiếu.
                  </p>
              )}

          {detection && (
              <div className="mt-3 rounded-lg border border-[var(--color-border)] p-3 text-sm">
                <p className="font-medium">
                  {detection.status === "detected"
                      ? "Đã nhận diện dấu đỏ"
                      : detection.status === "uncertain"
                          ? "Kết quả nhận diện chưa chắc chắn"
                          : "Không tìm thấy dấu đỏ phù hợp"}
                </p>

                {detection.detected_answer && (
                    <p className="mt-1">
                      Phương án được đánh dấu:{" "}
                      <strong>
                        {detection.detected_answer}
                      </strong>
                    </p>
                )}

                <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                  Độ tin cậy:{" "}
                  {Math.round(detection.confidence * 100)}%
                  . Kết quả chỉ mang tính tham khảo.
                </p>
              </div>
          )}

          {error && (
              <p
                  role="alert"
                  className="mt-3 text-sm text-[var(--color-danger)]"
              >
                {error}
              </p>
          )}

          {imageOpen &&
              question.question_image_url && (
                  <div className="mt-4 rounded-lg border border-[var(--color-border)] p-3">
                    <img
                        src={question.question_image_url}
                        alt={`Ảnh PDF nguồn của câu ${index + 1}`}
                        loading="lazy"
                        className="mx-auto max-h-[75vh] max-w-full object-contain"
                    />
                  </div>
              )}
        </div>
      </article>
  );
}

function ExamReview({
                      examId,
                    }: {
  examId: string;
}) {
  const [exam, setExam] =
      useState<Exam | null>(null);

  const [error, setError] =
      useState<string | null>(null);

  const [filter, setFilter] =
      useState<ReviewFilter>("all");

  const navigate = useNavigate();
  const { resetQuiz } = useQuiz();

  useEffect(() => {
    let cancelled = false;

    getExam(examId)
        .then((result) => {
          if (!cancelled) {
            setExam(result);
          }
        })
        .catch((requestError: unknown) => {
          if (!cancelled) {
            setError(
                errorMessage(
                    requestError,
                    "Không thể tải đề thi.",
                ),
            );
          }
        });

    return () => {
      cancelled = true;
    };
  }, [examId]);

  const updateQuestion = (
      index: number,
      changes: Partial<Question>,
  ) => {
    setExam((current) => {
      if (!current) return current;

      return {
        ...current,
        questions: current.questions.map(
            (question, questionIndex) =>
                questionIndex === index
                    ? { ...question, ...changes }
                    : question,
        ),
      };
    });
  };

  if (error) {
    return (
        <div className="mx-auto max-w-4xl p-6">
          <Link
              to="/history"
              className="text-sm text-[var(--color-primary)]"
          >
            Quay lại lịch sử PDF
          </Link>

          <p
              role="alert"
              className="mt-4 text-[var(--color-danger)]"
          >
            {error}
          </p>
        </div>
    );
  }

  if (!exam) {
    return (
        <div
            role="status"
            className="mx-auto max-w-4xl p-6"
        >
          Đang tải câu hỏi...
        </div>
    );
  }

  const total = exam.questions.length;

  const answered = exam.questions.filter(
      hasAnswer,
  ).length;

  const missing = total - answered;

  const filtered = exam.questions
      .map((question, index) => ({
        question,
        index,
      }))
      .filter(({ question }) => {
        if (filter === "missing") {
          return !hasAnswer(question);
        }

        if (filter === "answered") {
          return hasAnswer(question);
        }

        return true;
      });

  const progress =
      total > 0 ? (answered / total) * 100 : 0;

  const startQuiz = () => {
    if (total === 0) return;

    if (
        missing > 0 &&
        !window.confirm(
            `Còn ${missing} câu chưa có đáp án. ` +
            "Kết quả tự chấm có thể không phản ánh " +
            "chính xác năng lực của bạn. " +
            "Bạn vẫn muốn làm bài?",
        )
    ) {
      return;
    }

    resetQuiz();
    navigate(`/quiz/${exam.id}`);
  };

  const filters: {
    value: ReviewFilter;
    label: string;
  }[] = [
    {
      value: "all",
      label: `Tất cả (${total})`,
    },
    {
      value: "missing",
      label: `Chưa có đáp án (${missing})`,
    },
    {
      value: "answered",
      label: `Có đáp án (${answered})`,
    },
  ];

  return (
      <div className="mx-auto max-w-5xl px-6 py-10 text-[var(--color-text)]">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
          <div>
            <Link
                to="/history"
                className="text-sm text-[var(--color-primary)]"
            >
              ← Lịch sử PDF
            </Link>

            <h1 className="mt-2 text-3xl font-semibold">
              {exam.title}
            </h1>

            <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
              Rà soát nội dung và xác nhận đáp án
              trước khi làm bài.
            </p>

            {exam.source_file && (
                <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                  {exam.source_file}
                </p>
            )}
          </div>

          <button
              type="button"
              className={primaryButton}
              disabled={total === 0}
              onClick={startQuiz}
          >
            Bắt đầu làm bài
          </button>
        </div>

        <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {[
            {
              label: "Tổng số câu hỏi",
              value: total,
            },
            {
              label: "Có đáp án",
              value: answered,
            },
            {
              label: "Chưa có đáp án",
              value: missing,
            },
          ].map((metric) => (
              <div
                  key={metric.label}
                  className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
              >
                <p className="text-sm text-[var(--color-text-secondary)]">
                  {metric.label}
                </p>

                <p className="mt-2 text-3xl font-semibold">
                  {metric.value}
                </p>
              </div>
          ))}
        </div>

        <div className="mb-6 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <div className="mb-2 flex justify-between text-sm">
            <span>Mức độ đầy đủ của đáp án</span>
            <span>
            {answered}/{total}
          </span>
          </div>

          <div
              role="progressbar"
              aria-label="Mức độ đầy đủ của đáp án"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress)}
              className="h-2 overflow-hidden rounded-full bg-[var(--color-bg-secondary)]"
          >
            <div
                className="h-full rounded-full bg-[var(--color-primary)] transition-all"
                style={{
                  width: `${progress}%`,
                }}
            />
          </div>

          <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
            Có đáp án không đồng nghĩa với việc nội dung
            OCR đã được xác minh.
          </p>
        </div>

        <div className="mb-5 flex flex-wrap gap-2">
          {filters.map((item) => (
              <button
                  key={item.value}
                  type="button"
                  aria-pressed={filter === item.value}
                  onClick={() => setFilter(item.value)}
                  className={
                      "rounded-md border px-3 py-2 text-sm " +
                      (filter === item.value
                          ? "border-[var(--color-primary)] text-[var(--color-primary)]"
                          : "border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]")
                  }
              >
                {item.label}
              </button>
          ))}
        </div>

        {filtered.length === 0 ? (
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center text-sm text-[var(--color-text-secondary)]">
              Không có câu hỏi phù hợp với bộ lọc.
            </div>
        ) : (
            <div className="space-y-5">
              {filtered.map(({ question, index }) => (
                  <ReviewQuestion
                      key={
                          question.question_record_id ??
                          `${question.question_id}-${index}`
                      }
                      question={question}
                      index={index}
                      onUpdate={(changes) =>
                          updateQuestion(index, changes)
                      }
                  />
              ))}
            </div>
        )}
      </div>
  );
}

export function ExamPreviewPage() {
  const { examId } = useParams<{
    examId: string;
  }>();

  if (!examId) {
    return (
        <div
            role="alert"
            className="mx-auto max-w-4xl p-6 text-[var(--color-danger)]"
        >
          Không tìm thấy mã đề thi.
        </div>
    );
  }

  return (
      <ExamReview
          key={examId}
          examId={examId}
      />
  );
}