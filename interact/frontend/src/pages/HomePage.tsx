import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileUpload } from "../components/upload/FileUpload";
import { SubjectGrid } from "../components/subject/SubjectGrid";
import { MOCK_SUBJECTS, MOCK_EXAMS, MOCK_EXAM } from "../data/mockData";
import type { Subject } from "../types/types";import { useQuiz } from "../contexts/QuizContext";

/**
 * Landing page with upload area and subject selection.
 */
export function HomePage() {
  const [selectedSubject, setSelectedSubject] = useState<Subject | null>(null);
  const { startQuiz } = useQuiz();
  const navigate = useNavigate();

  const handleSubjectSelect = (subject: Subject) => {
    setSelectedSubject(subject);
  };

  const closeSubjectModal = () => {
    setSelectedSubject(null);
  };

  const handleExamStart = (examId: string) => {
    // In a real app, you would fetch the exam data first
    // For now, we use the mock exam
    startQuiz(MOCK_EXAM);
    navigate(`/quiz/${examId}`);
  };

  // Filter exams for the selected subject (mock)
  const subjectExams = selectedSubject 
    ? MOCK_EXAMS.filter((e) => e.subject_id === selectedSubject.id) 
    : [];

  return (
    <div className="w-full max-w-5xl mx-auto px-6 py-12 animate-fade-in">
      <div className="text-center mb-12">
        <h1 className="text-4xl md:text-5xl font-bold text-[var(--color-text)] mb-4 tracking-tight">
          Create Your Quiz
        </h1>
        <p className="text-lg text-[var(--color-text-secondary)] max-w-2xl mx-auto">
          Upload a PDF document to automatically generate a quiz, or choose from our pre-made subjects to test your knowledge.
        </p>
      </div>

      <FileUpload />

      <div className="flex items-center justify-center my-12">
        <div className="h-px bg-[var(--color-border)] flex-grow max-w-xs"></div>
        <span className="px-4 text-[var(--color-text-muted)] text-sm uppercase tracking-wider font-medium">Or</span>
        <div className="h-px bg-[var(--color-border)] flex-grow max-w-xs"></div>
      </div>

      <SubjectGrid
        subjects={MOCK_SUBJECTS}
        onSelectSubject={handleSubjectSelect}
      />

      {/* Simple Modal for Exam Selection */}
      {selectedSubject && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
          <div className="bg-[var(--color-bg)] rounded-2xl w-full max-w-md shadow-xl overflow-hidden border border-[var(--color-border)] animate-fade-in-up">
            <div className="p-5 border-b border-[var(--color-border)] flex items-center justify-between bg-[var(--color-bg-secondary)]">
              <div>
                <h3 className="text-lg font-bold text-[var(--color-text)]">
                  {selectedSubject.name}
                </h3>
                <p className="text-xs text-[var(--color-text-secondary)]">Select an exam to start</p>
              </div>
              <button
                onClick={closeSubjectModal}
                className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] rounded-md transition-colors"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            
            <div className="p-4 max-h-[60vh] overflow-y-auto">
              {subjectExams.length > 0 ? (
                <div className="space-y-3">
                  {subjectExams.map((exam) => (
                    <div
                      key={exam.id}
                      onClick={() => handleExamStart(exam.id)}
                      className="group flex items-center justify-between p-4 rounded-xl border border-[var(--color-border)] hover:border-[var(--color-primary)] bg-[var(--color-surface)] hover:bg-[var(--color-primary-light)] cursor-pointer transition-all"
                    >
                      <div>
                        <h4 className="font-semibold text-[var(--color-text)] group-hover:text-[var(--color-primary)] mb-1">
                          {exam.title}
                        </h4>
                        <span className="text-xs text-[var(--color-text-muted)] bg-[var(--color-bg-tertiary)] px-2 py-1 rounded">
                          {exam.question_count} questions
                        </span>
                      </div>
                      <div className="text-[var(--color-primary)] opacity-0 group-hover:opacity-100 transition-opacity transform translate-x-[-10px] group-hover:translate-x-0 duration-300">
                        <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
                        </svg>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-[var(--color-text-muted)]">
                  No exams available for this subject yet.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
