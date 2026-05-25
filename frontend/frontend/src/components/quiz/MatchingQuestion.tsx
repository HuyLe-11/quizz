import { useQuiz } from "../../contexts/QuizContext";
import { QuestionContent } from "./QuestionContent";
import type { Question } from "../../types/types";

interface MatchingQuestionProps {
  question: Question;
}

export function MatchingQuestion({ question }: MatchingQuestionProps) {
  const { state, setAnswer } = useQuiz();
  const answer = state.answers[question.question_id];
  const selections = answer?.matching_selections || {};

  const handleSelect = (descIndex: number, selectedAnswer: string) => {
    if (state.is_submitted) return;
    
    setAnswer(question.question_id, {
      ...answer,
      question_id: question.question_id,
      matching_selections: {
        ...selections,
        [descIndex]: selectedAnswer,
      },
    });
  };

  if (!question.matches) return null;

  // Gather all unique answers to show in the dropdowns (including distractors)
  // We use Set to avoid duplicates if any
  const availableAnswers = Array.from(new Set(question.matches.map(m => m.answer)));

  return (
    <div className="flex flex-col gap-4">
      <div className="hidden md:grid md:grid-cols-12 gap-4 mb-2 px-4 text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
        <div className="col-span-7">Description</div>
        <div className="col-span-5">Match</div>
      </div>
      
      {question.matches.filter(m => m.description).map((match, idx) => {
        const selectedVal = selections[idx] || "";
        
        let selectClass = "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] hover:border-[var(--color-primary)]";
        
        if (state.is_submitted) {
          const isCorrect = selectedVal === match.answer;
          if (isCorrect) {
            selectClass = "border-[var(--color-success)] bg-[var(--color-success-light)] dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300";
          } else {
            selectClass = "border-[var(--color-danger)] bg-[var(--color-danger-light)] dark:bg-red-950/40 text-red-800 dark:text-red-300";
          }
        }

        return (
          <div 
            key={idx} 
            className="flex flex-col md:grid md:grid-cols-12 gap-4 items-center bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 transition-colors hover:bg-[var(--color-surface-hover)]"
          >
            <div className="w-full md:col-span-7 flex items-start gap-3">
              <span className="shrink-0 w-6 h-6 flex items-center justify-center bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)] rounded-full text-xs font-bold mt-1">
                {idx + 1}
              </span>
              <div className="flex-1 mt-1 text-sm">
                <QuestionContent content={match.description} />
              </div>
            </div>
            
            <div className="w-full md:col-span-5">
              <select
                value={selectedVal}
                onChange={(e) => handleSelect(idx, e.target.value)}
                disabled={state.is_submitted}
                className={`w-full p-2.5 rounded-lg border focus:ring-2 focus:ring-[var(--color-primary-light)] focus:outline-none transition-colors text-sm ${selectClass}`}
              >
                <option value="" disabled>-- Select a match --</option>
                {availableAnswers.map((ans, aIdx) => (
                  <option key={aIdx} value={ans}>
                    {/* Note: option tags can't render HTML/JSX, so we strip tags or show raw string. */}
                    {ans.replace(/<[^>]*>?/gm, '')}
                  </option>
                ))}
              </select>
              
              {state.is_submitted && selectedVal !== match.answer && (
                <div className="mt-2 text-xs text-[var(--color-success)] font-medium flex items-center gap-1">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                  </svg>
                  Correct: {match.answer}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
