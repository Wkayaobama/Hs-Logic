// Thin fetch wrapper for the FastAPI backend, mounted under /api/hubspot
// (proxied by Vite in dev, and by nginx in the production container).

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function stringifyDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail == null) return "Unknown error";
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

async function extractErrorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (
      body &&
      typeof body === "object" &&
      "detail" in (body as Record<string, unknown>)
    ) {
      return stringifyDetail((body as Record<string, unknown>).detail);
    }
    return stringifyDetail(body);
  } catch {
    return res.statusText || `Request failed with status ${res.status}`;
  }
}

const BASE = "/api/hubspot";

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new ApiError(res.status, await extractErrorDetail(res));
  }
  return (await res.json()) as T;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await extractErrorDetail(res));
  }
  return (await res.json()) as T;
}
