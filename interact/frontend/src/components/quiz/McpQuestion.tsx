import { useQuiz } from "../../contexts/QuizContext";
import { QuestionContent } from "./QuestionContent";
import type { Question } from "../../types/types";

interface McpQuestionProps {
  question: Question;
}

export function McpQuestion({ question }: McpQuestionProps) {
  const { state, setAnswer } = useQuiz();
  const answer = state.answers[question.question_id];
  const selectedOptions = answer?.selected_options || [];

  const handleSelect = (label: string) => {
    if (state.is_submitted) return;

    let newSelected: string[];
    if (question.multiple_choice) {
      if (selectedOptions.includes(label)) {
        newSelected = selectedOptions.filter((l) => l !== label);
      } else {
        newSelected = [...selectedOptions, label];
      }
    } else {
      newSelected = [label];
    }

    setAnswer(question.question_id, {
      ...answer,
      question_id: question.question_id,
      selected_options: newSelected,
    });
  };

  return (
    <div className="flex flex-col gap-3">
      {question.options?.map((opt) => {
        const isSelected = selectedOptions.includes(opt.label);
        
        let stateClass = "border-[var(--color-border)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)] text-[var(--color-text)]";
        let letterClass = "bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)]";

        if (isSelected && !state.is_submitted) {
          stateClass = "border-[var(--color-primary)] bg-[var(--color-primary-light)] dark:bg-indigo-950/40 text-[var(--color-primary)] dark:text-indigo-300";
          letterClass = "bg-[var(--color-primary)] text-white";
        }

        // After submit logic
        if (state.is_submitted) {
          const isCorrectAnswer = question.option_answer?.includes(opt.label);
          
          if (isCorrectAnswer) {
            stateClass = "border-[var(--color-success)] bg-[var(--color-success-light)] dark:bg-emerald-950/30 text-emerald-800 dark:text-emerald-300";
            letterClass = "bg-[var(--color-success)] text-white";
          } else if (isSelected && !isCorrectAnswer) {
            stateClass = "border-[var(--color-danger)] bg-[var(--color-danger-light)] dark:bg-red-950/30 text-red-800 dark:text-red-300";
            letterClass = "bg-[var(--color-danger)] text-white";
          } else {
            stateClass = "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-secondary)] opacity-60";
          }
        }

        return (
          <button
            key={opt.label}
            onClick={() => handleSelect(opt.label)}
            disabled={state.is_submitted}
            className={`w-full flex items-start p-4 border rounded-xl transition-all text-left group ${stateClass}`}
          >
            <div className={`shrink-0 w-8 h-8 rounded flex items-center justify-center font-mono font-bold text-sm mr-4 transition-colors ${letterClass}`}>
              {opt.label}
            </div>
            <div className="flex-1 mt-0.5">
              <QuestionContent content={opt.content} />
            </div>
            
            {question.multiple_choice && !state.is_submitted && (
              <div className={`w-5 h-5 rounded border ml-4 shrink-0 mt-1.5 flex items-center justify-center ${isSelected ? 'bg-[var(--color-primary)] border-[var(--color-primary)]' : 'border-[var(--color-border-strong)]'}`}>
                {isSelected && (
                  <svg className="w-3.5 h-3.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
