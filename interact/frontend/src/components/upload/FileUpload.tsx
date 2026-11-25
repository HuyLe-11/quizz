import {
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  ChangeEvent,
  DragEvent,
} from "react";

import {
  Link,
  useNavigate,
} from "react-router-dom";

import { useAuth } from "../../contexts/AuthContext";

import {
  createUploadJob,
  getUploadJob,
} from "../../services/api";

import {
  clearUploadTracking,
  followUploadJob,
  isTerminalUploadJob,
  readUploadTracking,
  saveUploadTracking,
  validateUploadJob,
} from "../../services/uploadTracking";

import type {
  UploadTracking,
} from "../../services/uploadTracking";

import type {
  UploadJob,
  UploadJobPhase,
} from "../../types/types";


const MAX_FILE_SIZE = 30 * 1024 * 1024;


const STEPS = [
  {
    phase: "uploading",
    label: "Tiếp nhận tệp PDF",
  },
  {
    phase: "ocr",
    label: "Nhận diện bằng PaddleOCR",
  },
  {
    phase: "question_extraction",
    label: "Phân tích câu hỏi",
  },
  {
    phase: "source_pdf_save",
    label: "Lưu PDF gốc",
  },
  {
    phase: "question_crops",
    label: "Xử lý ảnh câu hỏi",
  },
  {
    phase: "sqlite_save",
    label: "Lưu đề thi",
  },
] as const;


const PHASE_LABELS: Record<UploadJobPhase, string> = {
  queued: "Đang chờ đến lượt xử lý",
  starting: "Đang khởi tạo tác vụ",
  ocr: "Đang nhận diện nội dung bằng PaddleOCR",
  question_extraction: "Đang phân tích câu hỏi",
  source_pdf_save: "Đang lưu PDF gốc",
  question_crops: "Đang xử lý ảnh câu hỏi",
  sqlite_save: "Đang lưu đề thi",
  cloud_save: "Đang lưu đề thi",
  completed: "Đã hoàn thành",
};


const PRIMARY =
    "w-full rounded-md bg-[var(--color-primary)] px-4 py-2.5 font-medium text-white disabled:opacity-60";

const SECONDARY =
    "rounded-md border border-[var(--color-border)] px-3 py-2 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]";


type PollState =
    | {
  kind: "checking";
}
    | {
  kind: "retrying";
  count: number;
  delayMs: number;
}
    | {
  kind: "paused";
  message: string;
}
    | {
  kind: "done";
};


function initialTracking(ownerId: string) {
  try {
    return {
      record: readUploadTracking(
          window.sessionStorage,
          ownerId,
      ),
      blocked: false,
    };
  } catch {
    return {
      record: null,
      blocked: true,
    };
  }
}


function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }

  if (bytes < 1024 ** 2) {
    return `${(bytes / 1024).toFixed(2)} KB`;
  }

  return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
}


function formatElapsed(seconds: number): string {
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}


function completedSteps(
    job: UploadJob | null,
): number {
  if (!job) return 0;

  if (job.status === "completed") {
    return STEPS.length;
  }

  // Both persistence backends share the same UI stage.
  const phase =
      job.phase === "cloud_save"
          ? "sqlite_save"
          : job.phase;

  return Math.max(
      1,
      STEPS.findIndex(
          step => step.phase === phase,
      ),
  );
}


export function FileUpload() {
  const { user } = useAuth();

  return user
      ? (
          <UploadPanel
              key={user.id}
              ownerId={user.id}
          />
      )
      : null;
}


function UploadPanel({
                       ownerId,
                     }: {
  ownerId: string;
}) {
  const [initial] = useState(
      () => initialTracking(ownerId),
  );

  const [record, setRecord] =
      useState<UploadTracking | null>(
          initial.record,
      );

  const [storageBlocked, setStorageBlocked] =
      useState(initial.blocked);

  const [storageWarning, setStorageWarning] =
      useState<string | null>(null);

  const [file, setFile] =
      useState<File | null>(null);

  const [model, setModel] =
      useState(
          initial.record?.model ??
          "PaddleOCR-VL-1.6",
      );

  const [dragging, setDragging] =
      useState(false);

  const [submitting, setSubmitting] =
      useState(false);

  const [job, setJob] =
      useState<UploadJob | null>(null);

  const [poll, setPoll] =
      useState<PollState>({
        kind: "checking",
      });

  const [revision, setRevision] =
      useState(0);

  const [error, setError] =
      useState<string | null>(null);

  const [elapsed, setElapsed] =
      useState(0);

  const inputRef =
      useRef<HTMLInputElement>(null);

  const submitRef =
      useRef<AbortController | null>(null);

  const navigate = useNavigate();

  const jobId =
      record?.jobId ?? null;

  const startedAt =
      record?.startedAt ?? null;

  const terminal =
      isTerminalUploadJob(job);

  const following =
      jobId !== null &&
      !terminal &&
      poll.kind !== "paused";

  const busy =
      submitting || following;

  const completed =
      completedSteps(job);


  useEffect(
      () => () => submitRef.current?.abort(),
      [],
  );


  // Reconnect to the saved job instead of submitting again.
  useEffect(() => {
    if (!jobId) return;

    const controller =
        new AbortController();

    const signal =
        controller.signal;

    void followUploadJob({
      jobId,
      signal,
      getJob: getUploadJob,

      onUpdate: (updated) => {
        if (signal.aborted) return;

        setJob(updated);

        setPoll({
          kind:
              isTerminalUploadJob(updated)
                  ? "done"
                  : "checking",
        });
      },

      onRetry: (count, delayMs) => {
        if (!signal.aborted) {
          setPoll({
            kind: "retrying",
            count,
            delayMs,
          });
        }
      },
    }).catch((cause: unknown) => {
      if (signal.aborted) return;

      setPoll({
        kind: "paused",

        message:
            cause instanceof Error
                ? cause.message
                : "Không thể đọc trạng thái tác vụ.",
      });
    });

    return () => controller.abort();

  }, [jobId, revision]);


  useEffect(() => {
    if (
        startedAt === null ||
        terminal
    ) {
      return;
    }

    const timer = window.setInterval(() => {
      setElapsed(
          Math.max(
              0,
              Math.floor(
                  (Date.now() - startedAt) / 1000,
              ),
          ),
      );
    }, 1000);

    return () =>
        window.clearInterval(timer);

  }, [startedAt, terminal]);


  function chooseFile(
      selected?: File,
  ) {
    if (
        !selected ||
        record ||
        storageBlocked ||
        submitRef.current
    ) {
      return;
    }

    if (
        selected.type !== "application/pdf" &&
        !selected.name.toLowerCase().endsWith(".pdf")
    ) {
      setError(
          "Vui lòng chọn tệp PDF.",
      );
      return;
    }

    if (
        !selected.size ||
        selected.size > MAX_FILE_SIZE
    ) {
      setError(
          "PDF phải có nội dung và không vượt quá 30 MB.",
      );
      return;
    }

    setFile(selected);
    setError(null);
  }


  function handleDrop(
      event: DragEvent<HTMLDivElement>,
  ) {
    event.preventDefault();
    setDragging(false);

    chooseFile(
        event.dataTransfer.files[0],
    );
  }


  function handleFileChange(
      event: ChangeEvent<HTMLInputElement>,
  ) {
    chooseFile(
        event.target.files?.[0],
    );

    event.target.value = "";
  }


  async function handleUpload() {
    if (
        !file ||
        record ||
        storageBlocked ||
        submitRef.current
    ) {
      return;
    }

    const pending: UploadTracking = {
      jobId: null,
      fileName: file.name,
      fileSize: file.size,
      model,
      startedAt: Date.now(),
    };

    // Persist before POST to prevent silent resubmission.
    try {
      saveUploadTracking(
          window.sessionStorage,
          ownerId,
          pending,
      );
    } catch {
      setStorageWarning(
          "Không thể lưu phiên theo dõi. " +
          "Chưa gửi PDF; hãy kiểm tra cài đặt " +
          "lưu trữ của trình duyệt.",
      );
      return;
    }

    const controller =
        new AbortController();

    submitRef.current = controller;

    setRecord(pending);
    setSubmitting(true);
    setElapsed(0);
    setError(null);
    setStorageWarning(null);
    setJob(null);

    setPoll({
      kind: "checking",
    });

    try {
      const created =
          await createUploadJob(
              file,
              model,
              controller.signal,
          );

      controller.signal.throwIfAborted();

      validateUploadJob(created);

      const tracked = {
        ...pending,
        jobId: created.job_id,
      };

      try {
        saveUploadTracking(
            window.sessionStorage,
            ownerId,
            tracked,
        );
      } catch {
        setStorageWarning(
            "Đã nhận Job ID nhưng không lưu được " +
            "vào trình duyệt. Không nên refresh " +
            "trước khi hoàn thành.",
        );
      }

      setRecord(tracked);
      setJob(created);
      setFile(null);

    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(
            cause instanceof Error
                ? cause.message
                : "Không nhận được mã tác vụ.",
        );
      }

    } finally {
      if (
          submitRef.current === controller
      ) {
        submitRef.current = null;
      }

      if (!controller.signal.aborted) {
        setSubmitting(false);
      }
    }
  }


  function resume() {
    if (
        !jobId ||
        busy ||
        terminal
    ) {
      return;
    }

    setPoll({
      kind: "checking",
    });

    setRevision(
        value => value + 1,
    );
  }


  function resetTracking(
      confirmUnknown = true,
  ): boolean {
    if (
        busy ||
        submitRef.current
    ) {
      return false;
    }

    if (
        confirmUnknown &&
        !terminal &&
        (record || storageBlocked)
    ) {
      if (
          !window.confirm(
              "Bạn đã kiểm tra Lịch sử PDF chưa? " +
              "Xóa theo dõi không hủy tác vụ backend. " +
              "Tải lại PDF có thể tạo tác vụ trùng.",
          )
      ) {
        return false;
      }
    }

    try {
      clearUploadTracking(
          window.sessionStorage,
          ownerId,
      );
    } catch {
      setStorageWarning(
          "Không thể xóa bản ghi theo dõi " +
          "trong trình duyệt.",
      );
      return false;
    }

    setRecord(null);
    setFile(null);
    setJob(null);
    setStorageBlocked(false);
    setStorageWarning(null);
    setError(null);

    setPoll({
      kind: "checking",
    });

    setElapsed(0);

    return true;
  }


  function openExam() {
    const examId =
        job?.status === "completed"
            ? job.result?.exam_id
            : null;

    if (
        examId &&
        resetTracking(false)
    ) {
      navigate(
          `/quiz/${encodeURIComponent(examId)}`,
      );
    }
  }


  const label =
      submitting
          ? "Đang gửi PDF đến máy chủ"
          : !jobId
              ? "Chưa xác định được mã tác vụ"
              : job?.status === "completed"
                  ? "Đã hoàn thành"
                  : job?.status === "failed"
                      ? "Xử lý thất bại"
                      : poll.kind === "retrying"
                          ? `Mất kết nối tạm thời — thử lại sau ${poll.delayMs / 1000}s`
                          : poll.kind === "paused"
                              ? "Đã tạm dừng theo dõi"
                              : job
                                  ? PHASE_LABELS[job.phase] ?? "Đang xử lý"
                                  : "Đang kết nối lại với tác vụ";


  return (
      <div className="mx-auto w-full max-w-2xl">

        {storageBlocked ? (

            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-6">

              <p role="alert">
                Không đọc được phiên theo dõi PDF đã lưu.
              </p>

              <Link
                  to="/history"
                  className="mt-3 block text-[var(--color-primary)]"
              >
                Kiểm tra lịch sử PDF
              </Link>

              <button
                  type="button"
                  className={`${SECONDARY} mt-3`}
                  onClick={() => resetTracking()}
              >
                Xóa bản ghi theo dõi
              </button>

            </div>

        ) : !record && !file ? (

            <div
                className={`rounded-xl border-2 border-dashed p-12 text-center transition-colors ${
                    dragging
                        ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]"
                        : "border-[var(--color-border-strong)] bg-[var(--color-surface)]"
                }`}

                onDragOver={event => {
                  event.preventDefault();
                  setDragging(true);
                }}

                onDragLeave={() =>
                    setDragging(false)
                }

                onDrop={handleDrop}
            >

              <input
                  ref={inputRef}
                  type="file"
                  accept=".pdf,application/pdf"
                  onChange={handleFileChange}
                  className="hidden"
              />

              <button
                  type="button"
                  onClick={() =>
                      inputRef.current?.click()
                  }
                  className="w-full rounded-md focus-visible:outline-2 focus-visible:outline-[var(--color-primary)]"
              >

                <svg
                    className="mx-auto mb-4 h-12 w-12 text-[var(--color-text-muted)]"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    aria-hidden="true"
                >
                  <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                  />
                </svg>

                <span className="block text-lg font-semibold text-[var(--color-text)]">
                            Tải tệp PDF lên
                        </span>

                <span className="mt-2 block text-sm text-[var(--color-text-muted)]">
                            Kéo thả hoặc nhấn để chọn PDF · Tối đa 30 MB
                        </span>

              </button>

            </div>

        ) : (

            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-6 shadow-sm">

              {/* File information */}

              <div className="mb-5 flex items-center justify-between gap-3">

                <div className="min-w-0">

                  <p
                      className="truncate font-medium text-[var(--color-text)]"
                      title={record?.fileName ?? file?.name}
                  >
                    {record?.fileName ?? file?.name}
                  </p>

                  <p className="text-xs text-[var(--color-text-muted)]">
                    {
                      formatSize(
                          record?.fileSize ??
                          file?.size ??
                          0,
                      )
                    }
                  </p>

                </div>

                {!record && (
                    <button
                        type="button"
                        className={SECONDARY}
                        onClick={() => {
                          setFile(null);
                          setError(null);
                        }}
                    >
                      Bỏ chọn
                    </button>
                )}

              </div>

              {/* OCR model selection */}

              <label className="mb-4 block text-sm text-[var(--color-text-secondary)]">

                Mô hình OCR

                <select
                    value={record?.model ?? model}
                    disabled={record !== null}

                    onChange={event =>
                        setModel(event.target.value)
                    }

                    className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-[var(--color-text)] disabled:opacity-70"
                >

                  <option value="PaddleOCR-VL-1.6">
                    PaddleOCR-VL-1.6 · bố cục, công thức và bảng
                  </option>

                  <option value="PP-OCRv6">
                    PP-OCRv6 · nhận dạng văn bản nhanh
                  </option>

                </select>

              </label>

              {!record ? (

                  <button
                      type="button"
                      className={PRIMARY}
                      onClick={handleUpload}
                  >
                    Tạo bài trắc nghiệm
                  </button>

              ) : (

                  <section
                      className="border-t border-[var(--color-border)] pt-5"
                      aria-label="Tiến trình xử lý PDF"
                  >

                    <div className="mb-3 flex justify-between gap-3 text-sm">

                      <strong>
                        Tiến trình xử lý
                      </strong>

                      <span className="tabular-nums text-[var(--color-text-muted)]">
                                    {formatElapsed(elapsed)}
                                </span>

                    </div>

                    <p
                        role="status"
                        className="mb-4 text-sm text-[var(--color-primary)]"
                    >
                      {label}
                    </p>

                    {/* Processing stages */}

                    <div className="space-y-3">

                      {STEPS.map((step, index) => (

                          <div
                              key={step.phase}
                              className="flex items-center gap-3 text-sm"
                          >

                                        <span
                                            aria-hidden="true"

                                            className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-[var(--color-border)] ${
                                                index < completed
                                                    ? "text-emerald-500"
                                                    : index === completed && busy
                                                        ? "animate-pulse text-[var(--color-primary)]"
                                                        : "text-[var(--color-text-muted)]"
                                            }`}
                                        >

                                            {
                                              index < completed
                                                  ? "✓"
                                                  : index === completed && busy
                                                      ? "●"
                                                      : "○"
                                            }

                                        </span>

                            <span
                                className={
                                  index <= completed
                                      ? "text-[var(--color-text)]"
                                      : "text-[var(--color-text-muted)]"
                                }
                            >
                                            {step.label}
                                        </span>

                          </div>

                      ))}

                    </div>

                    <div
                        role="progressbar"
                        aria-label="Giai đoạn hoàn thành"
                        aria-valuemin={0}
                        aria-valuemax={STEPS.length}
                        aria-valuenow={completed}
                        className="mt-5 h-2 overflow-hidden rounded-full bg-[var(--color-bg-tertiary)]"
                    >

                      <div
                          className="h-full rounded-full bg-[var(--color-primary)] transition-all"

                          style={{
                            width: `${completed / STEPS.length * 100}%`,
                          }}
                      />

                    </div>

                    <p className="mt-2 text-xs text-[var(--color-text-muted)]">
                      Đã hoàn thành {completed}/{STEPS.length} giai đoạn
                    </p>

                    {/* Ambiguous submission */}

                    {!jobId && !submitting && (

                        <p className="mt-4 text-sm text-[var(--color-text-secondary)]">

                          Máy chủ có thể đã nhận PDF nhưng trình
                          duyệt chưa nhận được Job ID.
                          Kiểm tra lịch sử trước khi tải lại.

                        </p>

                    )}

                    {/* Temporary connection errors */}

                    {poll.kind === "retrying" && (

                        <p className="mt-4 text-sm text-[var(--color-text-secondary)]">

                          Đang kết nối lại ({poll.count}/4).
                          PDF không được gửi lại.

                        </p>

                    )}

                    {/* Polling paused */}

                    {poll.kind === "paused" && (

                        <p
                            role="alert"
                            className="mt-4 text-sm text-[var(--color-text-secondary)]"
                        >

                          {poll.message} Tác vụ có thể vẫn đang chạy;
                          không cần gửi lại PDF để tiếp tục theo dõi.

                        </p>

                    )}

                    {/* Confirmed backend failure */}

                    {job?.status === "failed" && (

                        <p
                            role="alert"
                            className="mt-4 text-sm text-[var(--color-danger)]"
                        >

                          {
                              job.error ||
                              "Không thể xử lý PDF."
                          }

                        </p>

                    )}

                    {/* Completed upload */}

                    {job?.status === "completed" && (

                        <button
                            type="button"
                            className={`${PRIMARY} mt-4`}
                            onClick={openExam}
                        >
                          Làm bài
                        </button>

                    )}

                    {/* Resume the same job */}

                    {jobId &&
                        poll.kind === "paused" &&
                        !terminal && (

                            <button
                                type="button"
                                className={`${PRIMARY} mt-4`}
                                onClick={resume}
                            >
                              Tiếp tục theo dõi
                            </button>

                        )}

                    {/* History and tracking controls */}

                    {!busy && (

                        <div className="mt-4 flex flex-wrap items-center gap-3">

                          <Link
                              to="/history"
                              className="text-sm text-[var(--color-primary)]"
                          >
                            Kiểm tra lịch sử PDF
                          </Link>

                          <button
                              type="button"
                              className={SECONDARY}
                              onClick={() =>
                                  resetTracking()
                              }
                          >

                            {
                              terminal
                                  ? "Chọn PDF khác"
                                  : "Xóa theo dõi"
                            }

                          </button>

                        </div>

                    )}

                    {busy && (

                        <p className="mt-4 text-xs text-[var(--color-text-muted)]">

                          {
                            jobId
                                ? "Bạn có thể rời trang hoặc refresh; quay lại trang chủ để tiếp tục theo dõi."
                                : "Vui lòng không refresh khi đang chờ mã tác vụ."
                          }

                        </p>

                    )}

                  </section>

              )}

            </div>

        )}

        {error && (

            <p
                role="alert"
                className="mt-4 rounded-md bg-[var(--color-danger-light)] p-3 text-sm text-[var(--color-danger)]"
            >
              {error}
            </p>

        )}

        {storageWarning && (

            <p
                role="alert"
                className="mt-4 text-sm text-[var(--color-text-secondary)]"
            >
              {storageWarning}
            </p>

        )}

      </div>
  );
}