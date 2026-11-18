import { createContext, useContext } from "react";

export type Theme = "light" | "dark";

interface ThemeContextValue {
  theme: Theme;
  toggleTheme: () => void;
}

export const ThemeContext =
    createContext<ThemeContextValue | undefined>(
        undefined,
    );

/**
 * Hook to access current theme and toggle function.
 * Must be used within ThemeProvider.
 */
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);

  if (!context) {
    throw new Error(
        "useTheme must be used within a ThemeProvider",
    );
  }

  return context;
}
