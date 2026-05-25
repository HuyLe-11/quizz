import type { Question, UserAnswer, QuizAttempt, QuizStats, QuestionType } from "../types/types";

const LOCAL_STORAGE_KEY = "quiz_attempts_history";

/**
 * Check if the user's answer to a single question is correct.
 * This is used during quiz submission to calculate and log scores.
 * 
 * Future Backend Architecture Comment:
 * When migrating to a FastAPI backend, this evaluation logic should move to the backend
 * to prevent users from cheating by inspecting client-side correct answers.
 * The endpoint POST `/api/exams/{exam_id}/submit` will receive the user's answers,
 * perform this verification against the database records, and save the result.
 */
export function checkQuestionCorrectness(question: Question, answer: UserAnswer | undefined): boolean {
  if (!answer) return false;

  switch (question.type) {
    case "mcp": {
      const selected = answer.selected_options || [];
      const correct = question.option_answer || [];
      if (selected.length !== correct.length) return false;
      // All selected options must be in the correct answers list
      return selected.every((val) => correct.includes(val));
    }

    case "true_false_ng": {
      const val = answer.true_false_value || "";
      const correct = question.option_answer?.[0] || "";
      return val === correct;
    }

    case "matching": {
      const selections = answer.matching_selections || {};
      const matches = question.matches || [];
      const validMatches = matches.filter((m) => m.description);
      if (validMatches.length === 0) return false;
      // All valid descriptions must match their correct answers
      return validMatches.every((match, idx) => selections[idx] === match.answer);
    }

    case "fill_in": {
      const val = (answer.fill_in_values?.[0] || "").toLowerCase().trim();
      const correctList = question.fill_in_answers || [];
      // Input matches any of the accepted fill-in answers
      return correctList.some((ans) => ans.toLowerCase().trim() === val);
    }

    default:
      return false;
  }
}

/**
 * Service to manage quiz attempt logs and analytics.
 * Currently uses localStorage, designed to be swapped with FastAPI endpoints.
 * 
 * Future Backend Migration Guide:
 * 1. REST Endpoints:
 *    - POST `/api/attempts` -> Save a new attempt to the DB.
 *    - GET `/api/attempts` -> Retrieve attempt history (with pagination, e.g., `?limit=10&offset=0`).
 *    - GET `/api/attempts/stats` -> Fetch calculated stats directly from DB using aggregation queries.
 * 
 * 2. Database Schema (PostgreSQL Example):
 *    - `attempts` table: id (UUID), user_id (FK), exam_id (FK), score (INT), correct_count (INT), total_questions (INT), time_spent (INT), completed_at (TIMESTAMP)
 *    - `question_results` table: id (UUID), attempt_id (FK), question_id (FK), type (VARCHAR), is_correct (BOOLEAN)
 * 
 * 3. Aggregation Queries (SQL Example):
 *    - Avg Score: `SELECT AVG(score) FROM attempts WHERE user_id = :user_id`
 *    - Strengths & Weaknesses by Type:
 *      `SELECT type, SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct, COUNT(*) as total 
 *       FROM question_results qr JOIN attempts a ON qr.attempt_id = a.id 
 *       WHERE a.user_id = :user_id GROUP BY type`
 */
export const StatsService = {
  /**
   * Save a new quiz attempt log.
   */
  saveAttempt(attemptData: Omit<QuizAttempt, "id" | "completed_at">): QuizAttempt {
    const attempts = this.getAttempts();
    
    const newAttempt: QuizAttempt = {
      ...attemptData,
      id: `attempt_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`,
      completed_at: Date.now(),
    };

    attempts.unshift(newAttempt); // Add to the beginning (newest first)
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(attempts));
    return newAttempt;
  },

  /**
   * Get all past attempts, sorted by date (newest first).
   */
  getAttempts(): QuizAttempt[] {
    try {
      const stored = localStorage.getItem(LOCAL_STORAGE_KEY);
      if (!stored) return [];
      const parsed = JSON.parse(stored) as QuizAttempt[];
      // Ensure they are sorted by date descending
      return parsed.sort((a, b) => b.completed_at - a.completed_at);
    } catch (e) {
      console.error("Error reading quiz attempts from localStorage", e);
      return [];
    }
  },

  /**
   * Clear all quiz history.
   */
  clearHistory(): void {
    localStorage.removeItem(LOCAL_STORAGE_KEY);
  },

  /**
   * Compute aggregated statistics across all attempts.
   */
  getOverallStats(): QuizStats {
    const attempts = this.getAttempts();

    const stats: QuizStats = {
      totalAttempts: attempts.length,
      avgScore: 0,
      totalTimeSpent: 0,
      byType: {
        mcp: { correct: 0, total: 0, percentage: 0 },
        true_false_ng: { correct: 0, total: 0, percentage: 0 },
        matching: { correct: 0, total: 0, percentage: 0 },
        fill_in: { correct: 0, total: 0, percentage: 0 },
      },
      scoreProgress: [],
    };

    if (attempts.length === 0) return stats;

    let totalScoreSum = 0;

    attempts.forEach((attempt) => {
      totalScoreSum += attempt.score;
      stats.totalTimeSpent += attempt.time_spent;

      // Aggregate question types correctness
      if (attempt.question_results) {
        attempt.question_results.forEach((res) => {
          const type = res.type;
          if (stats.byType[type]) {
            stats.byType[type].total += 1;
            if (res.is_correct) {
              stats.byType[type].correct += 1;
            }
          }
        });
      }
    });

    stats.avgScore = Math.round(totalScoreSum / attempts.length);

    // Compute percentages for question types
    Object.keys(stats.byType).forEach((key) => {
      const typeKey = key as QuestionType;
      const typeStat = stats.byType[typeKey];
      if (typeStat.total > 0) {
        typeStat.percentage = Math.round((typeStat.correct / typeStat.total) * 100);
      }
    });

    // Compute chronological score progress (oldest first for line chart)
    stats.scoreProgress = attempts
      .slice()
      .reverse()
      .map((attempt) => {
        const dateStr = new Date(attempt.completed_at).toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });
        return {
          date: dateStr,
          score: attempt.score,
          examTitle: attempt.exam_title,
        };
      });

    return stats;
  },
};
