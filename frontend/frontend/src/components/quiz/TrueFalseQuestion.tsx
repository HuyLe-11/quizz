import { useQuiz } from "../../contexts/QuizContext";
import type { Question } from "../../types/types";

interface TrueFalseQuestionProps {
  question: Question;
}

export function TrueFalseQuestion({ question }: TrueFalseQuestionProps) {
  const { state, setAnswer } = useQuiz();
  const answer = state.answers[question.question_id];
  const selectedValue = answer?.true_false_value;

  const handleSelect = (val: string) => {
    if (state.is_submitted) return;
    setAnswer(question.question_id, {
      ...answer,
      question_id: question.question_id,
      true_false_value: val,
    });
  };

  const options = [
    { value: "true", label: "True" },
    { value: "false", label: "False" },
    { value: "ng", label: "Not Given" },
  ];

  return (
    <div className="flex flex-col sm:flex-row gap-4">
      {options.map((opt) => {
        const isSelected = selectedValue === opt.value;
        
        let stateClass = "border-[var(--color-border)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)] text-[var(--color-text)]";
        
        if (isSelected && !state.is_submitted) {
          if (opt.value === "true") {
            stateClass = "border-[var(--color-success)] bg-[var(--color-success-light)] dark:bg-emerald-950/30 text-[var(--color-success)] dark:text-emerald-400";
          } else if (opt.value === "false") {
            stateClass = "border-[var(--color-danger)] bg-[var(--color-danger-light)] dark:bg-red-950/30 text-[var(--color-danger)] dark:text-red-400";
          } else {
            stateClass = "border-[var(--color-warning)] bg-[var(--color-warning-light)] dark:bg-amber-950/30 text-[var(--color-warning)] dark:text-amber-400";
          }
        }

        if (state.is_submitted) {
          const isCorrect = question.option_answer?.[0] === opt.value;
          
          if (isCorrect) {
            stateClass = "border-[var(--color-success)] bg-[var(--color-success-light)] dark:bg-emerald-950/30 text-emerald-800 dark:text-emerald-300 ring-2 ring-[var(--color-success)]";
          } else if (isSelected && !isCorrect) {
            stateClass = "border-[var(--color-danger)] bg-[var(--color-danger-light)] dark:bg-red-950/30 text-red-800 dark:text-red-300";
          } else {
            stateClass = "border-[var(--color-border)] bg-[var(--color-bg-tertiary)] text-[var(--color-text-muted)] opacity-60";
          }
        }

        return (
          <button
            key={opt.value}
            onClick={() => handleSelect(opt.value)}
            disabled={state.is_submitted}
            className={`flex-1 p-6 rounded-xl border-2 font-bold text-lg transition-all text-center ${stateClass}`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
