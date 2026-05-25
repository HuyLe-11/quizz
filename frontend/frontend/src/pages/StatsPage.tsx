import { Link } from "react-router-dom";
import { StatsService } from "../services/stats";
import type { QuizStats, QuestionType } from "../types/types";

const questionTypeLabels: Record<QuestionType, string> = {
  mcp: "Multiple Choice",
  true_false_ng: "True / False",
  matching: "Matching",
  fill_in: "Fill-in",
};

function formatTime(seconds: number) {
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  return `${hours > 0 ? `${hours}h ` : ""}${mins}m` || "0m";
}

function scoreColor(score: number) {
  if (score >= 80) return "#34d399";
  if (score >= 60) return "#f59e0b";
  return "#f87171";
}

function badgeTone(score: number) {
  if (score >= 80) return "bg-emerald-500/15 text-emerald-300";
  if (score >= 60) return "bg-amber-500/15 text-amber-300";
  return "bg-rose-500/15 text-rose-300";
}

function formatDate(timestamp: number) {
  return new Date(timestamp).toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function buildStepPath(scores: number[]) {
  if (scores.length === 0) return "";
  const points = scores.map((value, index) => {
    const x = 40 + index * 120;
    const y = 120 - Math.round((value / 100) * 80);
    return { x, y };
  });

  let d = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i += 1) {
    d += ` L ${points[i].x} ${points[i].y}`;
  }
  return d;
}

export function StatsPage() {
  const attempts = StatsService.getAttempts();
  const stats: QuizStats = StatsService.getOverallStats();

  const totalCorrect = Object.values(stats.byType).reduce((sum, item) => sum + item.correct, 0);
  const totalQuestions = Object.values(stats.byType).reduce((sum, item) => sum + item.total, 0);
  const correctRatio = totalQuestions > 0 ? Math.round((totalCorrect / totalQuestions) * 100) : 0;

  const scoreProgress = stats.scoreProgress;
  const pathD = buildStepPath(scoreProgress.map((item) => item.score));

  return (
    <div className="max-w-7xl mx-auto px-6 py-10 min-h-[calc(100vh-4rem)] text-slate-100 animate-fade-in">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between mb-8">
        <div>
          <p className="text-sm uppercase tracking-[0.3em] text-slate-400">Study dashboard</p>
          <h1 className="mt-3 text-4xl font-semibold text-white">Performance insights</h1>
          <p className="mt-2 max-w-2xl text-slate-400">
            Review your recent quiz history, monitor trends, and identify your strongest and weakest question types.
          </p>
        </div>

        <Link
          to="/"
          className="inline-flex items-center justify-center rounded-full border border-slate-700 bg-white/5 px-5 py-3 text-sm font-semibold text-slate-100 transition hover:border-slate-500 hover:bg-white/10"
        >
          Take another quiz
        </Link>
      </div>

      {stats.totalAttempts === 0 ? (
        <div className="rounded-[2rem] border border-slate-800 bg-white/5 p-12 text-center shadow-[0_25px_120px_-40px_rgba(15,23,42,0.8)]">
          <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-3xl bg-slate-800/60 text-3xl">📘</div>
          <h2 className="text-2xl font-semibold text-white mb-2">No quizzes recorded yet</h2>
          <p className="text-slate-400 mb-6">
            Start your first quiz to populate this dashboard with scored attempts, progress charts, and learning insights.
          </p>
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-full bg-[var(--color-primary)] px-6 py-3 text-sm font-semibold text-slate-900 transition hover:opacity-90"
          >
            Explore quizzes
          </Link>
        </div>
      ) : (
        <>
          <div className="grid gap-5 xl:grid-cols-[1.5fr_1fr_1fr_1fr]">
            <div className="group rounded-[2rem] border border-slate-800 bg-white/5 p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.8)] backdrop-blur-xl">
              <p className="text-sm uppercase tracking-[0.25em] text-slate-500">Total Quizzes Taken</p>
              <p className="mt-4 text-4xl font-semibold text-white">{stats.totalAttempts}</p>
              <p className="mt-2 text-sm text-slate-400">Completed quiz attempts since you started learning.</p>
            </div>

            <div className="group rounded-[2rem] border border-slate-800 bg-white/5 p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.8)] backdrop-blur-xl flex flex-col items-center justify-center">
              <div
                className="relative flex h-36 w-36 items-center justify-center rounded-full bg-slate-900/50"
                style={{
                  background: `conic-gradient(${scoreColor(stats.avgScore)} ${stats.avgScore * 3.6}deg, rgba(15, 23, 42, 0.6) 0deg)`,
                }}
              >
                <div className="flex h-28 w-28 items-center justify-center rounded-full bg-slate-950 text-center">
                  <p className="text-3xl font-semibold text-white">{stats.avgScore}%</p>
                </div>
              </div>
              <p className="mt-5 text-sm uppercase tracking-[0.25em] text-slate-500">Average Score</p>
              <p className="mt-3 text-sm text-slate-400">A rolling view of your performance across all attempts.</p>
            </div>

            <div className="group rounded-[2rem] border border-slate-800 bg-white/5 p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.8)] backdrop-blur-xl">
              <p className="text-sm uppercase tracking-[0.25em] text-slate-500">Total Study Time</p>
              <p className="mt-4 text-4xl font-semibold text-white">{formatTime(stats.totalTimeSpent)}</p>
              <p className="mt-2 text-sm text-slate-400">Time spent reviewing material across all quizzes.</p>
            </div>

            <div className="group rounded-[2rem] border border-slate-800 bg-white/5 p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.8)] backdrop-blur-xl">
              <p className="text-sm uppercase tracking-[0.25em] text-slate-500">Correct Answer Ratio</p>
              <p className="mt-4 text-4xl font-semibold text-white">{correctRatio}%</p>
              <p className="mt-2 text-sm text-slate-400">Correct answers out of all questions attempted.</p>
            </div>
          </div>

          <div className="mt-10 grid gap-8 xl:grid-cols-[1.2fr_0.8fr]">
            <section className="rounded-[2rem] border border-slate-800 bg-white/5 p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.8)] backdrop-blur-xl">
              <div className="flex items-center justify-between gap-4 mb-6">
                <div>
                  <p className="text-sm uppercase tracking-[0.3em] text-slate-500">Weaknesses & Strengths</p>
                  <h2 className="mt-3 text-2xl font-semibold text-white">Performance by question type</h2>
                </div>
                <div className="rounded-full border border-slate-700 px-4 py-2 text-xs uppercase tracking-[0.25em] text-slate-400">Sorted by type</div>
              </div>

              <div className="space-y-5">
                {(
                  ["mcp", "true_false_ng", "matching", "fill_in"] as QuestionType[]
                ).map((type) => {
                  const data = stats.byType[type];
                  const label = questionTypeLabels[type];
                  const progress = data.total > 0 ? Math.round((data.correct / data.total) * 100) : 0;
                  return (
                    <div key={type} className="space-y-2">
                      <div className="flex items-center justify-between text-sm text-slate-300">
                        <span>{label}</span>
                        <span>{data.correct}/{data.total} correct</span>
                      </div>
                      <div className="h-3 rounded-full bg-slate-800 overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${progress}%`, background: `linear-gradient(90deg, #38bdf8, #22c55e)` }}
                        />
                      </div>
                      <div className="text-xs text-slate-500">Accuracy {progress}%</div>
                    </div>
                  );
                })}
              </div>
            </section>

            <section className="rounded-[2rem] border border-slate-800 bg-white/5 p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.8)] backdrop-blur-xl">
              <p className="text-sm uppercase tracking-[0.3em] text-slate-500">Score Progression</p>
              <h2 className="mt-3 text-2xl font-semibold text-white">Trend over time</h2>
              <div className="mt-7 overflow-x-auto">
                <div className="relative min-w-[400px] rounded-3xl border border-slate-800 bg-slate-950/40 p-5">
                  <svg viewBox={`0 0 ${120 * Math.max(scoreProgress.length, 2)} 140`} className="w-full h-44">
                    <defs>
                      <linearGradient id="lineGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor="#38bdf8" />
                        <stop offset="100%" stopColor="#22c55e" />
                      </linearGradient>
                    </defs>
                    <path d={pathD} fill="none" stroke="url(#lineGradient)" strokeWidth="4" strokeLinecap="round" />
                    {scoreProgress.map((item, index) => {
                      const x = 40 + index * 120;
                      const y = 120 - Math.round((item.score / 100) * 80);
                      return (
                        <g key={item.date}>
                          <circle cx={x} cy={y} r="10" fill="#0f172a" stroke="#38bdf8" strokeWidth="3" />
                          <text x={x} y={y - 16} textAnchor="middle" className="fill-slate-300" fontSize="12">
                            {item.score}%
                          </text>
                          <text x={x} y="134" textAnchor="middle" className="fill-slate-400" fontSize="11">
                            {item.date.split(",")[0]}
                          </text>
                        </g>
                      );
                    })}
                  </svg>
                </div>
              </div>
            </section>
          </div>

          <section className="mt-10 rounded-[2rem] border border-slate-800 bg-white/5 p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.8)] backdrop-blur-xl">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between mb-6">
              <div>
                <p className="text-sm uppercase tracking-[0.3em] text-slate-500">Attempt History</p>
                <h2 className="mt-3 text-2xl font-semibold text-white">Chronological log</h2>
              </div>
              <p className="text-sm text-slate-400">Most recent attempts appear first.</p>
            </div>

            <div className="overflow-hidden rounded-3xl border border-slate-800 bg-slate-950/60">
              <table className="min-w-full text-left text-sm text-slate-300">
                <thead className="border-b border-slate-800 bg-slate-950/90 text-slate-400">
                  <tr>
                    <th className="px-6 py-4">Subject</th>
                    <th className="px-6 py-4">Quiz</th>
                    <th className="px-6 py-4">Score</th>
                    <th className="px-6 py-4">Time</th>
                    <th className="px-6 py-4">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {attempts.map((attempt) => (
                    <tr key={attempt.id} className="border-b border-slate-800 hover:bg-slate-900/40 transition-colors">
                      <td className="px-6 py-4 align-top">
                        <span className="inline-flex rounded-full bg-slate-800/80 px-3 py-1 text-xs uppercase tracking-[0.2em] text-slate-400">
                          {attempt.subject_name}
                        </span>
                      </td>
                      <td className="px-6 py-4 align-top text-white font-medium">{attempt.exam_title}</td>
                      <td className="px-6 py-4 align-top">
                        <span className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${badgeTone(attempt.score)}`}>
                          {attempt.score}%
                        </span>
                      </td>
                      <td className="px-6 py-4 align-top text-slate-300">{formatTime(attempt.time_spent)}</td>
                      <td className="px-6 py-4 align-top text-slate-400">{formatDate(attempt.completed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
