import { cookies } from "next/headers";

/**
 * The session token, kept out of JavaScript entirely.
 *
 * The bearer token lives in an httpOnly cookie and is read only on the server
 * (spec §31). The alternative — `localStorage` — hands the token to any script
 * that manages to run on the page, and this product holds unpublished
 * financial statements. Nothing in the browser ever sees it, so nothing in the
 * browser can leak it.
 */

export const SESSION_COOKIE = "ifrs18_session";

export async function readToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

export async function startSession(token: string, expiresInSeconds: number): Promise<void> {
  const store = await cookies();
  store.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    // Set over TLS only outside development, where there is no TLS to use.
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: expiresInSeconds,
  });
}

export async function endSession(): Promise<void> {
  const store = await cookies();
  store.delete(SESSION_COOKIE);
}
