import {
    useState,
    type FormEvent,
} from "react";

import {
    Link,
    Navigate,
    useLocation,
    useNavigate,
} from "react-router-dom";

import { useAuth } from "../contexts/AuthContext";
import { isSupabaseConfigured } from "../services/supabase";

interface AuthPageProps {
    mode: "login" | "register";
}

export function AuthPage({
                             mode,
                         }: AuthPageProps) {
    const {
        user,
        login,
        register,
    } = useAuth();

    const navigate = useNavigate();
    const location = useLocation();

    const [name, setName] = useState("");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");

    const [submitting, setSubmitting] =
        useState(false);

    const [error, setError] =
        useState<string | null>(null);

    const [message, setMessage] =
        useState<string | null>(null);

    const isRegister = mode === "register";

    const navigationState = location.state as {
        from?: string;
    } | null;

    const requestedPath = navigationState?.from;

    const returnPath =
        typeof requestedPath === "string" &&
        requestedPath.startsWith("/") &&
        !requestedPath.startsWith("//") &&
        !["/login", "/register"].includes(
            requestedPath,
        )
            ? requestedPath
            : "/";

    if (user) {
        return (
            <Navigate to={returnPath} replace />
        );
    }

    async function handleSubmit(
        event: FormEvent<HTMLFormElement>,
    ): Promise<void> {
        event.preventDefault();

        if (submitting || !isSupabaseConfigured) {
            return;
        }

        setSubmitting(true);
        setError(null);
        setMessage(null);

        try {
            if (isRegister) {
                const activated = await register(
                    email.trim(),
                    password,
                    name.trim(),
                );

                if (!activated) {
                    setMessage(
                        "Yêu cầu đăng ký đã được gửi. " +
                        "Hãy kiểm tra email xác nhận nếu " +
                        "Supabase yêu cầu xác minh tài khoản.",
                    );

                    setPassword("");
                    return;
                }
            } else {
                await login(
                    email.trim(),
                    password,
                );
            }

            navigate(returnPath, {
                replace: true,
            });
        } catch (reason) {
            setError(
                reason instanceof Error
                    ? reason.message
                    : "Không thể xác thực tài khoản.",
            );
        } finally {
            setSubmitting(false);
        }
    }

    return (
        <div className="flex min-h-screen items-center justify-center bg-[var(--color-bg-secondary)] px-4 py-10">
            <section className="w-full max-w-md rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 shadow-lg">
                <div className="mb-8 text-center">
                    <h1 className="text-3xl font-bold">
            <span className="text-[var(--color-text)]">
              Quiz
            </span>
                        <span className="text-[var(--color-primary)]">
              PDF
            </span>
                    </h1>

                    <h2 className="mt-7 text-2xl font-bold text-[var(--color-text)]">
                        {isRegister
                            ? "Tạo tài khoản"
                            : "Đăng nhập"}
                    </h2>

                    <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
                        {isRegister
                            ? "Đăng ký để bắt đầu sử dụng QuizPDF."
                            : "Đăng nhập để tiếp tục học tập."}
                    </p>
                </div>

                {!isSupabaseConfigured && (
                    <p
                        role="alert"
                        className="mb-5 rounded-lg border border-[var(--color-danger)] p-3 text-sm text-[var(--color-danger)]"
                    >
                        Thiếu cấu hình Supabase trong
                        frontend/.env.local.
                    </p>
                )}

                <form
                    onSubmit={(event) => {
                        void handleSubmit(event);
                    }}
                    className="space-y-5"
                >
                    {isRegister && (
                        <div>
                            <label
                                htmlFor="auth-name"
                                className="mb-2 block text-sm font-medium text-[var(--color-text)]"
                            >
                                Tên hiển thị
                            </label>

                            <input
                                id="auth-name"
                                type="text"
                                autoComplete="name"
                                value={name}
                                required
                                minLength={2}
                                maxLength={80}
                                disabled={submitting}
                                onChange={(event) =>
                                    setName(event.target.value)
                                }
                                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-4 py-3 text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                            />
                        </div>
                    )}

                    <div>
                        <label
                            htmlFor="auth-email"
                            className="mb-2 block text-sm font-medium text-[var(--color-text)]"
                        >
                            Email
                        </label>

                        <input
                            id="auth-email"
                            type="email"
                            autoComplete="email"
                            value={email}
                            required
                            disabled={submitting}
                            onChange={(event) =>
                                setEmail(event.target.value)
                            }
                            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-4 py-3 text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                        />
                    </div>

                    <div>
                        <label
                            htmlFor="auth-password"
                            className="mb-2 block text-sm font-medium text-[var(--color-text)]"
                        >
                            Mật khẩu
                        </label>

                        <input
                            id="auth-password"
                            type="password"
                            autoComplete={
                                isRegister
                                    ? "new-password"
                                    : "current-password"
                            }
                            value={password}
                            required
                            minLength={isRegister ? 8 : undefined}
                            disabled={submitting}
                            onChange={(event) =>
                                setPassword(event.target.value)
                            }
                            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-4 py-3 text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                        />
                    </div>

                    {error && (
                        <p
                            role="alert"
                            className="rounded-lg border border-[var(--color-danger)] p-3 text-sm text-[var(--color-danger)]"
                        >
                            {error}
                        </p>
                    )}

                    {message && (
                        <p
                            role="status"
                            className="rounded-lg border border-[var(--color-success)] p-3 text-sm text-[var(--color-text)]"
                        >
                            {message}
                        </p>
                    )}

                    <button
                        type="submit"
                        disabled={
                            submitting ||
                            !isSupabaseConfigured
                        }
                        className="w-full rounded-lg bg-[var(--color-primary)] px-4 py-3 font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {submitting
                            ? "Đang xử lý..."
                            : isRegister
                                ? "Tạo tài khoản"
                                : "Đăng nhập"}
                    </button>
                </form>

                <div className="mt-7 border-t border-[var(--color-border)] pt-6 text-center text-sm text-[var(--color-text-secondary)]">
                    {isRegister
                        ? "Đã có tài khoản? "
                        : "Chưa có tài khoản? "}

                    <Link
                        to={
                            isRegister
                                ? "/login"
                                : "/register"
                        }
                        state={location.state}
                        className="font-semibold text-[var(--color-primary)] hover:underline"
                    >
                        {isRegister
                            ? "Đăng nhập"
                            : "Đăng ký"}
                    </Link>
                </div>
            </section>
        </div>
    );
}
