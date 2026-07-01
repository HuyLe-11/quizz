import type { Subject } from "../../types/types";
import { SubjectCard } from "./SubjectCard";

interface SubjectGridProps {
  subjects: Subject[];
  onSelectSubject: (subject: Subject) => void;
  loading?: boolean;
}

/**
 * Grid layout for displaying multiple subjects.
 */
export function SubjectGrid({ subjects, onSelectSubject, loading = false }: SubjectGridProps) {
  if (loading) {
    return (
      <div className="w-full">
        <h2 className="text-xl font-semibold text-[var(--color-text)] mb-6 text-center">
          Or choose from available subjects
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 animate-pulse">
              <div className="w-10 h-10 rounded-lg bg-[var(--color-bg-tertiary)] mb-4"></div>
              <div className="h-5 bg-[var(--color-bg-tertiary)] rounded w-3/4 mb-2"></div>
              <div className="h-4 bg-[var(--color-bg-tertiary)] rounded w-full mb-1"></div>
              <div className="h-4 bg-[var(--color-bg-tertiary)] rounded w-5/6 mb-4"></div>
              <div className="h-4 bg-[var(--color-bg-tertiary)] rounded w-1/4"></div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (subjects.length === 0) {
    return (
      <div className="w-full text-center py-10">
        <p className="text-[var(--color-text-muted)]">No subjects available.</p>
      </div>
    );
  }

  return (
    <div className="w-full">
      <h2 className="text-xl font-semibold text-[var(--color-text)] mb-6 text-center">
        Or choose from available subjects
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {subjects.map((subject) => (
          <SubjectCard
            key={subject.id}
            subject={subject}
            onClick={() => onSelectSubject(subject)}
          />
        ))}
      </div>
    </div>
  );
}
