import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuiz } from "../contexts/QuizContext";
import { MOCK_EXAM } from "../data/mockData";
import { QuizProgress } from "../components/quiz/QuizProgress";
import { QuestionCard } from "../components/quiz/QuestionCard";
import { QuizNavigation } from "../components/quiz/QuizNavigation";

/**
 * Main quiz taking page.
 */
export function QuizPage() {
  const { examId } = useParams<{ examId: string }>();
  const navigate = useNavigate();
  const { state, currentQuestion, startQuiz, nextQuestion, prevQuestion, submitQuiz } = useQuiz();
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  // Initialize quiz if accessed directly
  useEffect(() => {
    if (!state.exam && examId) {
      // In a real app, fetch exam by examId
      startQuiz(MOCK_EXAM);
    }
  }, [state.exam, examId, startQuiz]);

  if (!state.exam || !currentQuestion) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)]">
        <div className="animate-pulse flex flex-col items-center">
          <div className="w-12 h-12 border-4 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin mb-4"></div>
          <p className="text-[var(--color-text-muted)]">Loading exam...</p>
        </div>
      </div>
    );
  }

  const handleBack = () => {
    if (confirm("Are you sure you want to leave? Your progress will be lost.")) {
      navigate("/");
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 min-h-[calc(100vh-64px)] flex flex-col relative animate-fade-in">
      {/* Top Bar */}
      <div className="flex items-center justify-between mb-6 pb-4 border-b border-[var(--color-border)]">
        <div className="flex items-center gap-4">
          <button
            onClick={handleBack}
            className="flex items-center text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text)] transition-colors bg-[var(--color-surface)] px-3 py-1.5 rounded-md border border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
          >
            <svg className="w-4 h-4 mr-1.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Back
          </button>
          <h2 className="text-lg font-bold text-[var(--color-text)] hidden sm:block truncate max-w-md">
            {state.exam.title}
          </h2>
        </div>
        
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm font-semibold text-[var(--color-primary)] bg-[var(--color-primary-light)] px-3 py-1 rounded-full">
            {state.current_index + 1} / {state.exam.question_count}
          </span>
          <button
            onClick={() => setIsSidebarOpen(!isSidebarOpen)}
            className="lg:hidden p-2 rounded-md bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-text-secondary)]"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 6h16M4 12h16m-7 6h7" />
            </svg>
          </button>
        </div>
      </div>

      <div className="mb-6">
        <QuizProgress />
      </div>

      <div className="flex flex-1 gap-8 relative">
        {/* Main Content Area */}
        <div className="flex-1 w-full flex flex-col">
          <QuestionCard 
            key={currentQuestion.question_id} 
            question={currentQuestion} 
            questionIndex={state.current_index} 
          />
          
          {/* Mobile Navigation Controls (Footer) */}
          <div className="mt-8 flex justify-between items-center lg:hidden">
            <button
              onClick={() => prevQuestion()}
              disabled={state.current_index === 0}
              className="px-4 py-2 rounded-md font-medium border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            
            {state.current_index === state.exam.question_count - 1 && !state.is_submitted ? (
              <button
                onClick={() => submitQuiz()}
                className="px-6 py-2 rounded-md font-bold bg-[var(--color-success)] text-white hover:opacity-90"
              >
                Submit
              </button>
            ) : (
              <button
                onClick={() => nextQuestion()}
                disabled={state.current_index === state.exam.question_count - 1}
                className="px-6 py-2 rounded-md font-medium bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary-hover)] disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Next
              </button>
            )}
          </div>
        </div>

        {/* Right Sidebar (Desktop) / Drawer (Mobile) */}
        <div className={`
          fixed inset-y-0 right-0 z-40 w-72 bg-[var(--color-bg)] border-l border-[var(--color-border)] p-6 shadow-xl transform transition-transform duration-300 ease-in-out lg:relative lg:translate-x-0 lg:shadow-none lg:w-80 lg:border-l lg:border-[var(--color-border)] lg:p-4 lg:bg-transparent
          ${isSidebarOpen ? 'translate-x-0' : 'translate-x-full lg:translate-x-0'}
        `}>
          <div className="flex justify-between items-center mb-6 lg:hidden">
            <h3 className="font-bold text-[var(--color-text)]">Navigation</h3>
            <button onClick={() => setIsSidebarOpen(false)} className="p-2 text-[var(--color-text-muted)]">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <QuizNavigation />
        </div>
        
        {/* Overlay for mobile sidebar */}
        {isSidebarOpen && (
          <div 
            className="fixed inset-0 bg-black/20 z-30 lg:hidden"
            onClick={() => setIsSidebarOpen(false)}
          ></div>
        )}
      </div>
    </div>
  );
}
