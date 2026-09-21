"use server";

import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import { endSession, startSession } from "@/lib/session";

export interface AuthState {
  error?: string;
}

/**
 * Sign in, or register and sign in.
 *
 * The token never reaches the browser: it is exchanged here, on the server,
 * and stored in an httpOnly cookie (spec §31).
 */
export async function signIn(_: AuthState, formData: FormData): Promise<AuthState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const register = formData.get("intent") === "register";

  if (!email || !password) {
    return { error: "이메일과 비밀번호를 입력해 주세요." };
  }

  try {
    if (register) await api.register(email, password);
    const token = await api.login(email, password);
    await startSession(token.access_token, token.expires_in);
  } catch (error) {
    if (error instanceof ApiError) {
      // A failure the server could not have prevented is not the user's
      // mistake. Shown in the API's English beside "wrong password", it invites
      // them to keep retyping a password that was always correct.
      if (error.code === "database-unavailable") {
        return {
          error:
            "서버가 데이터베이스에 연결되지 않아 계정을 만들거나 로그인할 수 없습니다. " +
            "입력하신 정보의 문제가 아닙니다.",
        };
      }
      if (error.status >= 500) {
        return {
          error: "서버에서 예기치 않은 오류가 발생했습니다. 입력하신 정보의 문제가 아닙니다.",
        };
      }
      // The API's own wording says which rule was broken — a password that is
      // too short, an address already registered — so it is shown as-is.
      return { error: error.message };
    }
    return { error: "API에 연결할 수 없습니다. 백엔드가 실행 중인지 확인해 주세요." };
  }

  redirect("/projects");
}

export async function signOut(): Promise<void> {
  await endSession();
  redirect("/login");
}
