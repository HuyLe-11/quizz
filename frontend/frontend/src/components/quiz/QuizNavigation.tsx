import { useQuiz } from "../../contexts/QuizContext";

/**
 * Sidebar navigation grid for quick jumping between questions.
 */
export function QuizNavigation() {
  const { state, goToQuestion, submitQuiz, nextQuestion, prevQuestion } = useQuiz();

  if (!state.exam) return null;

  return (
    <div className="flex flex-col h-full">
      <div className="mb-6 flex-1 overflow-y-auto pr-2">
        <div className="grid grid-cols-5 gap-2 sm:grid-cols-4 md:grid-cols-5">
          {state.exam.questions.map((q, idx) => {
            const isCurrent = state.current_index === idx;
            const isAnswered = !!state.answers[q.question_id];
            
            // Basic status color logic for dots
            let dotClass = "bg-[var(--color-bg-tertiary)] text-[var(--color-text-muted)] border-[var(--color-border)]";
            
            if (isCurrent) {
              dotClass = "border-[var(--color-primary)] bg-[var(--color-primary-light)] text-[var(--color-primary)] font-bold";
            } else if (state.is_submitted) {
              // Note: True correctness logic would require backend validation in a real app
              // For UI mock purposes, we just mark answered as green/info for now, 
              // or you can leave them as primary if answered.
              dotClass = isAnswered 
                ? "bg-[var(--color-success-light)] border-[var(--color-success)] text-[var(--color-success)]" 
                : "bg-[var(--color-warning-light)] border-[var(--color-warning)] text-[var(--color-warning)]";
            } else if (isAnswered) {
              dotClass = "bg-[var(--color-surface-hover)] border-[var(--color-border-strong)] text-[var(--color-text-secondary)] font-medium";
            }

            return (
              <button
                key={q.question_id}
                onClick={() => goToQuestion(idx)}
                className={`
                  aspect-square rounded-md border-2 flex items-center justify-center text-xs font-mono transition-all hover:scale-105
                  ${dotClass}
                `}
                title={`Question ${idx + 1}`}
              >
                {idx + 1}
              </button>
            );
          })}
        </div>
      </div>

      <div className="pt-4 border-t border-[var(--color-border)] mt-auto flex flex-col gap-3 hidden lg:flex">
        <div className="flex gap-2">
          <button
            onClick={() => prevQuestion()}
            disabled={state.current_index === 0}
            className="flex-1 py-2 rounded-md font-medium text-sm border border-[var(--color-border)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Previous
          </button>
          <button
            onClick={() => nextQuestion()}
            disabled={state.current_index === state.exam.question_count - 1}
            className="flex-1 py-2 rounded-md font-medium text-sm bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary-hover)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Next
          </button>
        </div>
        
        {!state.is_submitted && (
          <button
            onClick={submitQuiz}
            className="w-full py-2.5 rounded-md font-bold text-white bg-gradient-to-r from-[var(--color-success)] to-emerald-500 hover:opacity-90 transition-opacity mt-2"
          >
            Submit Quiz
          </button>
        )}
      </div>
    </div>
  );
}
