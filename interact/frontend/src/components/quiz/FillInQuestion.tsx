import { useQuiz } from "../../contexts/QuizContext";
import type { Question } from "../../types/types";
import { checkQuestionCorrectness } from "../../services/stats";

interface FillInQuestionProps {
  question: Question;
}

export function FillInQuestion({ question }: FillInQuestionProps) {
  const { state, setAnswer } = useQuiz();
  const answer = state.answers[question.question_id];
  
  // For simplicity, assuming one blank per fill-in question in this UI mock
  const val = answer?.fill_in_values?.[0] || "";

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (state.is_submitted) return;
    setAnswer(question.question_id, {
      ...answer,
      question_id: question.question_id,
      fill_in_values: [e.target.value],
    });
  };

  let inputClass = "border-[var(--color-border-strong)] focus:border-[var(--color-primary)] bg-[var(--color-surface)] text-[var(--color-text)]";
  
  if (state.is_submitted) {
    // Simple exact match check (case insensitive). 
    // In reality, backend should evaluate this.
    // BUGFIX(F4): this check did not trim the accepted answers, unlike the scoring in stats.ts,
    // so the input could be painted red while the score counted it correct. Reuse the scorer.
    const isCorrect = checkQuestionCorrectness(question, answer);

    if (isCorrect) {
      inputClass = "border-[var(--color-success)] bg-[var(--color-success-light)] dark:bg-emerald-950/30 text-emerald-800 dark:text-emerald-300";
    } else {
      inputClass = "border-[var(--color-danger)] bg-[var(--color-danger-light)] dark:bg-red-950/30 text-red-800 dark:text-red-300";
    }
  }

  return (
    <div>
      <input
        type="text"
        value={val}
        onChange={handleChange}
        disabled={state.is_submitted}
        placeholder="Nhập câu trả lời..."
        className={`w-full max-w-md p-4 rounded-xl border-2 focus:ring-4 focus:ring-[var(--color-primary-light)] focus:outline-none transition-all font-medium text-lg ${inputClass}`}
      />
      
      {state.is_submitted && question.fill_in_answers && (
        <div className="mt-4 p-4 bg-[var(--color-bg-tertiary)] border-l-4 border-[var(--color-info)] rounded-r-lg">
          <p className="text-sm text-[var(--color-text-secondary)] mb-1 font-medium">Các đáp án được chấp nhận:</p>
          <div className="flex flex-wrap gap-2">
            {question.fill_in_answers.map((ans, i) => (
              <span key={i} className="px-3 py-1 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-md font-mono text-sm text-[var(--color-info)]">
                {ans}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
