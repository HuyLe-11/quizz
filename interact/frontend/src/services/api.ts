/**
 * API service layer for communicating with the FastAPI backend.
 * Supports authenticated requests and asynchronous PDF jobs.
 */

import { requireSupabase } from "./supabase";

import type {
    ApiResponse,
    Exam,
    ProcessedExamSummary,
    RedMarkDetection,
    Subject,
    UploadResponse,
    UploadJob,
} from "../types/types";

const configuredApiOrigin =
    import.meta.env.VITE_API_BASE_URL?.trim();

const API_ORIGIN = (
    configuredApiOrigin ||
    (import.meta.env.DEV ? "http://localhost:8000" : "")
)
    .replace(/\/+$/, "")
    .replace(/\/api$/i, "");

const API_BASE_URL = `${API_ORIGIN}/api`;

const REQUEST_TIMEOUT_MS = 20_000;
const EXAM_LOAD_TIMEOUT_MS = 120_000;
const UPLOAD_TIMEOUT_MS = 120_000;
const POLLING_INTERVAL_MS = 2_000;
const MAX_POLLING_TIME_MS = 600_000;

export function resolveApiUrl(
    path: string | null | undefined,
): string | null {
    if (!path) return null;

    return path.startsWith("http")
        ? path
        : `${API_ORIGIN}${path}`;
}

async function getAuthorizationHeader(): Promise<string> {
    const client = requireSupabase();

    const {
        data: { session },
        error,
    } = await client.auth.getSession();

    if (error) {
        throw new Error(
            `Không thể lấy phiên đăng nhập: ${error.message}`,
        );
    }

    if (!session?.access_token) {
        throw new Error(
            "Phiên đăng nhập đã hết hạn. " +
            "Vui lòng đăng nhập lại.",
        );
    }

    return `Bearer ${session.access_token}`;
}

async function fetchApi<T>(
    endpoint: string,
    options?: RequestInit,
    timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<T> {
    const headers = new Headers(options?.headers);

    headers.set(
        "Authorization",
        await getAuthorizationHeader(),
    );

    if (
        options?.body != null &&
        !(options.body instanceof FormData) &&
        !headers.has("Content-Type")
    ) {
        headers.set("Content-Type", "application/json");
    }

    const timeoutSignal = AbortSignal.timeout(timeoutMs);

    const signal = options?.signal
        ? AbortSignal.any([
            options.signal,
            timeoutSignal,
        ])
        : timeoutSignal;

    try {
        const response = await fetch(
            `${API_BASE_URL}${endpoint}`,
            {
                ...options,
                headers,
                signal,
            },
        );

        const payload = await response.json().catch((error) => {
            if (signal.aborted) {
                throw error;
            }

            return null;
        });

        if (!response.ok) {
            const message =
                typeof payload?.detail === "string"
                    ? payload.detail
                    : typeof payload?.message === "string"
                        ? payload.message
                        : `Máy chủ gặp lỗi (${response.status}).`;

            throw new Error(message);
        }

        if (
            !payload ||
            typeof payload !== "object"
        ) {
            throw new Error(
                "Máy chủ trả về dữ liệu không hợp lệ.",
            );
        }

        const result = payload as ApiResponse<T>;

        if (!result.success) {
            throw new Error(
                result.message ||
                "Đã xảy ra lỗi khi gọi API.",
            );
        }

        return result.data;
    } catch (error) {
        if (options?.signal?.aborted) {
            throw error;
        }

        if (timeoutSignal.aborted) {
            throw new Error(
                "Máy chủ phản hồi quá lâu. " +
                "Hãy kiểm tra kết nối và backend.",
                { cause: error },
            );
        }

        if (error instanceof TypeError) {
            throw new Error(
                "Không thể kết nối máy chủ. " +
                "Hãy kiểm tra backend và địa chỉ API.",
                { cause: error },
            );
        }

        throw error;
    }
}

// Subject endpoints.

export async function getSubjects(): Promise<Subject[]> {
    return fetchApi<Subject[]>("/subjects");
}

export async function getExamsBySubject(
    subjectId: string,
): Promise<Exam[]> {
    return fetchApi<Exam[]>(
        `/subjects/${subjectId}/exams`,
    );
}

// Exam endpoints.

export async function getExam(
    examId: string,
): Promise<Exam> {
    // A cloud exam may contain many private images.
    // The backend currently signs their URLs before replying.
    // Use a separate timeout instead of increasing it globally.
    const exam = await fetchApi<Exam>(
        `/exams/${encodeURIComponent(examId)}`,
        undefined,
        EXAM_LOAD_TIMEOUT_MS,
    );

    return {
        ...exam,
        questions: exam.questions.map((question) => ({
            ...question,
            question_image_url: resolveApiUrl(
                question.question_image_url,
            ),
        })),
    };
}

export async function getProcessedExams(): Promise<
    ProcessedExamSummary[]
> {
    return fetchApi<ProcessedExamSummary[]>("/exams");
}

// Question image and red-mark detection endpoints.

export async function cropQuestionImage(
    questionRecordId: string,
    force = false,
) {
    const result = await fetchApi<{
        image_url: string;
        crop_status: string;
        page_number: number;
        source: string;
        cached: boolean;
    }>(
        `/questions/${questionRecordId}/crop-image?force=${force}`,
        {
            method: "POST",
        },
    );

    return {
        ...result,
        image_url: resolveApiUrl(result.image_url) || "",
    };
}

export async function detectRedMark(
    questionRecordId: string,
    force = false,
): Promise<RedMarkDetection> {
    return fetchApi<RedMarkDetection>(
        `/questions/${questionRecordId}/detect-red-mark?force=${force}`,
        {
            method: "POST",
        },
    );
}

export async function confirmRedAnswer(
    questionRecordId: string,
    answer: string | null,
) {
    return fetchApi<RedMarkDetection>(
        `/questions/${questionRecordId}/red-mark-confirmation`,
        {
            method: "PATCH",
            body: JSON.stringify({ answer }),
        },
    );
}

// Quiz attempt endpoints.

export async function saveQuizAttempt(
    examId: string,
    attempt: {
        score: number;
        total_questions: number;
        correct_answers_count: number;
        time_spent: number;
        answers: Record<string, unknown>;
        question_results: unknown[];
        completed_at: number;
    },
) {
    return fetchApi<{
        id: string;
        completed_at: number;
    }>(
        `/exams/${examId}/attempts`,
        {
            method: "POST",
            body: JSON.stringify(attempt),
        },
    );
}

// Shared multipart upload payload.

function createUploadForm(
    file: File,
    model: string,
): FormData {
    const formData = new FormData();

    formData.append("file", file);
    formData.append("model", model);

    return formData;
}

// Legacy synchronous upload endpoint.

export async function uploadPdf(
    file: File,
    model = "PaddleOCR-VL-1.6",
): Promise<UploadResponse> {
    return fetchApi<UploadResponse>(
        "/upload",
        {
            method: "POST",
            body: createUploadForm(file, model),
        },
        MAX_POLLING_TIME_MS,
    );
}

// Asynchronous Request-Reply: submit a new upload job.

export async function createUploadJob(
    file: File,
    model = "PaddleOCR-VL-1.6",
    signal?: AbortSignal,
): Promise<UploadJob> {
    return fetchApi<UploadJob>(
        "/upload/jobs",
        {
            method: "POST",
            body: createUploadForm(file, model),
            signal,
        },
        UPLOAD_TIMEOUT_MS,
    );
}

// Asynchronous Request-Reply: query an existing job.

export async function getUploadJob(
    jobId: string,
    signal?: AbortSignal,
): Promise<UploadJob> {
    return fetchApi<UploadJob>(
        `/upload/jobs/${encodeURIComponent(jobId)}`,
        {
            method: "GET",
            cache: "no-store",
            signal,
        },
    );
}

export function isActiveUploadJob(
    job: UploadJob | null,
): boolean {
    return (
        job?.status === "queued" ||
        job?.status === "processing"
    );
}

// Abortable delay prevents overlapping polling requests.

function wait(
    milliseconds: number,
    signal: AbortSignal,
): Promise<void> {
    return new Promise((resolve, reject) => {
        if (signal.aborted) {
            reject(signal.reason);
            return;
        }

        const onAbort = () => {
            window.clearTimeout(timer);
            reject(signal.reason);
        };

        const timer = window.setTimeout(() => {
            signal.removeEventListener(
                "abort",
                onAbort,
            );
            resolve();
        }, milliseconds);

        signal.addEventListener(
            "abort",
            onAbort,
            {
                once: true,
            },
        );
    });
}

/**
 * Poll an existing upload job until completion or failure.
 * This function never submits another PDF.
 */
export async function waitForUploadJob(
    initialJob: UploadJob,
    onUpdate: (job: UploadJob) => void,
    signal: AbortSignal,
): Promise<UploadResponse> {
    const pollingTimeout = AbortSignal.timeout(
        MAX_POLLING_TIME_MS,
    );

    const pollingSignal = AbortSignal.any([
        signal,
        pollingTimeout,
    ]);

    let job = initialJob;

    try {
        while (true) {
            pollingSignal.throwIfAborted();

            onUpdate(job);

            if (job.status === "completed") {
                if (!job.result?.exam_id) {
                    throw new Error(
                        "Tác vụ đã hoàn thành nhưng " +
                        "không có thông tin đề thi.",
                    );
                }

                return job.result;
            }

            if (job.status === "failed") {
                throw new Error(
                    job.error ||
                    "Không thể xử lý tài liệu PDF.",
                );
            }

            if (!isActiveUploadJob(job)) {
                throw new Error(
                    "Máy chủ trả về trạng thái " +
                    "tác vụ không hợp lệ.",
                );
            }

            await wait(
                POLLING_INTERVAL_MS,
                pollingSignal,
            );

            job = await getUploadJob(
                job.job_id,
                pollingSignal,
            );
        }
    } catch (error) {
        if (signal.aborted) {
            throw error;
        }

        if (pollingTimeout.aborted) {
            throw new Error(
                "Đã hết thời gian theo dõi tác vụ. " +
                "PDF có thể vẫn đang được xử lý. " +
                "Hãy tiếp tục theo dõi thay vì tải lại tệp.",
                { cause: error },
            );
        }

        throw error;
    }
}
