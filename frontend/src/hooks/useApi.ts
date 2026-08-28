import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, ApiError } from "../api/client";

export interface UseApiResult<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  reload: (refresh?: boolean) => void;
}

function withRefreshParam(path: string): string {
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}refresh=true`;
}

/**
 * Fetches `path` on mount and whenever it changes. Calling `reload(true)`
 * re-fetches with a `refresh=true` query param appended (used to bust
 * server-side caches on health tabs) while keeping the previously loaded
 * data visible instead of blanking the UI.
 */
export function useApi<T>(path: string): UseApiResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const requestSeq = useRef(0);
  const mounted = useRef(true);

  const fetchData = useCallback(
    (refresh: boolean) => {
      const seq = ++requestSeq.current;
      setLoading(true);
      setError(null);

      const target = refresh ? withRefreshParam(path) : path;

      apiGet<T>(target)
        .then((result) => {
          if (!mounted.current || seq !== requestSeq.current) return;
          setData(result);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (!mounted.current || seq !== requestSeq.current) return;
          setError(
            err instanceof ApiError
              ? err
              : new ApiError(0, err instanceof Error ? err.message : String(err))
          );
          setLoading(false);
        });
    },
    [path]
  );

  useEffect(() => {
    mounted.current = true;
    fetchData(false);
    return () => {
      mounted.current = false;
    };
  }, [fetchData]);

  const reload = useCallback(
    (refresh = false) => {
      fetchData(refresh);
    },
    [fetchData]
  );

  return { data, loading, error, reload };
}
