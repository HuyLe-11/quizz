/**
 * Core type definitions for the Quiz application.
 *
 * These types define the contract between the React frontend and FastAPI backend.
 * Backend endpoints should return data matching these interfaces.
 */

// -- Question Type Enum --

/** Supported question types in the quiz system */
export type QuestionType = "mcp" | "true_false_ng" | "matching" | "fill_in";

// -- Option & Match Types --

/**
 * A single option for multiple-choice questions (mcp).
 * Example: { label: "A", content: "TCP uses 3-way handshake" }
 *
 * `content` may contain plain text, inline code, LaTeX (wrapped in $ or $$),
 * or an image URL prefixed with "![img]".
 */
export interface Option {
  label: string;
  content: string;
}

/**
 * A single match pair for matching questions.
 * `answer` and `description` may contain text, LaTeX, or image URLs.
 *
 * Note: The number of answers can exceed the number of descriptions,
 * meaning some answers may not have a matching description (distractors).
 */
export interface Match {
  answer: string;
  description: string;
}

// -- Question Interface --

/**
 * Represents a single question in an exam.
 *
 * Backend endpoint: GET /api/exams/:examId/questions
 * Each question has a type that determines which fields are relevant:
 *  - "mcp": uses `options` and `option_answer`
 *  - "true_false_ng": uses `option_answer` (values: "true" | "false" | "ng")
 *  - "matching": uses `matches`
 *  - "fill_in": uses `fill_in_answers`
 */
export interface Question {
  question_id: number;
  type: QuestionType;
  multiple_choice: boolean;
  question: string;
  options?: Option[];
  option_answer?: string[];
  fill_in_answers?: string[];
  matches?: Match[];
  explanation?: string;
}

// -- Subject & Exam --

/**
 * A subject/course that contains multiple exams.
 * Backend endpoint: GET /api/subjects
 */
export interface Subject {
  id: string;
  name: string;
  description: string;
  exam_count: number;
  icon?: string;
}

/**
 * An exam belonging to a subject, containing questions.
 * Backend endpoint: GET /api/subjects/:subjectId/exams (list)
 * Backend endpoint: GET /api/exams/:examId (detail with questions)
 */
export interface Exam {
  id: string;
  subject_id: string;
  title: string;
  question_count: number;
  questions: Question[];
}

// -- API Response Wrapper --

/**
 * Standard API response wrapper from FastAPI backend.
 * All endpoints should return data in this format.
 */
export interface ApiResponse<T> {
  success: boolean;
  data: T;
  message?: string;
}

// -- Upload Response --

/**
 * Response from PDF upload endpoint.
 * Backend endpoint: POST /api/upload
 */
export interface UploadResponse {
  exam_id: string;
  title: string;
  question_count: number;
}

// -- User Answer Types --

/** User's answer for a single question, varies by question type */
export interface UserAnswer {
  question_id: number;
  /** For mcp: selected option labels (e.g. ["A", "C"]) */
  selected_options?: string[];
  /** For true_false_ng: "true" | "false" | "ng" */
  true_false_value?: string;
  /** For matching: map of description index to selected answer */
  matching_selections?: Record<number, string>;
  /** For fill_in: array of user-entered text values */
  fill_in_values?: string[];
}

// -- Quiz Session State --

/** Current state of a quiz session */
export interface QuizState {
  exam: Exam | null;
  current_index: number;
  answers: Record<number, UserAnswer>;
  is_submitted: boolean;
  start_time: number | null;
}

// -- Statistics & Attempt Logs --

export interface QuestionResult {
  question_id: number;
  type: QuestionType;
  is_correct: boolean;
}

/** Represents a single logged quiz attempt */
export interface QuizAttempt {
  id: string;
  exam_id: string;
  exam_title: string;
  subject_id: string;
  subject_name: string;
  score: number; // percentage (0 - 100)
  total_questions: number;
  correct_answers_count: number;
  time_spent: number; // in seconds
  completed_at: number; // timestamp in ms
  answers: Record<number, UserAnswer>;
  question_results: QuestionResult[];
}

/** Correct ratio statistics for a specific question type */
export interface QuestionTypeStats {
  correct: number;
  total: number;
  percentage: number;
}

/** Aggegated statistics across all quiz attempts */
export interface QuizStats {
  totalAttempts: number;
  avgScore: number;
  totalTimeSpent: number; // in seconds
  byType: Record<string, QuestionTypeStats>;
  scoreProgress: { date: string; score: number; examTitle: string }[];
}
