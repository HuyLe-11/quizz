import {
    BrowserRouter,
    Navigate,
    Route,
    Routes,
    useLocation,
} from "react-router-dom";

import {
    QuizProvider,
    ThemeProvider,
} from "./contexts/AppProviders";

import { AuthProvider } from "./contexts/AuthProvider";
import { useAuth } from "./contexts/AuthContext";

import { Header } from "./components/layout/Header";

import { AuthPage } from "./pages/AuthPage";
import { HomePage } from "./pages/HomePage";
import { QuizPage } from "./pages/QuizPage";
import { StatsPage } from "./pages/StatsPage";
import { AttemptReview } from "./pages/AttemptReview";
import { HistoryPage } from "./pages/HistoryPage";
import { ExamPreviewPage } from "./pages/ExamPreviewPage";


import { configureAuthenticatedApi } from "./services/api";
import { requireSupabase } from "./services/supabase";
import { ParserPreviewPage } from "./pages/ParserPreviewPage";

configureAuthenticatedApi(async () => {
    const {
        data: { session },
        error,
    } = await requireSupabase().auth.getSession();

    if (error) {
        throw new Error(
            "Could not get authentication session.",
            { cause: error },
        );
    }

    if (!session?.access_token) {
        throw new Error("Authentication required.");
    }

    return `Bearer ${session.access_token}`;
});

const parserPreviewEnabled =
    import.meta.env.DEV &&
    import.meta.env.VITE_PARSER_PREVIEW === "1";

function ApplicationRoutes() {
    const { user, loading } = useAuth();
    const location = useLocation();

    if (loading) {
        return (
            <div
                role="status"
                className="flex min-h-screen items-center justify-center bg-[var(--color-bg)] text-[var(--color-text-secondary)]"
            >
                Đang kiểm tra phiên đăng nhập...
            </div>
        );
    }

    if (!user) {
        const returnPath =
            location.pathname + location.search;

        return (
            <Routes>
                <Route
                    path="/login"
                    element={<AuthPage mode="login" />}
                />

                <Route
                    path="/register"
                    element={<AuthPage mode="register" />}
                />

                <Route
                    path="*"
                    element={
                        <Navigate
                            to="/login"
                            replace
                            state={{ from: returnPath }}
                        />
                    }
                />
            </Routes>
        );
    }

    return (
        <QuizProvider key={user.id}>
            <div className="min-h-screen bg-[var(--color-bg)] transition-colors duration-200">
                <Header />

                <main className="pt-16">
                    <Routes>
                        <Route
                            path="/"
                            element={<HomePage />}
                        />

                        <Route
                            path="/quiz/:examId"
                            element={<QuizPage />}
                        />

                        {parserPreviewEnabled && (
                            <Route
                                path="/parser-preview"
                                element={<ParserPreviewPage />}
                            />
                        )}

                        <Route
                            path="/stats"
                            element={<StatsPage />}
                        />

                        <Route
                            path="/attempt/:attemptId"
                            element={<AttemptReview />}
                        />

                        <Route
                            path="/history"
                            element={<HistoryPage />}
                        />

                        <Route
                            path="/exams/:examId"
                            element={<ExamPreviewPage />}
                        />

                        <Route
                            path="*"
                            element={
                                <Navigate to="/" replace />
                            }
                        />
                    </Routes>
                </main>
            </div>
        </QuizProvider>
    );
}

export default function App() {
    return (
        <ThemeProvider>
            <AuthProvider>
                <BrowserRouter>
                    <ApplicationRoutes />
                </BrowserRouter>
            </AuthProvider>
        </ThemeProvider>
    );
}
