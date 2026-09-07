"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, getToken, setToken, type Me, type Session } from "./api";

interface Store {
  me: Me | null;
  loading: boolean;
  error: string | null;
  session: Session | null;
  refresh: () => Promise<void>;
  login: (email: string) => Promise<void>;
  logout: () => void;
  startScenario: (id: string) => Promise<Session>;
}

const Ctx = createContext<Store | null>(null);

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      const m = await api<Me>("/api/me");
      setMe(m);
      setError(null);
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (status === 401) {
        setToken(null);
        setMe(null);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    async (email: string) => {
      const r = await api<{ token: string }>("/api/auth/login", { method: "POST", json: { email } });
      setToken(r.token);
      await refresh();
    },
    [refresh],
  );

  const logout = useCallback(() => {
    setToken(null);
    setMe(null);
  }, []);

  const startScenario = useCallback(
    async (id: string) => {
      const s = await api<Session>(`/api/scenarios/${id}/start`, { method: "POST" });
      await refresh();
      return s;
    },
    [refresh],
  );

  const value = useMemo<Store>(
    () => ({ me, loading, error, session: me?.active_session ?? null, refresh, login, logout, startScenario }),
    [me, loading, error, refresh, login, logout, startScenario],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("StoreProvider missing");
  return s;
}

/** Poll a fetcher on an interval while mounted. */
export function usePoll<T>(fn: () => Promise<T>, intervalMs: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const run = async () => {
      try {
        const d = await fn();
        if (alive) {
          setData(d);
          setErr(null);
        }
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : String(e));
      }
    };
    void run();
    const id = window.setInterval(run, intervalMs);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, err, setData };
}
