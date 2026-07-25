export class RelayApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly path: string;

  constructor({
    status,
    detail,
    path,
  }: {
    status: number;
    detail: string;
    path: string;
  }) {
    super(detail);
    this.name = "RelayApiError";
    this.status = status;
    this.detail = detail;
    this.path = path;
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
