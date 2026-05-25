import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ThemeProvider } from "./contexts/ThemeContext";
import { QuizProvider } from "./contexts/QuizContext";
import { Header } from "./components/layout/Header";
import { HomePage } from "./pages/HomePage";
import { QuizPage } from "./pages/QuizPage";

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
              </Routes>
            </main>
          </div>
        </BrowserRouter>
      </QuizProvider>
    </ThemeProvider>
  );
}

export default App;
