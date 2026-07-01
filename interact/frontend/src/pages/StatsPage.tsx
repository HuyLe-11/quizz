import { Link } from "react-router-dom";
import { useState } from "react";
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

  // keep attempts in chronological order (oldest first) to match scoreProgress
  const attemptsChron = attempts.slice().reverse();

  const [modalOpen, setModalOpen] = useState(false);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [devPopup, setDevPopup] = useState<{ open: boolean; attemptId?: string | null }>({ open: false, attemptId: null });

  return (
    <div className="max-w-7xl mx-auto px-6 py-10 min-h-[calc(100vh-4rem)] text-[var(--color-text)] animate-fade-in">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between mb-8">
        <div>
          <p className="text-sm uppercase tracking-[0.3em] text-[var(--color-text-secondary)]">Study dashboard</p>
          <h1 className="mt-3 text-4xl font-semibold text-[var(--color-text)]">Performance insights</h1>
          <p className="mt-2 max-w-2xl text-[var(--color-text-secondary)]">
            Review your recent quiz history, monitor trends, and identify your strongest and weakest question types.
          </p>
        </div>

        <Link
          to="/"
          className="inline-flex items-center justify-center rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-5 py-3 text-sm font-semibold text-[var(--color-text-secondary)] transition hover:border-[var(--color-border-strong)] hover:bg-[var(--color-surface-hover)]"
        >
          Take another quiz
        </Link>
      </div>

      {stats.totalAttempts === 0 ? (
        <div className="rounded-[2rem] border border-[var(--color-border)] bg-[var(--color-surface)] p-12 text-center shadow-[0_25px_120px_-40px_rgba(15,23,42,0.8)]">
          <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-3xl bg-[var(--color-bg-tertiary)]/60 text-3xl">📘</div>
          <h2 className="text-2xl font-semibold text-[var(--color-text)] mb-2">No quizzes recorded yet</h2>
          <p className="text-[var(--color-text-secondary)] mb-6">
            Start your first quiz to populate this dashboard with scored attempts, progress charts, and learning insights.
          </p>
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-full bg-[var(--color-primary)] px-6 py-3 text-sm font-semibold text-[var(--color-primary-foreground)] transition hover:opacity-90"
          >
            Explore quizzes
          </Link>
        </div>
      ) : (
        <>
          <div className="grid gap-5 xl:grid-cols-[1.5fr_1fr_1fr_1fr]">
            <div className="group rounded-[2rem] border border-[var(--color-border)] bg-[var(--color-surface)] p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <p className="text-sm uppercase tracking-[0.25em] text-[var(--color-text-secondary)]">Total Quizzes Taken</p>
              <p className="mt-4 text-4xl font-semibold text-[var(--color-text)]">{stats.totalAttempts}</p>
              <p className="mt-2 text-sm text-[var(--color-text-secondary)]">Completed quiz attempts since you started learning.</p>
            </div>

            <div className="group rounded-[2rem] border border-[var(--color-border)] bg-[var(--color-surface)] p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.08)] backdrop-blur-xl flex flex-col items-center justify-center">
              <div
                className="relative flex h-36 w-36 items-center justify-center rounded-full bg-[var(--color-bg-tertiary)]/50"
                style={{
                  background: `conic-gradient(${scoreColor(stats.avgScore)} ${stats.avgScore * 3.6}deg, rgba(15, 23, 42, 0.6) 0deg)`,
                }}
              >
                <div className="flex h-28 w-28 items-center justify-center rounded-full bg-[var(--color-surface)] text-center">
                  <p className="text-3xl font-semibold text-[var(--color-text)]">{stats.avgScore}%</p>
                </div>
              </div>
              <p className="mt-5 text-sm uppercase tracking-[0.25em] text-[var(--color-text-secondary)]">Average Score</p>
              <p className="mt-3 text-sm text-[var(--color-text-secondary)]">A rolling view of your performance across all attempts.</p>
            </div>

            <div className="group rounded-[2rem] border border-[var(--color-border)] bg-[var(--color-surface)] p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <p className="text-sm uppercase tracking-[0.25em] text-[var(--color-text-secondary)]">Total Study Time</p>
              <p className="mt-4 text-4xl font-semibold text-[var(--color-text)]">{formatTime(stats.totalTimeSpent)}</p>
              <p className="mt-2 text-sm text-[var(--color-text-secondary)]">Time spent reviewing material across all quizzes.</p>
            </div>

            <div className="group rounded-[2rem] border border-[var(--color-border)] bg-[var(--color-surface)] p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <p className="text-sm uppercase tracking-[0.25em] text-[var(--color-text-secondary)]">Correct Answer Ratio</p>
              <p className="mt-4 text-4xl font-semibold text-[var(--color-text)]">{correctRatio}%</p>
              <p className="mt-2 text-sm text-[var(--color-text-secondary)]">Correct answers out of all questions attempted.</p>
            </div>
          </div>

          <div className="mt-10 grid gap-8 xl:grid-cols-[1.2fr_0.8fr]">
            <section className="rounded-[2rem] border border-[var(--color-border)] bg-[var(--color-surface)] p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              <div className="flex items-center justify-between gap-4 mb-6">
                <div>
                  <p className="text-sm uppercase tracking-[0.3em] text-[var(--color-text-secondary)]">Weaknesses & Strengths</p>
                    <h2 className="mt-3 text-2xl font-semibold text-[var(--color-text)]">Performance by question type</h2>
                </div>
                <div className="rounded-full border border-[var(--color-border)] px-4 py-2 text-xs uppercase tracking-[0.25em] text-[var(--color-text-secondary)]">Sorted by type</div>
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
                      <div className="flex items-center justify-between text-sm text-[var(--color-text-muted)]">
                        <span>{label}</span>
                        <span>{data.correct}/{data.total} correct</span>
                      </div>
                      <div className="h-3 rounded-full bg-slate-800 overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${progress}%`, background: `linear-gradient(90deg, #38bdf8, #22c55e)` }}
                        />
                      </div>
                      <div className="text-xs text-[var(--color-text-secondary)]">Accuracy {progress}%</div>
                    </div>
                  );
                })}
              </div>
            </section>

            <section className="rounded-[2rem] border border-slate-800 bg-white/5 p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.8)] backdrop-blur-xl">
              <p className="text-sm uppercase tracking-[0.3em] text-[var(--color-text-secondary)]">Score Progression</p>
              <h2 className="mt-3 text-2xl font-semibold text-[var(--color-text)]">Trend over time</h2>
              <div className="mt-7 overflow-x-auto">
                <div className="relative min-w-[400px] rounded-3xl border border-[var(--color-border)] bg-[var(--color-bg-tertiary)]/40 p-5">
                  <svg viewBox={`0 0 ${120 * Math.max(scoreProgress.length, 2)} 140`} className="w-full h-44">
                    <defs>
                      <linearGradient id="lineGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                        {/* Thay đổi màu gradient của đường tuyến tính nếu cần thiết để hợp với light mode */}
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
                          {/* Small chart point (no click/hover interactions here) */}
                          <circle cx={x} cy={y} r="10" className="fill-[var(--color-bg-tertiary)]" stroke="#38bdf8" strokeWidth="3" />
                          
                          {/* FIX 2: Cập nhật class màu chữ để tự động đổi màu theo chế độ */}
                          <text x={x} y={y - 16} textAnchor="middle" className="fill-[var(--color-text-secondary)] font-medium" fontSize="12">
                            {item.score}%
                          </text>
                          
                          {/* FIX 3: Cập nhật màu chữ cho phần ngày tháng hiển thị phía dưới */}
                          <text x={x} y="134" textAnchor="middle" className="fill-[var(--color-text-secondary)]" fontSize="11">
                            {item.date.split(",")[0]}
                          </text>
                        </g>
                      );
                    })}
                    </svg>
                    </div>
                    <div className="mt-4 flex justify-end">
                      <button
                        onClick={() => {
                          setModalOpen(true);
                          setHoverIndex(null);
                        }}
                        className="rounded-full bg-[var(--color-primary)] px-4 py-2 text-sm font-semibold text-[var(--color-primary-foreground)]"
                      >
                        Expand
                      </button>
                    </div>
              </div>
            </section>
          </div>

          <section className="mt-10 rounded-[2rem] border border-slate-800 bg-white/5 p-6 shadow-[0_20px_80px_-60px_rgba(15,23,42,0.8)] backdrop-blur-xl">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between mb-6">
              <div>
                <p className="text-sm uppercase tracking-[0.3em] text-[var(--color-text-secondary)]">Attempt History</p>
                <h2 className="mt-3 text-2xl font-semibold text-[var(--color-text)]">Chronological log</h2>
              </div>
              <p className="text-sm text-[var(--color-text-secondary)]">Most recent attempts appear first.</p>
            </div>

            <div className="overflow-hidden rounded-3xl border border-[var(--color-border)] bg-[var(--color-surface)]">
              <table className="min-w-full text-left text-sm text-[var(--color-text-muted)]">
                <thead className="border-b border-[var(--color-border)] bg-[var(--color-bg-tertiary)]/90 text-[var(--color-text-secondary)]">
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
                    <tr key={attempt.id} className="border-b border-[var(--color-border)] hover:bg-[var(--color-surface-hover)] transition-colors">
                      <td className="px-6 py-4 align-top">
                        <span className="inline-flex rounded-full bg-[var(--color-bg-tertiary)]/80 px-3 py-1 text-xs uppercase tracking-[0.2em] text-[var(--color-text-secondary)]">
                          {attempt.subject_name}
                        </span>
                      </td>
                      <td className="px-6 py-4 align-top text-[var(--color-text)] font-medium">{attempt.exam_title}</td>
                      <td className="px-6 py-4 align-top">
                        <span className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${badgeTone(attempt.score)}`}>
                          {attempt.score}%
                        </span>
                      </td>
                      <td className="px-6 py-4 align-top text-[var(--color-text-muted)]">{formatTime(attempt.time_spent)}</td>
                      <td className="px-6 py-4 align-top text-[var(--color-text-secondary)]">{formatDate(attempt.completed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {modalOpen && (
        (() => {
          const largeScores = scoreProgress.map((s) => s.score);
          // build a path that matches the large chart's coordinate system
          // increase vertical space so nodes don't get clipped
          const chartTop = 40;
          const chartBottom = 40;
          const chartHeight = 320 - chartTop - chartBottom;
          const largePoints = largeScores.map((value, index) => {
            const x = 40 + index * 120;
            const y = chartTop + Math.round((100 - value) / 100 * chartHeight);
            return { x, y };
          });
          let largePath = "";
          if (largePoints.length > 0) {
            largePath = `M ${largePoints[0].x} ${largePoints[0].y}`;
            for (let i = 1; i < largePoints.length; i += 1) {
              largePath += ` L ${largePoints[i].x} ${largePoints[i].y}`;
            }
          }

          return (
            <div className="fixed inset-0 z-60 flex items-center justify-center p-6">
              <div className="absolute inset-0 bg-black/50" onClick={() => setModalOpen(false)} />
              <div className="relative max-w-[90vw] w-full max-h-[90vh] overflow-auto rounded-2xl bg-[var(--color-surface)] border border-[var(--color-border)] p-6">
                <div className="flex items-start justify-between gap-4 mb-4">
                  <div>
                    <h3 className="text-lg font-semibold text-[var(--color-text)]">Score Progression</h3>
                    <p className="text-sm text-[var(--color-text-secondary)]">Hover points to see attempt overview. Click a point to review the attempt.</p>
                  </div>
                  <button onClick={() => setModalOpen(false)} className="px-3 py-1 rounded-md bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)]">Close</button>
                </div>

                <div className="mb-6 overflow-x-auto relative">
                  <svg viewBox={`0 0 ${120 * Math.max(largeScores.length, 2)} 320`} className="w-full h-80">
                    <defs>
                      <linearGradient id="lgLineGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor="#38bdf8" />
                        <stop offset="100%" stopColor="#22c55e" />
                      </linearGradient>
                    </defs>
                    <path d={largePath} fill="none" stroke="url(#lgLineGradient)" strokeWidth="6" strokeLinecap="round" />
                    {largeScores.map((sc, i) => {
                      const x = 40 + i * 120;
                      const y = largePoints[i].y;
                      return (
                        <g
                          key={i}
                          onMouseEnter={() => setHoverIndex(i)}
                          onMouseLeave={() => setHoverIndex(null)}
                          onMouseMove={(e) => {
                            // use viewport coordinates so tooltip can be positioned fixed (not clipped)
                            setTooltipPos({ x: e.clientX, y: e.clientY });
                          }}
                          onClick={() => {
                            const attemptId = attemptsChron[i]?.id || null;
                            setDevPopup({ open: true, attemptId });
                          }}
                          style={{ cursor: "pointer" }}
                        >
                          <circle cx={x} cy={y} r="12" className="fill-[var(--color-bg-tertiary)]" stroke="#38bdf8" strokeWidth="4" />
                          <text x={x} y={y - 30} textAnchor="middle" className="fill-[var(--color-text-secondary)] font-medium" fontSize="13">{sc}%</text>
                          <text x={x} y={y - 20} textAnchor="middle" className="fill-[var(--color-text-secondary)]" fontSize="11">{scoreProgress[i].date.split(",")[0]}</text>
                        </g>
                      );
                    })}
                  </svg>

                  {/* Tooltip inside modal */}
                  {hoverIndex !== null && attemptsChron[hoverIndex] && (
                    (() => {
                      const att = attemptsChron[hoverIndex];
                      const counts: Record<string, { correct: number; total: number }> = {
                        mcp: { correct: 0, total: 0 },
                        true_false_ng: { correct: 0, total: 0 },
                        matching: { correct: 0, total: 0 },
                        fill_in: { correct: 0, total: 0 },
                      };
                      (att.question_results || []).forEach((qr) => {
                        if (!counts[qr.type]) counts[qr.type] = { correct: 0, total: 0 };
                        counts[qr.type].total += 1;
                        if (qr.is_correct) counts[qr.type].correct += 1;
                      });

                      return (
                        <div
                          className="pointer-events-none fixed z-50 w-56 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm shadow-lg"
                          style={{ left: tooltipPos.x, top: tooltipPos.y - 18, transform: "translate(-50%, -120%)" }}
                        >
                          <div className="font-medium text-[var(--color-text)]">{att.exam_title} — {att.score}%</div>
                          <div className="mt-2 text-[var(--color-text-secondary)]">
                            {(Object.keys(counts) as Array<string>).map((k) => (
                              <div key={k} className="flex items-center justify-between text-xs">
                                <span>{questionTypeLabels[k as QuestionType] || k}</span>
                                <span>
                                  {counts[k].correct}/{counts[k].total}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })()
                  )}
                </div>
              </div>
            </div>
          );
        })()
      )}

      {/* Development popup shown when clicking a node (feature in progress) */}
      {devPopup.open && (
        <div className="fixed inset-0 z-70 flex items-center justify-center p-6">
          <div className="absolute inset-0 bg-black/40" onClick={() => setDevPopup({ open: false, attemptId: null })} />
          <div className="relative z-80 max-w-sm w-full rounded-lg bg-[var(--color-surface)] border border-[var(--color-border)] p-6">
            <h3 className="text-lg font-semibold text-[var(--color-text)]">This function is in development</h3>
            <p className="mt-2 text-sm text-[var(--color-text-secondary)]">The review feature is currently under development. It will support detailed review in the next update.</p>
            <div className="mt-4 flex justify-end">
              <button onClick={() => setDevPopup({ open: false, attemptId: null })} className="rounded-full bg-[var(--color-primary)] px-4 py-2 text-sm font-semibold text-[var(--color-primary-foreground)]">Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
