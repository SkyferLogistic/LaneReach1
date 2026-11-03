/**
 * Typed API client (generated types from the backend's OpenAPI document).
 *
 * - The bearer token comes from the session (set on login).
 * - Every side-effecting request (POST/PUT/PATCH/DELETE) carries a fresh `Idempotency-Key`, so
 *   a retried click never performs an action twice.
 * - Errors are RFC 7807 problems; `unwrap` turns them into `ApiError`.
 */
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

export interface ProblemFieldError {
  location?: (string | number)[];
  message?: string;
  code?: string;
}

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string | null;
  errors?: ProblemFieldError[];
  request_id?: string | null;
}

/** A failed API call with its problem document. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;

  constructor(status: number, problem: Problem) {
    super(problem.detail || problem.title || `Request failed (${status})`);
    this.status = status;
    this.problem = problem;
  }
}

let currentToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

/** Set by the session provider. */
export function setApiToken(token: string | null): void {
  currentToken = token;
}

/** Called when the API answers 401 (expired or revoked token). */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const authMiddleware: Middleware = {
  onRequest({ request }) {
    if (currentToken) request.headers.set("Authorization", `Bearer ${currentToken}`);
    if (request.method !== "GET" && !request.headers.has("Idempotency-Key")) {
      request.headers.set("Idempotency-Key", newIdempotencyKey());
    }
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401 && currentToken && onUnauthorized) onUnauthorized();
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: "" });
api.use(authMiddleware);

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** Return the data or throw an `ApiError` built from the problem document. */
export async function unwrap<T>(call: Promise<FetchResult<T>>): Promise<T> {
  const { data, error, response } = await call;
  if (!response.ok) {
    const problem: Problem =
      error && typeof error === "object" ? (error as Problem) : { title: response.statusText };
    throw new ApiError(response.status, problem);
  }
  return data as T;
}

/** Human-readable error text, including field errors. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const fields = (error.problem.errors ?? [])
      .map((field) => {
        const where = (field.location ?? []).filter((part) => part !== "body").join(".");
        return where ? `${where}: ${field.message ?? ""}` : (field.message ?? "");
      })
      .filter(Boolean);
    return [error.message, ...fields].join("\n");
  }
  return error instanceof Error ? error.message : String(error);
}
