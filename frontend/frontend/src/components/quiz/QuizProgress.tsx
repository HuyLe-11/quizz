import { useQuiz } from "../../contexts/QuizContext";

/**
 * Progress indicator for the current quiz session.
 */
export function QuizProgress() {
  const { state, answeredCount } = useQuiz();
  
  if (!state.exam) return null;

  const total = state.exam.question_count;
  const percentage = total > 0 ? (answeredCount / total) * 100 : 0;

  return (
    <div className="w-full bg-[var(--color-surface)] p-4 rounded-xl border border-[var(--color-border)] shadow-sm">
      <div className="flex justify-between items-center mb-2 text-sm">
        <span className="font-medium text-[var(--color-text-secondary)]">Progress</span>
        <span className="font-mono font-semibold text-[var(--color-primary)]">
          {answeredCount} of {total} answered
        </span>
      </div>
      <div className="w-full h-2.5 bg-[var(--color-bg-tertiary)] rounded-full overflow-hidden">
        <div 
          className="h-full bg-gradient-to-r from-[var(--color-primary-hover)] to-[var(--color-primary)] rounded-full transition-all duration-500 ease-out"
          style={{ width: `${percentage}%` }}
        ></div>
      </div>
    </div>
  );
}
