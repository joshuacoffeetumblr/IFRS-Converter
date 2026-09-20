"use client";

import { useActionState } from "react";
import { FormError } from "@/components/FormError";
import { SubmitButton } from "@/components/SubmitButton";
import { createProject, type CreateState } from "./actions";

const THIS_YEAR = new Date().getFullYear();

export function NewProjectForm() {
  const [state, action] = useActionState<CreateState, FormData>(createProject, {});

  return (
    <section className="mt-12 rounded-md border border-border bg-surface p-5">
      <h2 className="text-sm font-medium">새 분석 시작</h2>
      <p className="mt-1 text-xs text-muted">
        하나의 프로젝트는 하나의 기업, 하나의 보고기간을 다룹니다.
      </p>

      <form action={action} className="mt-5 grid gap-4 sm:grid-cols-2">
        <Field label="프로젝트명" name="name" required placeholder="2025 연결 손익계산서" />
        <Field label="회사명" name="company_name" required placeholder="○○ 주식회사" />
        <Field
          label="사업자등록번호"
          name="identifier"
          placeholder="1248100998 (선택)"
          hint="입력하면 같은 기업의 다른 기간 분석과 연결됩니다."
        />
        <Field
          label="회계연도"
          name="fiscal_year"
          type="number"
          defaultValue={String(THIS_YEAR)}
          required
        />
        <Field label="기초일" name="period_start" type="date" defaultValue={`${THIS_YEAR}-01-01`} />
        <Field label="기말일" name="period_end" type="date" defaultValue={`${THIS_YEAR}-12-31`} />

        <label className="block text-xs font-medium text-muted">
          작성 기준
          <select
            name="basis"
            defaultValue="CONSOLIDATED"
            className="mt-1 w-full rounded-md border border-border bg-canvas px-3 py-2 text-sm text-ink"
          >
            <option value="CONSOLIDATED">연결</option>
            <option value="SEPARATE">별도</option>
          </select>
        </label>

        <label className="block text-xs font-medium text-muted">
          표시 단위
          <select
            name="presentation_scale"
            defaultValue="6"
            className="mt-1 w-full rounded-md border border-border bg-canvas px-3 py-2 text-sm text-ink"
          >
            <option value="0">원</option>
            <option value="3">천원</option>
            <option value="6">백만원</option>
            <option value="9">십억원</option>
          </select>
          <span className="mt-1 block font-normal">
            원본 문서에 표시된 단위와 같게 맞추세요.
          </span>
        </label>

        <div className="sm:col-span-2">
          <FormError message={state.error} />
          <div className="mt-3">
            <SubmitButton pendingLabel="생성 중…">프로젝트 만들기</SubmitButton>
          </div>
        </div>
      </form>
    </section>
  );
}

function Field({
  label,
  name,
  hint,
  ...props
}: { label: string; name: string; hint?: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block text-xs font-medium text-muted">
      {label}
      <input
        name={name}
        {...props}
        className="mt-1 w-full rounded-md border border-border bg-canvas px-3 py-2 text-sm text-ink"
      />
      {hint ? <span className="mt-1 block font-normal">{hint}</span> : null}
    </label>
  );
}
