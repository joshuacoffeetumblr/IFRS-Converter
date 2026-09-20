"use client";

import { useActionState } from "react";
import { FormError } from "@/components/FormError";
import { SubmitButton } from "@/components/SubmitButton";
import { signIn, type AuthState } from "./actions";

/**
 * Sign in (spec §31).
 *
 * One form for both sign-in and registration: at this stage of the product
 * there is no invitation flow to separate them, and two near-identical screens
 * would be two places to get the password rules wrong.
 */
export default function LoginPage() {
  const [state, action] = useActionState<AuthState, FormData>(signIn, {});

  return (
    <main className="mx-auto max-w-sm px-4 py-24 sm:px-6">
      <p className="text-xs uppercase tracking-[0.18em] text-muted">IFRS 18</p>
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">Impact Analyzer</h1>
      <p className="mt-3 text-sm leading-relaxed text-muted">
        분석은 업로드한 사람에게만 보입니다. 계정이 없다면 아래에서 바로 만들 수 있습니다.
      </p>

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
          <SubmitButton
            name="intent"
            value="register"
            variant="secondary"
            pendingLabel="생성 중…"
          >
            계정 만들기
          </SubmitButton>
        </div>
      </form>
    </main>
  );
}
