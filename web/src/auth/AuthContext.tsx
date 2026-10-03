import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, UNAUTHORIZED_EVENT, type PublicConfig, type User } from "../api";
import { clearToken, getToken, setToken } from "./session";

interface AuthState {
  user: User | null;
  config: PublicConfig | null;
  loading: boolean;
  configError: string | null;
  signIn(token: string, user?: User): Promise<void>;
  signOut(): void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const c = await api.publicConfig();
        if (alive) setConfig(c);
      } catch (e) {
        if (alive) setConfigError((e as Error).message);
      }
      if (getToken()) {
        try {
          const u = await api.me();
          if (alive) setUser(u);
        } catch {
          clearToken();
        }
      }
      if (alive) setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, []);

  const signOut = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  useEffect(() => {
    const h = () => signOut();
    window.addEventListener(UNAUTHORIZED_EVENT, h);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, h);
  }, [signOut]);

  const signIn = useCallback(async (token: string, u?: User) => {
    setToken(token);
    setUser(u ?? (await api.me()));
  }, []);

  const value = useMemo(() => ({ user, config, loading, configError, signIn, signOut }), [user, config, loading, configError, signIn, signOut]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

/** Convenience for screens behind RequireAuth. */
export function useUser(): User {
  const { user } = useAuth();
  if (!user) throw new Error("useUser without a signed-in user");
  return user;
}
