import {
    createContext,
    useContext,
} from "react";

import type { User } from "@supabase/supabase-js";

export interface AuthContextValue {
    user: User | null;
    loading: boolean;
    login: (
        email: string,
        password: string,
    ) => Promise<void>;
    register: (
        email: string,
        password: string,
        displayName: string,
    ) => Promise<boolean>;
    logout: () => Promise<void>;
}

export const AuthContext =
    createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
    const context = useContext(AuthContext);

    if (!context) {
        throw new Error(
            "useAuth must be used within AuthProvider.",
        );
    }

    return context;
}
