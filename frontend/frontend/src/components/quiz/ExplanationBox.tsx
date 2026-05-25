import { useState } from "react";
import { QuestionContent } from "./QuestionContent";

interface ExplanationBoxProps {
  explanation?: string;
}

/**
 * Collapsible panel showing the answer explanation.
 */
export function ExplanationBox({ explanation }: ExplanationBoxProps) {
  const [isOpen, setIsOpen] = useState(true);

  if (!explanation) return null;

  return (
    <div className="mt-6 border border-[var(--color-border)] rounded-lg overflow-hidden bg-[var(--color-surface)] shadow-sm animate-fade-in-up">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between p-4 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-surface-hover)] transition-colors border-b border-[var(--color-border)]"
      >
        <div className="flex items-center gap-2 font-semibold text-[var(--color-primary)]">
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          Explanation
        </div>
        <svg
          className={`w-5 h-5 text-[var(--color-text-muted)] transform transition-transform duration-200 ${isOpen ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      
      {isOpen && (
        <div className="p-5 border-l-4 border-l-[var(--color-primary)] bg-[var(--color-bg-tertiary)]">
          <QuestionContent content={explanation} />
        </div>
      )}
    </div>
  );
}
