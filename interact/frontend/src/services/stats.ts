import type {
  Question,
  UserAnswer,
  QuizAttempt,
  QuizStats,
  QuestionType,
} from "../types/types";

const LOCAL_KEY = "quiz_attempts_history";
const USER_PREFIX = "quiz_attempts_history_v2";

let activeKey: string | null = null;

export function isQuestionAnswered(
    answer: UserAnswer | undefined,
): boolean {
  if (!answer) return false;

  return (
      (answer.selected_options?.length ?? 0) > 0 ||
      !!answer.true_false_value ||
      Object.values(answer.matching_selections ?? {}).some(Boolean) ||
      (answer.fill_in_values ?? []).some(
          (value) => value.trim() !== "",
      )
  );
}

export function checkQuestionCorrectness(
    question: Question,
    answer: UserAnswer | undefined,
): boolean {
  if (!isQuestionAnswered(answer) || !answer) {
    return false;
  }

  switch (question.type) {
    case "mcp": {
      const selected = new Set(
          answer.selected_options ?? [],
      );

      const correct = new Set(
          question.user_confirmed_answer
              ? [question.user_confirmed_answer]
              : question.option_answer ?? [],
      );

      return (
          correct.size > 0 &&
          selected.size === correct.size &&
          [...selected].every(
              (value) => correct.has(value),
          )
      );
    }

    case "true_false_ng": {
      const correct = question.option_answer?.[0];

      return (
          !!correct &&
          answer.true_false_value === correct
      );
    }

    case "matching": {
      const selections =
          answer.matching_selections ?? {};

      const matches = (question.matches ?? []).filter(
          (m) => m.description,
      );

      return (
          matches.length > 0 &&
          matches.every(
              (m, index) =>
                  !!m.answer &&
                  selections[index] === m.answer,
          )
      );
    }

    case "fill_in": {
      const value = (
          answer.fill_in_values?.[0] ?? ""
      ).trim().toLowerCase();

      return (
          !!value &&
          (question.fill_in_answers ?? []).some(
              (item) => item.trim().toLowerCase() === value,
          )
      );
    }

    default:
      return false;
  }
}

export const StatsService = {
  // Only the standalone SQLite App enables this mode.
  enableLocalHistory(): void {
    activeKey = LOCAL_KEY;
  },

  setUserId(userId: string | null): void {
    activeKey = userId
        ? `${USER_PREFIX}:${userId}`
        : null;
  },

  saveAttempt(
      data: Omit<QuizAttempt, "id" | "completed_at">,
  ): QuizAttempt {
    const key = activeKey;

    if (!key) {
      throw new Error(
          "Cannot save statistics without an authenticated user.",
      );
    }

    const attempts = this.getAttempts();

    const attempt: QuizAttempt = {
      ...data,
      id:
          `attempt_${Date.now()}_` +
          Math.random().toString(36).slice(2, 9),
      completed_at: Date.now(),
    };

    localStorage.setItem(
        key,
        JSON.stringify([attempt, ...attempts]),
    );

    return attempt;
  },

  getAttempts(): QuizAttempt[] {
    if (!activeKey) return [];

    try {
      const parsed: unknown = JSON.parse(
          localStorage.getItem(activeKey) ?? "[]",
      );

      return Array.isArray(parsed)
          ? (parsed as QuizAttempt[]).sort(
              (a, b) => b.completed_at - a.completed_at,
          )
          : [];
    } catch (error) {
      console.error(
          "Could not read quiz history:",
          error,
      );

      return [];
    }
  },

  clearHistory(): void {
    if (activeKey) {
      localStorage.removeItem(activeKey);
    }
  },

  getOverallStats(): QuizStats {
    const attempts = this.getAttempts();

    const stats: QuizStats = {
      totalAttempts: attempts.length,
      avgScore: 0,
      totalTimeSpent: 0,

      byType: {
        mcp: {
          correct: 0,
          total: 0,
          percentage: 0,
        },
        true_false_ng: {
          correct: 0,
          total: 0,
          percentage: 0,
        },
        matching: {
          correct: 0,
          total: 0,
          percentage: 0,
        },
        fill_in: {
          correct: 0,
          total: 0,
          percentage: 0,
        },
      },

      scoreProgress: [],
    };

    if (!attempts.length) return stats;

    let totalScore = 0;

    for (const attempt of attempts) {
      totalScore += attempt.score;
      stats.totalTimeSpent += attempt.time_spent;

      for (const result of attempt.question_results ?? []) {
        const item = stats.byType[result.type];

        if (!item) continue;

        item.total++;

        if (result.is_correct) {
          item.correct++;
        }
      }
    }

    stats.avgScore = Math.round(
        totalScore / attempts.length,
    );

    for (const key of Object.keys(
        stats.byType,
    ) as QuestionType[]) {
      const item = stats.byType[key];

      if (item.total) {
        item.percentage = Math.round(
            (100 * item.correct) / item.total,
        );
      }
    }

    stats.scoreProgress = attempts
        .slice()
        .reverse()
        .map((attempt) => ({
          date: new Date(
              attempt.completed_at,
          ).toLocaleDateString("vi-VN", {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          }),
          score: attempt.score,
          examTitle: attempt.exam_title,
        }));

    return stats;
  },
};