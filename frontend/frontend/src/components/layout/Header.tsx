import { Link } from "react-router-dom";
import { DarkModeToggle } from "../ui/DarkModeToggle";

/**
 * Top navigation bar for the application.
 */
export function Header() {
  return (
    <header className="fixed top-0 left-0 right-0 h-16 bg-[var(--color-bg)] border-b border-[var(--color-border)] z-50 flex items-center justify-between px-6 shadow-sm">
      <Link to="/" className="flex items-center gap-1 hover:opacity-80 transition-opacity">
        <span className="text-xl font-bold text-[var(--color-text)] tracking-tight">Quiz</span>
        <span className="text-xl font-bold text-[var(--color-primary)] tracking-tight">PDF</span>
      </Link>

      <div className="flex items-center gap-4">
        <DarkModeToggle />
      </div>
    </header>
  );
}
