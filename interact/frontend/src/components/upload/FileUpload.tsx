import {
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  ChangeEvent,
  DragEvent,
} from "react";

import { useNavigate } from "react-router-dom";

import {
  createUploadJob,
  isActiveUploadJob,
  waitForUploadJob,
  uploadPdf,
  usesUploadJobs,
} from "../../services/api";

import type {
  UploadJob,
  UploadJobPhase,
} from "../../types/types";

const MAX_FILE_SIZE = 30 * 1024 * 1024;

const PROCESSING_STEPS = [
  {
    phase: "uploading",
    label: "Tiếp nhận tệp PDF",
  },
  {
    phase: "ocr",
    label: "Nhận diện nội dung bằng PaddleOCR",
  },
  {
    phase: "question_extraction",
    label: "Phân tích câu hỏi",
  },
  {
    phase: "question_crops",
    label: "Xử lý ảnh câu hỏi",
  },
  {
    phase: "cloud_save",
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

function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";

  if (bytes < 1024) {
    return `${bytes} B`;
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(2)} KB`;
  }

  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatElapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60);

  const remainingSeconds = seconds % 60;

  return (
      String(minutes).padStart(2, "0") +
      ":" +
      String(remainingSeconds).padStart(2, "0")
  );
}

function getCompletedSteps(
    job: UploadJob | null,
): number {
  if (!job) return 0;

  if (job.status === "completed") {
    return PROCESSING_STEPS.length;
  }

  if (
      job.phase === "queued" ||
      job.phase === "starting"
  ) {
    return 1;
  }

  const phase = job.phase === "sqlite_save"
      ? "cloud_save"
      : job.phase === "source_pdf_save"
          ? "question_crops"
          : job.phase;

  const index = PROCESSING_STEPS.findIndex(
      (step) => step.phase === phase,
  );

  return Math.max(1, index);
}

function Spinner({
                   className = "",
                 }: {
  className?: string;
}) {
  return (
      <svg
          className={`animate-spin h-4 w-4 ${className}`}
          fill="none"
          viewBox="0 0 24 24"
          aria-hidden="true"
      >
        <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
        />

        <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
        />
      </svg>
  );
}

function CheckIcon() {
  return (
      <svg
          className="h-4 w-4 shrink-0"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          aria-hidden="true"
      >
        <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="m5 12 4 4L19 6"
        />
      </svg>
  );
}

export function FileUpload() {
  const [file, setFile] =
      useState<File | null>(null);

  const [isDragging, setIsDragging] =
      useState(false);

  const [isUploading, setIsUploading] =
      useState(false);

  const [model, setModel] =
      useState("PaddleOCR-VL-1.6");

  const [error, setError] =
      useState<string | null>(null);

  const [job, setJob] =
      useState<UploadJob | null>(null);

  const [startedAt, setStartedAt] =
      useState<number | null>(null);

  const [elapsed, setElapsed] =
      useState(0);

  const fileInputRef =
      useRef<HTMLInputElement>(null);

  const requestRef =
      useRef<AbortController | null>(null);

  const navigate = useNavigate();

  const hasActiveJob = isActiveUploadJob(job);

  const completedSteps = getCompletedSteps(job);

  const totalSteps = PROCESSING_STEPS.length;

  const progress =
      (completedSteps / totalSteps) * 100;

  // Stop frontend requests when the component unmounts.
  // This does not cancel the backend processing job.
  useEffect(() => {
    return () => {
      requestRef.current?.abort();
    };
  }, []);

  // Track elapsed waiting time, independently of job progress.
  useEffect(() => {
    if (
        !isUploading ||
        startedAt === null
    ) {
      return;
    }

    const timer = window.setInterval(() => {
      setElapsed(
          Math.floor(
              (Date.now() - startedAt) / 1000,
          ),
      );
    }, 1000);

    return () => {
      window.clearInterval(timer);
    };
  }, [isUploading, startedAt]);

  const validateAndSetFile = (
      selectedFile: File | undefined,
  ) => {
    if (!selectedFile) return;

    const isPdf =
        selectedFile.type === "application/pdf" ||
        selectedFile.name.toLowerCase().endsWith(".pdf");

    if (!isPdf) {
      setError("Vui lòng chọn tệp PDF.");
      return;
    }

    if (selectedFile.size === 0) {
      setError("Tệp PDF không có nội dung.");
      return;
    }

    if (selectedFile.size > MAX_FILE_SIZE) {
      setError(
          "Dung lượng PDF không được vượt quá 30 MB.",
      );

      return;
    }

    setFile(selectedFile);
    setError(null);
    setJob(null);
    setElapsed(0);
    setStartedAt(null);
  };

  const handleDragOver = (
      event: DragEvent<HTMLDivElement>,
  ) => {
    event.preventDefault();

    if (!isUploading) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (
      event: DragEvent<HTMLDivElement>,
  ) => {
    event.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (
      event: DragEvent<HTMLDivElement>,
  ) => {
    event.preventDefault();
    setIsDragging(false);

    if (
        isUploading ||
        hasActiveJob
    ) {
      return;
    }

    validateAndSetFile(
        event.dataTransfer.files[0],
    );
  };

  const handleFileChange = (
      event: ChangeEvent<HTMLInputElement>,
  ) => {
    validateAndSetFile(
        event.target.files?.[0],
    );

    event.target.value = "";
  };

  const handleRemoveFile = () => {
    if (isUploading) return;

    setFile(null);
    setJob(null);
    setError(null);
    setElapsed(0);
    setStartedAt(null);
  };

  const handleUpload = async () => {
    if (
        !file ||
        requestRef.current !== null
    ) {
      return;
    }

    const controller =
        new AbortController();

    requestRef.current = controller;

    setIsUploading(true);
    setError(null);

    // Resume an existing job after a temporary connection error.
    const existingJob =
        job && isActiveUploadJob(job)
            ? job
            : null;

    const startTime =
        existingJob && startedAt !== null
            ? startedAt
            : Date.now();

    setStartedAt(startTime);

    setElapsed(
        Math.floor(
            (Date.now() - startTime) / 1000,
        ),
    );

    try {
      if (!usesUploadJobs()) {
        const result = await uploadPdf(
            file,
            model,
            controller.signal,
        );

        controller.signal.throwIfAborted();

        navigate(
            `/quiz/${encodeURIComponent(result.exam_id)}`,
        );

        return;
      }

      let currentJob: UploadJob;

      if (existingJob) {
        currentJob = existingJob;
      } else {
        setJob(null);

        currentJob = await createUploadJob(
            file,
            model,
            controller.signal,
        );
      }

      controller.signal.throwIfAborted();

      setJob(currentJob);

      const result = await waitForUploadJob(
          currentJob,
          (updatedJob) => {
            if (!controller.signal.aborted) {
              setJob(updatedJob);
            }
          },
          controller.signal,
      );

      controller.signal.throwIfAborted();

      navigate(
          `/quiz/${encodeURIComponent(result.exam_id)}`,
      );
    } catch (requestError) {
      if (!controller.signal.aborted) {
        setError(
            requestError instanceof Error
                ? requestError.message
                : "Không thể xử lý tệp PDF.",
        );
      }
    } finally {
      if (
          requestRef.current === controller
      ) {
        requestRef.current = null;
      }

      if (!controller.signal.aborted) {
        setElapsed(
            Math.floor(
                (Date.now() - startTime) / 1000,
            ),
        );

        setIsUploading(false);
      }
    }
  };

  const phaseLabel = !job
      ? (usesUploadJobs()
          ? "Đang tải tệp PDF lên máy chủ"
          : "Đang xử lý PDF (không có tiến độ từng giai đoạn)")
      : PHASE_LABELS[job.phase] ||
      "Đang xử lý tài liệu";

  const progressLabel =
      job?.status === "failed"
          ? `Xử lý thất bại — ${phaseLabel}`
          : !isUploading && hasActiveJob
              ? "Đã tạm dừng theo dõi tác vụ"
              : phaseLabel;

  return (
      <div className="w-full max-w-2xl mx-auto">
        {!file ? (
            <div
                className={`relative border-2 border-dashed rounded-xl p-12 text-center transition-colors cursor-pointer ${
                    isDragging
                        ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]"
                        : "border-[var(--color-border-strong)] hover:border-[var(--color-primary)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)]"
                }`}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() =>
                    fileInputRef.current?.click()
                }
                onKeyDown={(event) => {
                  if (
                      event.key === "Enter" ||
                      event.key === " "
                  ) {
                    event.preventDefault();
                    fileInputRef.current?.click();
                  }
                }}
                role="button"
                tabIndex={0}
                aria-label="Chọn tệp PDF"
            >
              <input
                  type="file"
                  ref={fileInputRef}
                  onChange={handleFileChange}
                  accept=".pdf,application/pdf"
                  className="hidden"
              />

              <svg
                  className="mx-auto h-12 w-12 text-[var(--color-text-muted)] mb-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth="2"
              >
                <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                />
              </svg>

              <h3 className="mt-2 text-lg font-semibold text-[var(--color-text)]">
                Tải tệp PDF lên
              </h3>

              <p className="mt-1 text-sm text-[var(--color-text-muted)]">
                Kéo thả tệp vào đây hoặc nhấn để chọn tệp
              </p>

              <p className="mt-2 text-xs text-[var(--color-text-muted)]">
                Dung lượng tối đa: 30 MB
              </p>
            </div>
        ) : (
            <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-6 shadow-sm">
              {/* Selected PDF information */}

              <div className="flex items-center justify-between mb-6">
                <div className="flex items-center space-x-3 min-w-0">
                  <svg
                      className="h-8 w-8 shrink-0 text-[var(--color-primary)]"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                  >
                    <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth="2"
                        d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                    />
                  </svg>

                  <div className="min-w-0">
                    <p
                        className="text-sm font-medium text-[var(--color-text)] truncate"
                        title={file.name}
                    >
                      {file.name}
                    </p>

                    <p className="text-xs text-[var(--color-text-muted)]">
                      {formatFileSize(file.size)}
                    </p>
                  </div>
                </div>

                <button
                    type="button"
                    onClick={handleRemoveFile}
                    className="text-[var(--color-text-muted)] hover:text-[var(--color-danger)] transition-colors p-1 disabled:opacity-40 disabled:cursor-not-allowed"
                    disabled={isUploading}
                    aria-label="Bỏ chọn tệp PDF"
                >
                  <svg
                      className="h-5 w-5"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                  >
                    <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth="2"
                        d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
              </div>

              {/* Submit or resume an upload job */}

              <button
                  type="button"
                  onClick={handleUpload}
                  disabled={isUploading}
                  className="w-full py-2.5 px-4 bg-[var(--color-primary)] hover:bg-[var(--color-primary-hover)] text-white font-medium rounded-md transition-colors disabled:opacity-70 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                {isUploading ? (
                    <>
                      <Spinner />

                      Đang xử lý...
                    </>
                ) : hasActiveJob ? (
                    "Tiếp tục theo dõi"
                ) : (
                    "Tạo bài trắc nghiệm"
                )}
              </button>

              {/* OCR model selection */}

              <label className="mt-4 block text-left text-sm text-[var(--color-text-secondary)]">
                Mô hình OCR

                <select
                    value={model}
                    onChange={(event) =>
                        setModel(event.target.value)
                    }
                    disabled={
                        isUploading || hasActiveJob
                    }
                    className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-[var(--color-text)] disabled:opacity-70"
                >
                  <option value="PaddleOCR-VL-1.6">
                    PaddleOCR-VL-1.6 · bố cục tài liệu,
                    công thức và bảng
                  </option>

                  <option value="PP-OCRv6">
                    PP-OCRv6 · nhận dạng văn bản nhanh
                  </option>
                </select>
              </label>

              {/* Processing progress */}

              {(isUploading || job) && (
                  <div className="mt-6 pt-5 border-t border-[var(--color-border)]">
                    <div className="flex items-center justify-between gap-3 mb-3">
                      <h4 className="text-sm font-semibold text-[var(--color-text)]">
                        Tiến trình xử lý
                      </h4>

                      <span className="text-sm tabular-nums text-[var(--color-text-muted)]">
                  {formatElapsed(elapsed)}
                </span>
                    </div>

                    <p
                        className={`text-sm mb-4 ${
                            job?.status === "failed"
                                ? "text-[var(--color-danger)]"
                                : "text-[var(--color-primary)]"
                        }`}
                        role="status"
                        aria-live="polite"
                    >
                      {progressLabel}
                    </p>

                    {usesUploadJobs() && <>
                    <div className="space-y-3">
                      {PROCESSING_STEPS.map(
                          (step, index) => {
                            const isComplete =
                                index < completedSteps;

                            const isQueuePhase =
                                job?.phase === "queued" ||
                                job?.phase === "starting";

                            const isCurrent =
                                isUploading &&
                                !isQueuePhase &&
                                index === completedSteps;

                            const isFailed =
                                job?.status === "failed" &&
                                !isQueuePhase &&
                                index === completedSteps;

                            return (
                                <div
                                    key={step.phase}
                                    className="flex items-center gap-3"
                                >
                        <span
                            className={`w-5 h-5 rounded-full shrink-0 flex items-center justify-center ${
                                isComplete
                                    ? "bg-emerald-500/15 text-[var(--color-success)]"
                                    : isCurrent
                                        ? "text-[var(--color-primary)]"
                                        : isFailed
                                            ? "text-[var(--color-danger)]"
                                            : "border border-[var(--color-border-strong)]"
                            }`}
                        >
                          {isComplete ? (
                              <CheckIcon />
                          ) : isCurrent ? (
                              <Spinner />
                          ) : isFailed ? (
                              <span className="font-bold">
                              !
                            </span>
                          ) : null}
                        </span>

                                  <span
                                      className={`text-sm ${
                                          isComplete
                                              ? "text-[var(--color-text)]"
                                              : isCurrent
                                                  ? "text-[var(--color-primary)] font-medium"
                                                  : isFailed
                                                      ? "text-[var(--color-danger)]"
                                                      : "text-[var(--color-text-muted)]"
                                      }`}
                                  >
                          {step.label}
                        </span>
                                </div>
                            );
                          },
                      )}
                    </div>

                    <div
                        className="mt-5 h-2 bg-[var(--color-bg-tertiary)] rounded-full overflow-hidden"
                        role="progressbar"
                        aria-label="Số giai đoạn đã hoàn thành"
                        aria-valuemin={0}
                        aria-valuemax={totalSteps}
                        aria-valuenow={completedSteps}
                    >
                      <div
                          className="h-full rounded-full bg-[var(--color-primary)] transition-all duration-500"
                          style={{
                            width: `${progress}%`,
                          }}
                      />
                    </div>

                    <div className="mt-2 flex items-center justify-between text-xs text-[var(--color-text-muted)]">
                <span>
                  Đã hoàn thành {completedSteps}/
                  {totalSteps} giai đoạn
                </span>

                      <span>
                  {job?.status === "queued"
                      ? "Đang chờ"
                      : job?.status === "completed"
                          ? "Hoàn thành"
                          : job?.status === "failed"
                              ? "Thất bại"
                              : isUploading
                                  ? "Đang xử lý"
                                  : "Dừng theo dõi"}
                </span>
                    </div>

                    </>}
                    {!isUploading && hasActiveJob && (
                        <p className="mt-3 text-xs text-[var(--color-text-secondary)]">
                          Tác vụ có thể vẫn đang được xử lý
                          trên máy chủ. Nhấn Tiếp tục theo
                          dõi để kiểm tra lại mà không tải
                          PDF lên lần nữa.
                        </p>
                    )}
                  </div>
              )}
            </div>
        )}

        {/* Request and processing errors */}

        {error && (
            <div
                className="mt-4 p-3 bg-[var(--color-danger-light)] text-[var(--color-danger)] rounded-md text-sm text-center"
                role="alert"
            >
              {error}
            </div>
        )}
      </div>
  );
}
