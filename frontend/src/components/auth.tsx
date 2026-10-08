"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { API_URL } from "@/lib/api";

export interface MockUser {
  id: number;
  name: string;
  email: string;
}

interface AuthState {
  user: MockUser | null;
  token: string | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

const TOKEN_KEY = "vengage_mock_token";
const USER_KEY = "vengage_mock_user";

async function authFetch(path: string, body: unknown): Promise<{ token: string; user: MockUser }> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    let msg = text.slice(0, 300);
    try {
      const j = JSON.parse(text);
      if (typeof j?.detail === "string") msg = j.detail;
    } catch {
      /* keep raw text */
    }
    throw new Error(`${res.status}: ${msg}`);
  }
  return res.json();
}

export function AuthProvider({ children }: { children: ReactNode }) {
  // Lazy init from localStorage (no setState-in-effect): mock session only.
  const [user, setUser] = useState<MockUser | null>(() => {
    try {
      if (typeof window === "undefined") return null;
      const u = localStorage.getItem(USER_KEY);
      return u ? (JSON.parse(u) as MockUser) : null;
    } catch {
      return null;
    }
  });
  const [token, setToken] = useState<string | null>(() => {
    try {
      if (typeof window === "undefined") return null;
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  });
  const [ready, setReady] = useState(false);

  // Mark ready after first client render (hydration-safe, no state read).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setReady(true);
  }, []);

  const persist = useCallback((t: string, u: MockUser) => {
    setToken(t);
    setUser(u);
    try {
      localStorage.setItem(TOKEN_KEY, t);
      localStorage.setItem(USER_KEY, JSON.stringify(u));
    } catch {
      /* storage unavailable */
    }
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const r = await authFetch("/api/auth/login", { email, password });
      persist(r.token, r.user);
    },
    [persist]
  );

  const register = useCallback(
    async (name: string, email: string, password: string) => {
      const r = await authFetch("/api/auth/register", { name, email, password });
      persist(r.token, r.user);
    },
    [persist]
  );

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo(
    () => ({ user, token, ready, login, register, logout }),
    [user, token, ready, login, register, logout]
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

const PUBLIC_PATHS = ["/login", "/register"];

/** Client-side gate: mock session only — API calls stay open either way. */
export function AuthGuard({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  const isPublic = PUBLIC_PATHS.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`)
  );

  useEffect(() => {
    if (!ready) return;
    if (!user && !isPublic) router.replace("/login");
    if (user && isPublic) router.replace("/");
  }, [ready, user, isPublic, router]);

  if (!ready) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }
  if (!user && !isPublic) return null;
  return <>{children}</>;
}
