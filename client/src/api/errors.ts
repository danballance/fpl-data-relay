export class RelayApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly path: string;
  readonly code: string | undefined;
  readonly retryAfterSeconds: number | undefined;

  constructor({
    status,
    detail,
    path,
    code,
    retryAfterSeconds,
  }: {
    status: number;
    detail: string;
    path: string;
    code?: string;
    retryAfterSeconds?: number;
  }) {
    super(detail);
    this.name = "RelayApiError";
    this.status = status;
    this.detail = detail;
    this.path = path;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof RelayApiError) {
    return error.detail;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "An unknown error occurred.";
}
