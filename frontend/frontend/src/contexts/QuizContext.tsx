/**
 * Quiz context for managing quiz session state.
 *
 * Provides centralized state management for:
 * - Current exam data
 * - Active question index
 * - User answers for all questions
 * - Submission status
 * - Timer tracking
 */

import {
  createContext,
  useContext,
  useReducer,
  useCallback,
  type ReactNode,
} from "react";

import type { Exam, UserAnswer, QuizState } from "../types/types";
import { checkQuestionCorrectness, StatsService } from "../services/stats";
import { MOCK_SUBJECTS } from "../data/mockData";

// -- Actions --

type QuizAction =
  | { type: "START_QUIZ"; exam: Exam }
  | { type: "SET_QUESTION_INDEX"; index: number }
  | { type: "SET_ANSWER"; questionId: number; answer: UserAnswer }
  | { type: "SUBMIT_QUIZ" }
  | { type: "RESET_QUIZ" };

// -- Initial State --

const initialState: QuizState = {
  exam: null,
  current_index: 0,
  answers: {},
  is_submitted: false,
  start_time: null,
};

// -- Reducer --

function quizReducer(state: QuizState, action: QuizAction): QuizState {
  switch (action.type) {
    case "START_QUIZ":
      return {
        ...initialState,
        exam: action.exam,
        start_time: Date.now(),
      };

    case "SET_QUESTION_INDEX":
      return {
        ...state,
        current_index: action.index,
      };

    case "SET_ANSWER":
      return {
        ...state,
        answers: {
          ...state.answers,
          [action.questionId]: action.answer,
        },
      };

    case "SUBMIT_QUIZ":
      return {
        ...state,
        is_submitted: true,
      };

    case "RESET_QUIZ":
      return initialState;

    default:
      return state;
  }
}

// -- Context --

interface QuizContextValue {
  state: QuizState;
  startQuiz: (exam: Exam) => void;
  goToQuestion: (index: number) => void;
  setAnswer: (questionId: number, answer: UserAnswer) => void;
  submitQuiz: () => void;
  resetQuiz: () => void;
  /** Navigate to the next question. Returns false if already at last question. */
  nextQuestion: () => boolean;
  /** Navigate to the previous question. Returns false if already at first question. */
  prevQuestion: () => boolean;
  /** Get the current question object, or null if no exam is loaded. */
  currentQuestion: ReturnType<typeof getCurrentQuestion>;
  /** Count of answered questions */
  answeredCount: number;
}

function getCurrentQuestion(state: QuizState) {
  if (!state.exam) return null;
  return state.exam.questions[state.current_index] ?? null;
}

const QuizContext = createContext<QuizContextValue | undefined>(undefined);

export function QuizProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(quizReducer, initialState);

  const startQuiz = useCallback((exam: Exam) => {
    dispatch({ type: "START_QUIZ", exam });
  }, []);

  const goToQuestion = useCallback((index: number) => {
    dispatch({ type: "SET_QUESTION_INDEX", index });
  }, []);

  const setAnswer = useCallback((questionId: number, answer: UserAnswer) => {
    dispatch({ type: "SET_ANSWER", questionId, answer });
  }, []);

  const submitQuiz = useCallback(() => {
    dispatch({ type: "SUBMIT_QUIZ" });
  }, []);

  const resetQuiz = useCallback(() => {
    dispatch({ type: "RESET_QUIZ" });
  }, []);

  const nextQuestion = useCallback((): boolean => {
    if (!state.exam) return false;
    const nextIdx = state.current_index + 1;
    if (nextIdx >= state.exam.questions.length) return false;
    dispatch({ type: "SET_QUESTION_INDEX", index: nextIdx });
    return true;
  }, [state.exam, state.current_index]);

  const prevQuestion = useCallback((): boolean => {
    if (state.current_index <= 0) return false;
    dispatch({ type: "SET_QUESTION_INDEX", index: state.current_index - 1 });
    return true;
  }, [state.current_index]);

  const currentQuestion = getCurrentQuestion(state);
  const answeredCount = Object.keys(state.answers).length;

  return (
    <QuizContext.Provider
      value={{
        state,
        startQuiz,
        goToQuestion,
        setAnswer,
        submitQuiz,
        resetQuiz,
        nextQuestion,
        prevQuestion,
        currentQuestion,
        answeredCount,
      }}
    >
      {children}
    </QuizContext.Provider>
  );
}

/**
 * Hook to access quiz state and actions.
 * Must be used within QuizProvider.
 */
export function useQuiz(): QuizContextValue {
  const context = useContext(QuizContext);
  if (!context) {
    throw new Error("useQuiz must be used within a QuizProvider");
  }
  return context;
}
