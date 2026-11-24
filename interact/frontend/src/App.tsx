import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import { ThemeProvider } from "./contexts/ThemeContext";
import { QuizProvider } from "./contexts/QuizContext";
import { Header } from "./components/layout/Header";
import { HomePage } from "./pages/HomePage";
import { QuizPage } from "./pages/QuizPage";
import { StatsPage } from "./pages/StatsPage";
import { AttemptReview } from "./pages/AttemptReview";
import { HistoryPage } from "./pages/HistoryPage";
import { ExamPreviewPage } from "./pages/ExamPreviewPage";
import { ParserPreviewPage } from "./pages/ParserPreviewPage";

function App() {
  return (
      <ThemeProvider>
        <QuizProvider>
          <BrowserRouter>
            <div className="min-h-screen bg-[var(--color-bg)] transition-colors duration-200">
              <Header />
              <main className="pt-16">
                <div className="mx-auto flex max-w-7xl justify-end px-6 pt-3">
                  <Link
                      to="/parser-preview"
                      className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm font-medium text-[var(--color-primary)] hover:bg-[var(--color-surface-hover)]"
                  >
                    Parser Preview
                  </Link>
                </div>

                <Routes>
                  <Route path="/" element={<HomePage />} />
                  <Route path="/parser-preview" element={<ParserPreviewPage />} />
                  <Route path="/quiz/:examId" element={<QuizPage />} />
                  <Route path="/stats" element={<StatsPage />} />
                  <Route path="/attempt/:attemptId" element={<AttemptReview />} />
                  <Route path="/history" element={<HistoryPage />} />
                  <Route path="/exams/:examId" element={<ExamPreviewPage />} />
                </Routes>
              </main>
            </div>
          </BrowserRouter>
        </QuizProvider>
      </ThemeProvider>
  );
}

export default App;
