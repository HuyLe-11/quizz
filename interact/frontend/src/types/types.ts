/**
 * Core type definitions for QuizPDF.
 * These interfaces define the contract between React and FastAPI.
 */

export type QuestionType =
    | "mcp"
    | "true_false_ng"
    | "matching"
    | "fill_in";

export interface Option {
  label: string;
  content: string;
}

export interface Match {
  answer: string;
  description: string;
}

export interface Question {
  question_id: number;
  question_record_id?: string;
  has_source_pdf?: boolean;
  type: QuestionType;
  multiple_choice: boolean;
  question: string;
  options?: Option[];
  option_answer?: string[];
  fill_in_answers?: string[];
  matches?: Match[];
  explanation?: string;
  page_number?: number | null;
  question_image_url?: string | null;
  crop_status?: "success" | "failed" | "not_created" | string;
  red_mark_detection?: RedMarkDetection | null;
  user_confirmed_answer?: string | null;
}

export interface RedMarkDetection {
  detected_answer: string | null;
  confidence: number;
  status: "detected" | "uncertain" | "not_found";
  page_number: number;
  mark_coordinates: number[] | null;
  user_confirmed_answer?: string | null;
  cached?: boolean;
}

export interface Subject {
  id: string;
  name: string;
  description: string;
  exam_count: number;
  icon?: string;
}

export interface Exam {
  id: string;
  subject_id: string;
  title: string;
  question_count: number;
  questions: Question[];
  source_file?: string | null;
  uploaded_at?: string | null;
  has_source_pdf?: boolean;
}

export interface ProcessedExamSummary {
  id: string;
  name: string;
  subject?: string | null;
  source_file?: string | null;
  uploaded_at: string;
  has_source_pdf: boolean;
  question_count: number;
}

export interface ApiResponse<T> {
  success: boolean;
  data: T;
  message?: string;
}

// Upload response used by both the legacy and job-based APIs.

export interface UploadResponse {
  exam_id: string;
  title: string;
  question_count: number;
}

// Asynchronous Request-Reply contracts.

export type UploadJobStatus =
    | "queued"
    | "processing"
    | "completed"
    | "failed";

export type UploadJobPhase =
    | "queued"
    | "starting"
    | "ocr"
    | "question_extraction"
    | "source_pdf_save"
    | "question_crops"
    | "sqlite_save"
    | "cloud_save"
    | "completed";

export interface UploadJob {
  job_id: string;
  status: UploadJobStatus;
  phase: UploadJobPhase;
  result: UploadResponse | null;
  error: string | null;
  error_status: number | null;
}

// User answers.

export interface UserAnswer {
  question_id: number;
  selected_options?: string[];
  true_false_value?: string;
  matching_selections?: Record<number, string>;
  fill_in_values?: string[];
}

// Quiz session state.

export interface QuizState {
  exam: Exam | null;
  current_index: number;
  answers: Record<number, UserAnswer>;
  is_submitted: boolean;
  start_time: number | null;
}

// Statistics and attempt logs.

export interface QuestionResult {
  question_id: number;
  type: QuestionType;
  is_correct: boolean;
}

export interface QuizAttempt {
  id: string;
  exam_id: string;
  exam_title: string;
  subject_id: string;
  subject_name: string;
  score: number;
  total_questions: number;
  correct_answers_count: number;
  time_spent: number;
  completed_at: number;
  answers: Record<number, UserAnswer>;
  question_results: QuestionResult[];
}

export interface QuestionTypeStats {
  correct: number;
  total: number;
  percentage: number;
}

export interface QuizStats {
  totalAttempts: number;
  avgScore: number;
  totalTimeSpent: number;
  byType: Record<string, QuestionTypeStats>;
  scoreProgress: {
    date: string;
    score: number;
    examTitle: string;
  }[];
}
