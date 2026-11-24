import {
    useReducer,
    useCallback,
    useEffect,
    useState,
    type ReactNode,
} from "react";

import type {
    Exam,
    Question,
    QuizState,
    UserAnswer,
} from "../types/types";

import { QuizContext } from "./QuizContext";
import { ThemeContext, type Theme } from "./ThemeContext";

import {
    checkQuestionCorrectness,
    isQuestionAnswered,
    StatsService,
} from "../services/stats";

import { MOCK_SUBJECTS } from "../data/mockData";
import { saveQuizAttempt } from "../services/api";

type QuizAction =
    | { type: "START_QUIZ"; exam: Exam; startTime: number }
    | { type: "SET_QUESTION_INDEX"; index: number }
    | { type: "SET_ANSWER"; questionId: number; answer: UserAnswer }
    | { type: "SUBMIT_QUIZ" }
    | {
    type: "UPDATE_QUESTION";
    examId: string;
    recordId: string;
    patch: Partial<Question>;
}
    | { type: "RESET_QUIZ" };

const initialState: QuizState = {
    exam: null,
    current_index: 0,
    answers: {},
    is_submitted: false,
    start_time: null,
};

function quizReducer(
    state: QuizState,
    action: QuizAction,
): QuizState {
    switch (action.type) {
        case "START_QUIZ":
            return {
                ...initialState,
                exam: action.exam,
                start_time: action.startTime,
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

        case "UPDATE_QUESTION":
            if (!state.exam || state.exam.id !== action.examId) {
                return state;
            }

            return {
                ...state,
                exam: {
                    ...state.exam,
                    questions: state.exam.questions.map((q) =>
                        q.question_record_id === action.recordId
                            ? { ...q, ...action.patch }
                            : q,
                    ),
                },
            };

        case "RESET_QUIZ":
            return initialState;
    }
}

export function QuizProvider({
                                 children,
                             }: {
    children: ReactNode;
}) {
    const [state, dispatch] = useReducer(
        quizReducer,
        initialState,
    );

    const startQuiz = useCallback((exam: Exam) => {
        dispatch({
            type: "START_QUIZ",
            exam,
            startTime: Date.now(),
        });
    }, []);

    const goToQuestion = useCallback((index: number) => {
        dispatch({
            type: "SET_QUESTION_INDEX",
            index,
        });
    }, []);

    const setAnswer = useCallback(
        (questionId: number, answer: UserAnswer) => {
            dispatch({
                type: "SET_ANSWER",
                questionId,
                answer,
            });
        },
        [],
    );

    const updateQuestion = useCallback(
        (
            examId: string,
            recordId: string,
            patch: Partial<Question>,
        ) => {
            dispatch({
                type: "UPDATE_QUESTION",
                examId,
                recordId,
                patch,
            });
        },
        [],
    );

    const resetQuiz = useCallback(() => {
        dispatch({ type: "RESET_QUIZ" });
    }, []);

    const submitQuiz = useCallback(() => {
        if (!state.exam || state.is_submitted) {
            return;
        }

        const exam = state.exam;

        const question_results = exam.questions.map((q) => ({
            question_id: q.question_id,
            type: q.type,
            is_correct: checkQuestionCorrectness(
                q,
                state.answers[q.question_id],
            ),
        }));

        const correct_answers_count =
            question_results.filter((q) => q.is_correct).length;

        const total_questions = question_results.length;

        const score = total_questions
            ? Math.round(
                (100 * correct_answers_count) / total_questions,
            )
            : 0;

        const time_spent = state.start_time
            ? Math.round((Date.now() - state.start_time) / 1000)
            : 0;

        const subject = MOCK_SUBJECTS.find(
            (item) => item.id === exam.subject_id,
        );

        const result = {
            score,
            total_questions,
            correct_answers_count,
            time_spent,
            answers: state.answers,
            question_results,
        };

        StatsService.saveAttempt({
            ...result,
            exam_id: exam.id,
            exam_title: exam.title,
            subject_id: exam.subject_id,
            subject_name: subject?.name ?? "Chưa xác định",
        });

        if (!exam.id.startsWith("exam-")) {
            void saveQuizAttempt(exam.id, {
                ...result,
                completed_at: Date.now(),
            }).catch(() => undefined);
        }

        dispatch({ type: "SUBMIT_QUIZ" });
    }, [state]);

    const nextQuestion = useCallback(() => {
        if (
            !state.exam ||
            state.current_index + 1 >= state.exam.questions.length
        ) {
            return false;
        }

        dispatch({
            type: "SET_QUESTION_INDEX",
            index: state.current_index + 1,
        });

        return true;
    }, [state.exam, state.current_index]);

    const prevQuestion = useCallback(() => {
        if (state.current_index <= 0) {
            return false;
        }

        dispatch({
            type: "SET_QUESTION_INDEX",
            index: state.current_index - 1,
        });

        return true;
    }, [state.current_index]);

    return (
        <QuizContext.Provider
            value={{
                state,
                startQuiz,
                goToQuestion,
                setAnswer,
                submitQuiz,
                resetQuiz,
                updateQuestion,
                nextQuestion,
                prevQuestion,
                currentQuestion:
                    state.exam?.questions[state.current_index] ?? null,
                answeredCount:
                Object.values(state.answers)
                    .filter(isQuestionAnswered).length,
            }}
        >
            {children}
        </QuizContext.Provider>
    );
}

const THEME_KEY = "quiz-app-theme";

function getInitialTheme(): Theme {
    const stored = localStorage.getItem(THEME_KEY);

    if (stored === "light" || stored === "dark") {
        return stored;
    }

    return window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
}

export function ThemeProvider({
                                  children,
                              }: {
    children: ReactNode;
}) {
    const [theme, setTheme] =
        useState<Theme>(getInitialTheme);

    useEffect(() => {
        document.documentElement.classList.remove(
            "light",
            "dark",
        );

        document.documentElement.classList.add(theme);

        localStorage.setItem(THEME_KEY, theme);
    }, [theme]);

    const toggleTheme = useCallback(() => {
        setTheme((prev) => prev === "light" ? "dark" : "light");
    }, []);

    return (
        <ThemeContext.Provider value={{ theme, toggleTheme }}>
            {children}
        </ThemeContext.Provider>
    );
}