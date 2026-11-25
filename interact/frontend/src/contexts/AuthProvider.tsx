import {
    useCallback,
    useEffect,
    useMemo,
    useState,
    type ReactNode,
} from "react";

import type { User } from "@supabase/supabase-js";

import {
    AuthContext,
    type AuthContextValue,
} from "./AuthContext";

import {
    isSupabaseConfigured,
    requireSupabase,
    supabase,
} from "../services/supabase";

import { StatsService } from "../services/stats";

interface AuthProviderProps {
    children: ReactNode;
}

async function synchronizeProfile(
    user: User,
): Promise<void> {
    const client = requireSupabase();

    const rawDisplayName =
        user.user_metadata?.display_name;

    const displayName =
        typeof rawDisplayName === "string"
            ? rawDisplayName.trim()
            : "";

    const { error } = await client
        .from("profiles")
        .upsert(
            {
                id: user.id,
                display_name: displayName,
            },
            {
                onConflict: "id",
            },
        );

    if (error) {
        console.error(
            "Could not synchronize Supabase profile:",
            error.message,
        );
    }
}

export function AuthProvider({
                                 children,
                             }: AuthProviderProps) {
    const [user, setUser] = useState<User | null>(
        null,
    );

    const [loading, setLoading] = useState(
        isSupabaseConfigured,
    );

    const applyUser = useCallback(
        (nextUser: User | null) => {
            StatsService.setUserId(
                nextUser?.id ?? null,
            );

            setUser(nextUser);
            setLoading(false);
        },
        [],
    );

    useEffect(() => {
        if (!supabase) {
            StatsService.setUserId(null);
            return;
        }

        const client = supabase;
        let active = true;

        const {
            data: { subscription },
        } = client.auth.onAuthStateChange(
            (_event, session) => {
                if (!active) {
                    return;
                }

                const nextUser = session?.user ?? null;

                applyUser(nextUser);

                if (nextUser) {
                    // Run Supabase queries outside the auth callback.
                    window.setTimeout(() => {
                        if (active) {
                            void synchronizeProfile(nextUser);
                        }
                    }, 0);
                }
            },
        );

        void client.auth
            .getUser()
            .then(({ data, error }) => {
                if (!active) {
                    return;
                }

                if (error) {
                    console.warn(
                        "Could not validate current session:",
                        error.message,
                    );

                    applyUser(null);
                    return;
                }

                applyUser(data.user ?? null);
            })
            .catch((error: unknown) => {
                if (!active) {
                    return;
                }

                console.error(
                    "Could not initialize authentication:",
                    error,
                );

                applyUser(null);
            });

        return () => {
            active = false;
            subscription.unsubscribe();
        };
    }, [applyUser]);

    const login = useCallback(
        async (
            email: string,
            password: string,
        ): Promise<void> => {
            const client = requireSupabase();

            const { data, error } =
                await client.auth.signInWithPassword({
                    email,
                    password,
                });

            if (error) {
                throw new Error(error.message);
            }

            if (!data.user || !data.session) {
                throw new Error(
                    "Không nhận được phiên đăng nhập hợp lệ.",
                );
            }

            applyUser(data.user);
        },
        [applyUser],
    );

    const register = useCallback(
        async (
            email: string,
            password: string,
            displayName: string,
        ): Promise<boolean> => {
            const client = requireSupabase();

            const { data, error } =
                await client.auth.signUp({
                    email,
                    password,
                    options: {
                        data: {
                            display_name: displayName,
                        },
                    },
                });

            if (error) {
                throw new Error(error.message);
            }

            if (data.session && data.user) {
                applyUser(data.user);
                return true;
            }

            return false;
        },
        [applyUser],
    );

    const logout = useCallback(
        async (): Promise<void> => {
            const client = requireSupabase();

            const { error } = await client.auth.signOut();

            if (error) {
                throw new Error(error.message);
            }

            applyUser(null);
        },
        [applyUser],
    );

    const value = useMemo<AuthContextValue>(
        () => ({
            user,
            loading,
            login,
            register,
            logout,
        }),
        [
            user,
            loading,
            login,
            register,
            logout,
        ],
    );

    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    );
}
