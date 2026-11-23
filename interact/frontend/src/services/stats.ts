import type {
  Question,
  UserAnswer,
  QuizAttempt,
  QuizStats,
  QuestionType,
} from "../types/types";

const STORAGE_PREFIX = "quiz_attempts_history_v2";

let activeUserId: string | null = null;

function storageKey(): string | null {
  if (!activeUserId) {
    return null;
  }

  return `${STORAGE_PREFIX}:${activeUserId}`;
}

export function checkQuestionCorrectness(
    question: Question,
    answer: UserAnswer | undefined,
): boolean {
  if (!answer) {
    return false;
  }

  switch (question.type) {
    case "mcp": {
      const selected = answer.selected_options || [];

      const correct = question.user_confirmed_answer
          ? [question.user_confirmed_answer]
          : question.option_answer || [];

      if (selected.length !== correct.length) {
        return false;
      }

      return selected.every(
          (value) => correct.includes(value),
      );
    }

    case "true_false_ng": {
      const value = answer.true_false_value || "";

      const correct =
          question.option_answer?.[0] || "";

      return value === correct;
    }

    case "matching": {
      const selections =
          answer.matching_selections || {};

      const matches = question.matches || [];

      const validMatches = matches.filter(
          (match) => match.description,
      );

      if (validMatches.length === 0) {
        return false;
      }

      return validMatches.every(
          (match, index) =>
              selections[index] === match.answer,
      );
    }

    case "fill_in": {
      const value = (
          answer.fill_in_values?.[0] || ""
      ).toLowerCase().trim();

      return (
          question.fill_in_answers || []
      ).some(
          (candidate) =>
              candidate.toLowerCase().trim() === value,
      );
    }

    default:
      return false;
  }
}

export const StatsService = {
  setUserId(userId: string | null): void {
    activeUserId = userId;
  },

  saveAttempt(
      attemptData: Omit<
          QuizAttempt,
          "id" | "completed_at"
      >,
  ): QuizAttempt {
    const key = storageKey();

    if (!key) {
      throw new Error(
          "Cannot save statistics without an authenticated user.",
      );
    }

    const attempts = this.getAttempts();

    const newAttempt: QuizAttempt = {
      ...attemptData,
      id:
          `attempt_${Date.now()}_` +
          Math.random().toString(36).slice(2, 9),
      completed_at: Date.now(),
    };

    attempts.unshift(newAttempt);

    localStorage.setItem(
        key,
        JSON.stringify(attempts),
    );

    return newAttempt;
  },

  getAttempts(): QuizAttempt[] {
    const key = storageKey();

    if (!key) {
      return [];
    }

    try {
      const stored = localStorage.getItem(key);

      if (!stored) {
        return [];
      }

      const parsed: unknown = JSON.parse(stored);

      if (!Array.isArray(parsed)) {
        return [];
      }

      return (parsed as QuizAttempt[]).sort(
          (a, b) => b.completed_at - a.completed_at,
      );
    } catch (error) {
      console.error(
          "Could not read local quiz history:",
          error,
      );

      return [];
    }
  },

  clearHistory(): void {
    const key = storageKey();

    if (key) {
      localStorage.removeItem(key);
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

    if (attempts.length === 0) {
      return stats;
    }

    let totalScore = 0;

    attempts.forEach((attempt) => {
      totalScore += attempt.score;
      stats.totalTimeSpent += attempt.time_spent;

      attempt.question_results?.forEach((result) => {
        const type = result.type;

        if (!stats.byType[type]) {
          return;
        }

        stats.byType[type].total += 1;

        if (result.is_correct) {
          stats.byType[type].correct += 1;
        }
      });
    });

    stats.avgScore = Math.round(
        totalScore / attempts.length,
    );

    Object.keys(stats.byType).forEach((key) => {
      const type = key as QuestionType;
      const item = stats.byType[type];

      if (item.total > 0) {
        item.percentage = Math.round(
            (item.correct / item.total) * 100,
        );
      }
    });

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
