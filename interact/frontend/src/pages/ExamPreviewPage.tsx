import {
  useEffect,
  useState,
} from "react";

import {
  Link,
  useNavigate,
  useParams,
} from "react-router-dom";

import {
  useQuiz,
} from "../contexts/QuizContext";

import {
  getExam,
} from "../services/api";

import {
  QuestionContent,
} from "../components/quiz/QuestionContent";

import {
  getParserReview,
  type ParserReview,
} from "../services/parserReview";

import type {
  Exam,
} from "../types/types";


const ISSUE_LABELS: Record<string, string> = {
  choice_count_too_low:
      "Quá ít phương án",

  choice_labels_invalid:
      "Nhãn phương án không hợp lệ",

  choice_content_empty:
      "Phương án thiếu nội dung",

  expected_four_choices:
      "Không đủ phương án A–D theo chỉ dẫn",

  existing_review_flag:
      "Parser yêu cầu đối chiếu",
};


const UNAVAILABLE_LABELS = {
  missing:
      "Đề này chưa có báo cáo rà soát khi import.",

  invalid:
      "Báo cáo rà soát không hợp lệ.",

  unsupported:
      "Phiên bản báo cáo chưa được hỗ trợ.",

  stale:
      "Báo cáo không còn khớp danh sách câu hỏi.",
};


type ReviewState =
    | {
  status: "loading";
}

    | {
  status: "error";

  message: string;
}

    | {
  status: "loaded";

  report: ParserReview;
};


export function ExamPreviewPage() {
  const { examId } = useParams<{
    examId: string;
  }>();

  if (!examId) {
    return (
        <div
            role="alert"
            className="p-6"
        >
          Không tìm thấy mã đề thi.
        </div>
    );
  }

  return (
      <ExamPreview
          key={examId}
          examId={examId}
      />
  );
}


function ExamPreview({
                       examId,
                     }: {
  examId: string;
}) {

  const [exam, setExam] =
      useState<Exam | null>(null);

  const [error, setError] =
      useState<string | null>(null);

  const [review, setReview] =
      useState<ReviewState>({
        status: "loading",
      });

  const [filter, setFilter] =
      useState<"all" | "review">(
          "all",
      );

  const [retry, setRetry] =
      useState(0);

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

        .catch((cause: unknown) => {
          if (!cancelled) {
            setError(
                cause instanceof Error

                    ? cause.message

                    : "Không thể tải đề.",
            );
          }
        });

    return () => {
      cancelled = true;
    };

  }, [examId]);


  useEffect(() => {
    const controller =
        new AbortController();

    getParserReview(
        examId,

        controller.signal,
    )

        .then((report) => {
          if (
              !controller.signal.aborted
          ) {
            setReview({
              status: "loaded",

              report,
            });
          }
        })

        .catch((cause: unknown) => {
          if (
              !controller.signal.aborted
          ) {
            setReview({
              status: "error",

              message:
                  cause instanceof Error

                      ? cause.message

                      : "Không thể tải báo cáo.",
            });
          }
        });

    return () => {
      controller.abort();
    };

  }, [examId, retry]);


  if (error) {
    return (
        <div
            role="alert"
            className="mx-auto max-w-4xl p-6 text-[var(--color-danger)]"
        >
          {error}
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


  const report =
      review.status === "loaded"

          ? review.report

          : null;


  const records = new Set(
      exam.questions.map(
          (question) =>
              question.question_record_id,
      ),
  );


  const available =
      report?.status === "available"

      && report.review_summary.total_questions
      === exam.questions.length

      && report.review_items.every(
          (item) =>
              records.has(
                  item.question_record_id,
              ),
      )

          ? report

          : null;


  const reviewById = new Map(
      available?.review_items.map(
          (item) => [
            item.question_record_id,

            item,
          ],
      ),
  );


  const visible = exam.questions

      .map(
          (question, index) => ({
            question,

            index,
          }),
      )

      .filter(
          ({ question }) =>
              !available

              || filter === "all"

              || reviewById.has(
                  question.question_record_id
                  || "",
              ),
      );


  function retryReview() {
    setReview({
      status: "loading",
    });

    setFilter("all");

    setRetry(
        (value) => value + 1,
    );
  }


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

            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              {exam.question_count} câu hỏi đã trích xuất
            </p>

          </div>

          <button
              onClick={() => {
                resetQuiz();

                navigate(
                    `/quiz/${exam.id}`,
                );
              }}

              className="rounded-md bg-[var(--color-primary)] px-5 py-2.5 font-medium text-white"
          >
            Làm bài
          </button>

        </div>


        <section
            className="mb-6 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5"

            aria-label="Báo cáo rà soát parser"
        >

          <h2 className="font-semibold">
            Rà soát đề thi
          </h2>

          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Cảnh báo được ghi nhận khi import PDF,
            không phải xác nhận độ chính xác của đáp án.
          </p>


          {review.status === "loading" && (
              <p
                  role="status"

                  className="mt-3 text-sm"
              >
                Đang tải báo cáo...
              </p>
          )}


          {review.status === "error" && (
              <div
                  role="alert"

                  className="mt-3 text-sm"
              >

                <p>
                  {review.message} Bạn vẫn có thể xem câu hỏi.
                </p>

                <button
                    onClick={retryReview}

                    className="mt-2 text-[var(--color-primary)] underline"
                >
                  Thử lại
                </button>

              </div>
          )}


          {report?.status === "unavailable" && (
              <p className="mt-3 text-sm">

                {
                    UNAVAILABLE_LABELS[
                        report.reason
                        ]

                    ?? "Chưa có dữ liệu rà soát."
                }

                {" "}

                Không thể kết luận đề không có lỗi.

              </p>
          )}


          {report?.status === "available"
              && !available

              && (
                  <p
                      role="alert"

                      className="mt-3 text-sm"
                  >
                    Báo cáo không khớp danh sách câu hỏi hiện tại.
                  </p>
              )
          }


          {available && (
              <>

                <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">

                  {[
                    [
                      "Tổng số câu",

                      available.review_summary
                          .total_questions,
                    ],

                    [
                      "Không có cảnh báo",

                      available.review_summary
                          .total_questions

                      - available.review_summary
                          .needs_review,
                    ],

                    [
                      "Cần rà soát",

                      available.review_summary
                          .needs_review,
                    ],

                  ].map(([label, count]) => (

                      <div
                          key={label}

                          className="rounded-lg border border-[var(--color-border)] p-3"
                      >

                        <p className="text-sm text-[var(--color-text-secondary)]">
                          {label}
                        </p>

                        <p className="mt-1 text-2xl font-semibold">
                          {count}
                        </p>

                      </div>

                  ))}

                </div>


                <div
                    className="mt-4 flex flex-wrap gap-2"

                    aria-label="Lọc câu hỏi"
                >

                  {(
                      [
                        "all",

                        "review",
                      ] as const

                  ).map((value) => (

                      <button
                          key={value}

                          aria-pressed={
                              filter === value
                          }

                          onClick={() =>
                              setFilter(value)
                          }

                          className={
                            `rounded-md border border-[var(--color-border)] px-3 py-2 text-sm ${

                                filter === value

                                    ? "bg-[var(--color-primary)] text-white"

                                    : ""
                            }`
                          }
                      >

                        {
                          value === "all"

                              ? `Tất cả (${exam.questions.length})`

                              : `Cần rà soát (${available.review_summary.needs_review})`
                        }

                      </button>

                  ))}

                </div>

              </>
          )}

        </section>


        <div className="space-y-5">

          {visible.length === 0 && (

              <p className="rounded-lg border border-[var(--color-border)] p-4 text-sm">

                Không có câu hỏi trong bộ lọc này.

              </p>

          )}


          {visible.map(({
                          question,

                          index,
                        }) => {

            const item = reviewById.get(
                question.question_record_id
                || "",
            );

            return (

                <article
                    key={
                        question.question_record_id

                        || question.question_id
                    }

                    className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5"
                >

                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">

                    <h2 className="font-semibold">
                      Câu {index + 1}
                    </h2>

                    {item && (

                        <span className="rounded-md border border-amber-500 px-2 py-1 text-xs font-medium">

                                        Cần rà soát

                                    </span>

                    )}

                  </div>


                  {item && (

                      <div className="mb-4 rounded-md border border-amber-500 p-3 text-sm">

                        {item.warning && (

                            <p className="mb-2">

                              {item.warning}

                            </p>

                        )}


                        <ul className="list-disc space-y-1 pl-5">

                          {item.issue_codes.map(
                              (code, i) => (

                                  <li key={`${code}-${i}`}>

                                    {
                                        ISSUE_LABELS[code]

                                        ?? code
                                    }

                                  </li>

                              ),
                          )}

                        </ul>


                        {item.requires_source_image

                            && !question.question_image_url

                            && (

                                <p className="mt-2">

                                  Chưa có ảnh cắt để đối chiếu.
                                  Hãy kiểm tra PDF gốc.

                                </p>

                            )
                        }

                      </div>

                  )}


                  <QuestionContent
                      content={
                        question.question
                      }

                      className="text-[var(--color-text)]"
                  />


                  {question.options

                      && question.options.length > 0

                      && (

                          <ol className="mt-4 space-y-2">

                            {question.options.map(
                                (option, i) => (

                                    <li
                                        key={`${option.label}-${i}`}

                                        className="flex gap-2 text-sm"
                                    >

                                      <strong>
                                        {option.label}.
                                      </strong>

                                      <QuestionContent
                                          content={
                                            option.content
                                          }
                                      />

                                    </li>

                                ),
                            )}

                          </ol>

                      )
                  }


                  {question.question_image_url && (

                      <details
                          className="mt-4"

                          open={
                              item?.requires_source_image
                              || undefined
                          }
                      >

                        <summary className="cursor-pointer text-sm text-[var(--color-primary)]">

                          Xem ảnh câu hỏi gốc

                        </summary>

                        <img
                            src={
                              question.question_image_url
                            }

                            alt={`Ảnh câu ${index + 1}`}

                            className="mt-3 max-h-[70vh] max-w-full rounded-lg border border-[var(--color-border)] object-contain"
                        />

                      </details>

                  )}

                </article>

            );
          })}

        </div>

      </div>
  );
}