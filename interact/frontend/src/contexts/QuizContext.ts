import { createContext, useContext } from "react";
import type { Exam, Question, QuizState, UserAnswer } from "../types/types";

interface QuizContextValue {
    state: QuizState;
    startQuiz: (exam: Exam) => void;
    goToQuestion: (index: number) => void;
    setAnswer: (questionId: number, answer: UserAnswer) => void;
    submitQuiz: () => void;
    resetQuiz: () => void;
    updateQuestion: (
        examId: string,
        recordId: string,
        patch: Partial<Question>,
    ) => void;
    nextQuestion: () => boolean;
    prevQuestion: () => boolean;
    currentQuestion: Question | null;
    answeredCount: number;
}

export const QuizContext =
    createContext<QuizContextValue | undefined>(undefined);

export function useQuiz(): QuizContextValue {
    const context = useContext(QuizContext);

    if (!context) {
        throw new Error("useQuiz must be used within a QuizProvider");
    }

    return context;
}