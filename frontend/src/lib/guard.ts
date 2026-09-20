import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import { readToken } from "@/lib/session";

/**
 * Every project screen goes through here.
 *
 * Ownership is enforced by the API — another user's project is a 404 there,
 * not a 403 — so this only checks that there is a session at all. The point is
 * to send a signed-out visitor to the sign-in page instead of rendering an
 * empty screen built from a failed request.
 */
export async function requireSession(): Promise<void> {
  if ((await readToken()) === null) redirect("/login");
}

/** Whether the current cookie still identifies a real user. */
export async function currentUser(): Promise<{ id: string; email: string } | null> {
  if ((await readToken()) === null) return null;
  try {
    return await api.me();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}
