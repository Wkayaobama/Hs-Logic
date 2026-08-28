import { useEffect, useState, type ReactNode } from "react";
import { apiGet, ApiError } from "../api/client";
import type { ListEnvelope } from "../api/types";
import Spinner from "./Spinner";
import ErrorState from "./ErrorState";
import { formatNumber } from "../lib/format";

interface RecordListProps<T> {
  path: string;
  limit?: number;
  header: ReactNode;
  renderRow: (row: T) => ReactNode;
  rowKey: (row: T) => string;
  emptyMessage?: string;
}

export default function RecordList<T>({
  path,
  limit = 50,
  header,
  renderRow,
  rowKey,
  emptyMessage = "No records found.",
}: RecordListProps<T>) {
  const [rows, setRows] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [nextAfter, setNextAfter] = useState<string | null | undefined>(
    undefined
  );
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    apiGet<ListEnvelope<T>>(`${path}?limit=${limit}`)
      .then((envelope) => {
        if (cancelled) return;
        setRows(envelope.results);
        setTotal(envelope.total);
        setNextAfter(envelope.next_after);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? err
            : new ApiError(0, err instanceof Error ? err.message : String(err))
        );
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [path, limit, attempt]);

  const loadMore = () => {
    if (!nextAfter || loadingMore) return;
    setLoadingMore(true);
    apiGet<ListEnvelope<T>>(`${path}?limit=${limit}&after=${nextAfter}`)
      .then((envelope) => {
        setRows((prev) => [...prev, ...envelope.results]);
        setTotal(envelope.total);
        setNextAfter(envelope.next_after);
        setLoadingMore(false);
      })
      .catch((err: unknown) => {
        setError(
          err instanceof ApiError
            ? err
            : new ApiError(0, err instanceof Error ? err.message : String(err))
        );
        setLoadingMore(false);
      });
  };

  if (loading && rows.length === 0) {
    return <Spinner />;
  }

  if (error && rows.length === 0) {
    return <ErrorState error={error} onRetry={() => setAttempt((a) => a + 1)} />;
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-500">
            {header}
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row) => (
              <tr key={rowKey(row)} className="hover:bg-gray-50">
                {renderRow(row)}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <p className="text-center text-sm text-gray-500 py-10">
            {emptyMessage}
          </p>
        )}
      </div>

      {(nextAfter || rows.length > 0) && (
        <div className="flex items-center justify-between px-5 py-3 border-t border-gray-100">
          <span className="text-xs text-gray-400">
            Showing {formatNumber(rows.length)} of {formatNumber(total)}
          </span>
          {nextAfter && (
            <button
              type="button"
              onClick={loadMore}
              disabled={loadingMore}
              className="inline-flex items-center rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 transition-colors"
            >
              {loadingMore ? "Loading…" : "Load more"}
            </button>
          )}
        </div>
      )}

      {error && rows.length > 0 && (
        <div className="px-5 py-3 border-t border-red-100 bg-red-50 text-xs text-red-700">
          Failed to load more: {error.detail}
        </div>
      )}
    </div>
  );
}
