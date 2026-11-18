/**
 * Application-level React providers.
 *
 * Keeps provider components separate from context
 * definitions and custom hooks for React Fast Refresh.
 */

import {
    useReducer,
    useCallback,
    useEffect,
    useState,
    type ReactNode,
} from "react";

import type {
    Exam,
    UserAnswer,
    QuizState,
} from "../types/types";

import {
    checkQuestionCorrectness,
    StatsService,
} from "../services/stats";

import { MOCK_SUBJECTS } from "../data/mockData";
import { saveQuizAttempt } from "../services/api";

import { QuizContext } from "./QuizContext";

import {
    ThemeContext,
    type Theme,
} from "./ThemeContext";

// ==================================================
// Quiz Provider
// ==================================================

type QuizAction =
    | {
    type: "START_QUIZ";
    exam: Exam;
    startTime: number;
}
    | {
    type: "SET_QUESTION_INDEX";
    index: number;
}
    | {
    type: "SET_ANSWER";
    questionId: number;
    answer: UserAnswer;
}
    | {
    type: "SUBMIT_QUIZ";
}
    | {
    type: "RESET_QUIZ";
};

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

        case "RESET_QUIZ":
            return initialState;

        default:
            return state;
    }
}

function getCurrentQuestion(
    state: QuizState,
) {
    if (!state.exam) {
        return null;
    }

    return (
        state.exam.questions[state.current_index] ??
        null
    );
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

    const startQuiz = useCallback(
        (exam: Exam) => {
            dispatch({
                type: "START_QUIZ",
                exam,
                startTime: Date.now(),
            });
        },
        [],
    );

    const goToQuestion = useCallback(
        (index: number) => {
            dispatch({
                type: "SET_QUESTION_INDEX",
                index,
            });
        },
        [],
    );

    const setAnswer = useCallback(
        (
            questionId: number,
            answer: UserAnswer,
        ) => {
            dispatch({
                type: "SET_ANSWER",
                questionId,
                answer,
            });
        },
        [],
    );

    const submitQuiz = useCallback(() => {
        if (
            !state.exam ||
            state.is_submitted
        ) {
            return;
        }

        const question_results =
            state.exam.questions.map(
                (question) => ({
                    question_id: question.question_id,
                    type: question.type,
                    is_correct: checkQuestionCorrectness(
                        question,
                        state.answers[question.question_id],
                    ),
                }),
            );

        const correct_answers_count =
            question_results.filter(
                (item) => item.is_correct,
            ).length;

        const total_questions =
            question_results.length;

        const score =
            total_questions > 0
                ? Math.round(
                    (correct_answers_count /
                        total_questions) *
                    100,
                )
                : 0;

        const time_spent = state.start_time
            ? Math.round(
                (Date.now() - state.start_time) /
                1000,
            )
            : 0;

        const subject = MOCK_SUBJECTS.find(
            (item) =>
                item.id === state.exam?.subject_id,
        );

        // Preserve the existing local attempt history.

        StatsService.saveAttempt({
            exam_id: state.exam.id,
            exam_title: state.exam.title,
            subject_id: state.exam.subject_id,
            subject_name:
                subject?.name ?? "Chưa xác định",
            score,
            total_questions,
            correct_answers_count,
            time_spent,
            answers: state.answers,
            question_results,
        });

        // Preserve backend persistence for uploaded exams.

        if (!state.exam.id.startsWith("exam-")) {
            void saveQuizAttempt(
                state.exam.id,
                {
                    score,
                    total_questions,
                    correct_answers_count,
                    time_spent,
                    answers: state.answers,
                    question_results,
                    completed_at: Date.now(),
                },
            ).catch(() => undefined);
        }

        dispatch({
            type: "SUBMIT_QUIZ",
        });
    }, [state]);

    const resetQuiz = useCallback(() => {
        dispatch({
            type: "RESET_QUIZ",
        });
    }, []);

    const nextQuestion = useCallback(
        (): boolean => {
            if (!state.exam) {
                return false;
            }

            const nextIdx =
                state.current_index + 1;

            if (
                nextIdx >=
                state.exam.questions.length
            ) {
                return false;
            }

            dispatch({
                type: "SET_QUESTION_INDEX",
                index: nextIdx,
            });

            return true;
        },
        [state.exam, state.current_index],
    );

    const prevQuestion = useCallback(
        (): boolean => {
            if (state.current_index <= 0) {
                return false;
            }

            dispatch({
                type: "SET_QUESTION_INDEX",
                index: state.current_index - 1,
            });

            return true;
        },
        [state.current_index],
    );

    const currentQuestion =
        getCurrentQuestion(state);

    const answeredCount =
        Object.keys(state.answers).length;

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

// ==================================================
// Theme Provider
// ==================================================

const STORAGE_KEY = "quiz-app-theme";

/**
 * Read the initial theme from local storage
 * or the operating system preference.
 */
function getInitialTheme(): Theme {
    const stored =
        localStorage.getItem(STORAGE_KEY);

    if (
        stored === "light" ||
        stored === "dark"
    ) {
        return stored;
    }

    if (
        window.matchMedia(
            "(prefers-color-scheme: dark)",
        ).matches
    ) {
        return "dark";
    }

    return "light";
}

export function ThemeProvider({
                                  children,
                              }: {
    children: ReactNode;
}) {
    const [theme, setTheme] =
        useState<Theme>(getInitialTheme);

    // Synchronize React state with the DOM and localStorage.

    useEffect(() => {
        const root =
            document.documentElement;

        root.classList.remove(
            "light",
            "dark",
        );

        root.classList.add(theme);

        localStorage.setItem(
            STORAGE_KEY,
            theme,
        );
    }, [theme]);

    const toggleTheme = useCallback(() => {
        setTheme((previousTheme) =>
            previousTheme === "light"
                ? "dark"
                : "light",
        );
    }, []);

    return (
        <ThemeContext.Provider
            value={{
                theme,
                toggleTheme,
            }}
        >
            {children}
        </ThemeContext.Provider>
    );
}
