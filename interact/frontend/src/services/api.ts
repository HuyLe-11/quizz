/**
 * API service layer for communicating with the FastAPI backend.
 *
 * Base URL: http://localhost:8000/api
 *
 * Each function documents the expected backend endpoint, method,
 * request/response format to make backend implementation straightforward.
 */

import type { ApiResponse, Exam, ProcessedExamSummary, RedMarkDetection, Subject, UploadResponse } from "../types/types";

const API_ORIGIN = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000")
  .replace(/\/+$/, "")
  .replace(/\/api$/i, "");
const API_BASE_URL = `${API_ORIGIN}/api`;

export function resolveApiUrl(path: string | null | undefined): string | null {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API_ORIGIN}${path}`;
}

// -- Helper --

/**
 * Generic fetch wrapper with error handling.
 * Parses JSON response and extracts data from ApiResponse wrapper.
 */
async function fetchApi<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const headers = new Headers(options?.headers);
  if (options?.body != null && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...options,
      headers,
      signal: options?.signal ?? AbortSignal.timeout(20000),
    });
  } catch (requestError) {
    if (requestError instanceof DOMException && requestError.name === "TimeoutError") {
      throw new Error("Máy chủ phản hồi quá lâu. Hãy kiểm tra backend rồi thử lại.");
    }
    throw new Error("Không thể kết nối máy chủ. Hãy kiểm tra backend và cấu hình địa chỉ API.");
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || error.detail || `Máy chủ gặp lỗi (${response.status}).`);
  }

  const result: ApiResponse<T> = await response.json();

  if (!result.success) {
    throw new Error(result.message || "Đã xảy ra lỗi khi gọi API.");
  }

  return result.data;
}

// -- Subject Endpoints --

/**
 * Fetch all available subjects.
 *
 * Backend endpoint: GET /api/subjects
 * Response: ApiResponse<Subject[]>
 * Example response:
 * {
 *   "success": true,
 *   "data": [
 *     { "id": "net101", "name": "Computer Networks", "description": "...", "exam_count": 5 }
 *   ]
 * }
 */
export async function getSubjects(): Promise<Subject[]> {
  return fetchApi<Subject[]>("/subjects");
}

/**
 * Fetch all exams for a specific subject (without full question data).
 *
 * Backend endpoint: GET /api/subjects/:subjectId/exams
 * Response: ApiResponse<Exam[]>
 * Note: `questions` array may be empty in list view for performance.
 */
export async function getExamsBySubject(subjectId: string): Promise<Exam[]> {
  return fetchApi<Exam[]>(`/subjects/${subjectId}/exams`);
}

// -- Exam Endpoints --

/**
 * Fetch a single exam with all questions.
 *
 * Backend endpoint: GET /api/exams/:examId
 * Response: ApiResponse<Exam>
 * The `questions` array should contain full question data including
 * options, matches, fill_in_answers, and explanations.
 */
export async function getExam(examId: string): Promise<Exam> {
  const exam = await fetchApi<Exam>(`/exams/${examId}`);
  return {
    ...exam,
    questions: exam.questions.map((question) => ({
      ...question,
      question_image_url: resolveApiUrl(question.question_image_url),
    })),
  };
}

export async function getProcessedExams(): Promise<ProcessedExamSummary[]> {
  return fetchApi<ProcessedExamSummary[]>("/exams");
}

export async function cropQuestionImage(questionRecordId: string, force = false) {
  const result = await fetchApi<{ image_url: string; crop_status: string; page_number: number; source: string; cached: boolean }>(
    `/questions/${questionRecordId}/crop-image?force=${force}`,
    { method: "POST" },
  );
  return { ...result, image_url: resolveApiUrl(result.image_url) || "" };
}

export async function detectRedMark(questionRecordId: string, force = false): Promise<RedMarkDetection> {
  return fetchApi<RedMarkDetection>(
    `/questions/${questionRecordId}/detect-red-mark?force=${force}`,
    { method: "POST" },
  );
}

export async function confirmRedAnswer(questionRecordId: string, answer: string | null) {
  return fetchApi<RedMarkDetection>(`/questions/${questionRecordId}/red-mark-confirmation`, {
    method: "PATCH",
    body: JSON.stringify({ answer }),
  });
}

export async function saveQuizAttempt(examId: string, attempt: {
  score: number;
  total_questions: number;
  correct_answers_count: number;
  time_spent: number;
  answers: Record<string, unknown>;
  question_results: unknown[];
  completed_at: number;
}) {
  return fetchApi<{ id: string; completed_at: number }>(`/exams/${examId}/attempts`, {
    method: "POST",
    body: JSON.stringify(attempt),
  });
}

// -- Upload Endpoints --

/**
 * Upload a PDF file to generate an exam.
 *
 * Backend endpoint: POST /api/upload
 * Request: multipart/form-data with field "file" (PDF)
 * Response: ApiResponse<UploadResponse>
 *
 * The backend should:
 * 1. Accept the PDF file
 * 2. Parse/extract questions from the PDF
 * 3. Return the created exam ID and metadata
 */
export async function uploadPdf(file: File, model = "PaddleOCR-VL-1.6"): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("model", model);

  const response = await fetch(`${API_BASE_URL}/upload`, {
    method: "POST",
    body: formData,
    // Note: Do NOT set Content-Type header; browser sets it with boundary
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || error.detail || `Tải tệp lên không thành công (${response.status}).`);
  }

  const result: ApiResponse<UploadResponse> = await response.json();

  if (!result.success) {
    throw new Error(result.message || "Tải tệp lên không thành công.");
  }

  return result.data;
}
