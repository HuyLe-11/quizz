/**
 * API service layer for communicating with the FastAPI backend.
 *
 * Base URL: http://localhost:8000/api
 *
 * Each function documents the expected backend endpoint, method,
 * request/response format to make backend implementation straightforward.
 */

import type { ApiResponse, Exam, Subject, UploadResponse } from "../types/types";

const API_BASE_URL = "http://localhost:8000/api";

// -- Helper --

/**
 * Generic fetch wrapper with error handling.
 * Parses JSON response and extracts data from ApiResponse wrapper.
 */
async function fetchApi<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || `API error: ${response.status}`);
  }

  const result: ApiResponse<T> = await response.json();

  if (!result.success) {
    throw new Error(result.message || "Unknown API error");
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
  return fetchApi<Exam>(`/exams/${examId}`);
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
export async function uploadPdf(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE_URL}/upload`, {
    method: "POST",
    body: formData,
    // Note: Do NOT set Content-Type header; browser sets it with boundary
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || `Upload failed: ${response.status}`);
  }

  const result: ApiResponse<UploadResponse> = await response.json();

  if (!result.success) {
    throw new Error(result.message || "Upload failed");
  }

  return result.data;
}
