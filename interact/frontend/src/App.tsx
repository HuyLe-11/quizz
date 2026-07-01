import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ThemeProvider } from "./contexts/ThemeContext";
import { QuizProvider } from "./contexts/QuizContext";
import { Header } from "./components/layout/Header";
import { HomePage } from "./pages/HomePage";
import { QuizPage } from "./pages/QuizPage";
import { StatsPage } from "./pages/StatsPage";
import { AttemptReview } from "./pages/AttemptReview";

function App() {
  return (
    <ThemeProvider>
      <QuizProvider>
        <BrowserRouter>
          <div className="min-h-screen bg-[var(--color-bg)] transition-colors duration-200">
            <Header />
            <main className="pt-16">
              <Routes>
                <Route path="/" element={<HomePage />} />
                <Route path="/quiz/:examId" element={<QuizPage />} />
                <Route path="/stats" element={<StatsPage />} />
                <Route path="/attempt/:attemptId" element={<AttemptReview />} />
              </Routes>
            </main>
          </div>
        </BrowserRouter>
      </QuizProvider>
    </ThemeProvider>
  );
}

export default App;
