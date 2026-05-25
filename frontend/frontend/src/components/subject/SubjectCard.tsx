import type { Subject } from "../../types/types";

interface SubjectCardProps {
  subject: Subject;
  onClick: () => void;
}

const IconMap: Record<string, React.ReactNode> = {
  globe: (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
  ),
  cpu: (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg>
  ),
  database: (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg>
  ),
  code: (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>
  ),
  layers: (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg>
  ),
};

/**
 * Card displaying subject information.
 */
export function SubjectCard({ subject, onClick }: SubjectCardProps) {
  const icon = subject.icon && IconMap[subject.icon] ? IconMap[subject.icon] : IconMap["code"];

  return (
    <div
      onClick={onClick}
      className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 cursor-pointer transition-all hover:shadow-[var(--color-shadow)] hover:-translate-y-1 hover:border-[var(--color-primary-light)] group"
    >
      <div className="w-10 h-10 rounded-lg bg-[var(--color-bg-tertiary)] flex items-center justify-center text-[var(--color-text-secondary)] mb-4 group-hover:bg-[var(--color-primary-light)] group-hover:text-[var(--color-primary)] transition-colors">
        {icon}
      </div>
      <h3 className="text-lg font-semibold text-[var(--color-text)] mb-1 truncate">
        {subject.name}
      </h3>
      <p className="text-sm text-[var(--color-text-secondary)] mb-4 line-clamp-2 min-h-[40px]">
        {subject.description}
      </p>
      <div className="flex items-center text-xs font-medium text-[var(--color-text-muted)]">
        <svg className="w-4 h-4 mr-1.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        {subject.exam_count} {subject.exam_count === 1 ? 'Exam' : 'Exams'}
      </div>
    </div>
  );
}
