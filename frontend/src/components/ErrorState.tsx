import { ApiError } from "../api/client";

interface ErrorStateProps {
  error: ApiError | Error;
  onRetry?: () => void;
}

export default function ErrorState({ error, onRetry }: ErrorStateProps) {
  const message =
    error instanceof ApiError
      ? `${error.status ? `(${error.status}) ` : ""}${error.detail}`
      : error.message;

  return (
    <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-center">
      <p className="text-sm text-red-700 mb-3">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 transition-colors"
        >
          Retry
        </button>
      )}
    </div>
  );
}
