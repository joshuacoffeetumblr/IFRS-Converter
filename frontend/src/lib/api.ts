/**
 * Backend client.
 *
 * Architecture §13: this layer transports values. It does not compute them.
 * Monetary values arrive as strings (API spec §1) and stay strings until a
 * formatter renders them — parsing them into JavaScript numbers would silently
 * lose the precision the reconciliation gate depends on.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/api${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new ApiError(`Request to ${path} failed`, response.status);
  }

  return (await response.json()) as T;
}

export interface Disclaimer {
  disclaimer_en: string;
  disclaimer_ko: string;
  limitations_en: string[];
}

export interface Health {
  status: "ok";
  environment: string;
  version: string;
}

export const api = {
  health: () => request<Health>("/health"),
  disclaimer: () => request<Disclaimer>("/meta/disclaimer"),
};
