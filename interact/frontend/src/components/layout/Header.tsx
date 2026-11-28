import { useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../../contexts/AuthContext";
import { DarkModeToggle } from "../ui/DarkModeToggle";

export function Header() {
    const { user, logout } = useAuth();

    const [signingOut, setSigningOut] =
        useState(false);

    const [error, setError] =
        useState<string | null>(null);

    const rawName =
        user?.user_metadata?.display_name;

    const displayName =
        typeof rawName === "string" && rawName.trim()
            ? rawName.trim()
            : user?.email || "Tài khoản";

    async function handleLogout(): Promise<void> {
        if (signingOut) {
            return;
        }

        setSigningOut(true);
        setError(null);

        try {
            await logout();
        } catch (reason) {
            setError(
                reason instanceof Error
                    ? reason.message
                    : "Không thể đăng xuất.",
            );
        } finally {
            setSigningOut(false);
        }
    }

    return (
        <header className="fixed top-0 right-0 left-0 z-50 h-16 border-b border-[var(--color-border)] bg-[var(--color-bg)] shadow-sm">
            <div className="mx-auto flex h-full max-w-7xl items-center justify-between gap-4 px-4 sm:px-6">
                <Link
                    to="/"
                    className="flex shrink-0 items-center gap-1 hover:opacity-80"
                >
          <span className="text-xl font-bold text-[var(--color-text)]">
            Quiz
          </span>
                    <span className="text-xl font-bold text-[var(--color-primary)]">
            PDF
          </span>
                </Link>

                <nav className="flex min-w-0 items-center gap-3 sm:gap-5">
                    <Link
                        to="/stats"
                        className="text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text)]"
                    >
                        Thống kê
                    </Link>

                    <Link
                        to="/history"
                        className="text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text)]"
                    >
                        Lịch sử PDF
                    </Link>

                    <Link
                        to="/ai-practice"
                        className="text-sm font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text)]"
                    >
                        AI Practice
                    </Link>

                    <span
                        title={user?.email || ""}
                        className="hidden max-w-36 truncate text-sm text-[var(--color-text)] lg:block"
                    >
            {displayName}
          </span>

                    <button
                        type="button"
                        disabled={signingOut}
                        onClick={() => {
                            void handleLogout();
                        }}
                        className="shrink-0 rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm font-medium text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
                    >
                        {signingOut
                            ? "Đang thoát..."
                            : "Đăng xuất"}
                    </button>

                    <DarkModeToggle />
                </nav>
            </div>

            {error && (
                <p
                    role="alert"
                    className="absolute right-4 top-full mt-2 max-w-sm rounded-lg border border-[var(--color-danger)] bg-[var(--color-surface)] p-3 text-sm text-[var(--color-danger)] shadow-lg"
                >
                    {error}
                </p>
            )}
        </header>
    );
}
