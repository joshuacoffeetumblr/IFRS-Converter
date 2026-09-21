"use client";

import { useActionState } from "react";
import { FormError } from "@/components/FormError";
import { SubmitButton } from "@/components/SubmitButton";
import { signIn, type AuthState } from "./actions";

/**
 * The sign-in form (spec §31).
 *
 * One form for both sign-in and registration: at this stage of the product
 * there is no invitation flow to separate them, and two near-identical screens
 * would be two places to get the password rules wrong.
 *
 * The page around it is a server component, so that it can say whether the API
 * and its database are reachable before anyone types a password.
 */
export function LoginForm() {
  const [state, action] = useActionState<AuthState, FormData>(signIn, {});

  return (
    <form action={action} className="mt-8 space-y-4">
      <div>
        <label htmlFor="email" className="block text-xs font-medium text-muted">
          이메일
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
          className="mt-1 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
        />
      </div>

      <div>
        <label htmlFor="password" className="block text-xs font-medium text-muted">
          비밀번호
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          minLength={12}
          className="mt-1 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
        />
        <p className="mt-1 text-xs text-muted">
          12자 이상. 길이가 구성 규칙보다 실제로 더 중요합니다.
        </p>
      </div>

      <FormError message={state.error} />

      <div className="flex gap-2 pt-2">
        <SubmitButton name="intent" value="login" pendingLabel="확인 중…">
          로그인
        </SubmitButton>
        <SubmitButton name="intent" value="register" variant="secondary" pendingLabel="생성 중…">
          계정 만들기
        </SubmitButton>
      </div>
    </form>
  );
}
