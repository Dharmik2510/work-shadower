import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";

export interface AsyncState<T> {
  data: T | undefined;
  error: Error | null;
  loading: boolean;
  reload(): void;
  setData(v: T | ((prev: T | undefined) => T)): void;
}

/** Runs an async loader whenever deps change; ignores stale responses. */
export function useAsync<T>(fn: () => Promise<T>, deps: DependencyList): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const run = useRef(0);

  useEffect(() => {
    const id = ++run.current;
    setLoading(true);
    fn().then(
      (d) => {
        if (id !== run.current) return;
        setData(d);
        setError(null);
        setLoading(false);
      },
      (e: Error) => {
        if (id !== run.current) return;
        setError(e);
        setLoading(false);
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload, setData: setData as AsyncState<T>["setData"] };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
