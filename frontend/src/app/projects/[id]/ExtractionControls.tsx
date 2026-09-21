"use client";

import { useActionState } from "react";
import { FormError } from "@/components/FormError";
import { SubmitButton } from "@/components/SubmitButton";
import {
  finalizeProject,
  reopenProject,
  runClassification,
  runExtraction,
  uploadStatement,
  type ActionState,
} from "./actions";

/**
 * The four steps of the pipeline, in the order they run.
 *
 * Each is a separate action rather than one "do everything" button: a user has
 * to be able to look at what extraction produced before classifying it, and at
 * what classification proposed before finalizing it. That inspection is the
 * product.
 */
export function ExtractionControls({
  projectId,
  hasLines,
  status,
  canFinalize,
}: {
  projectId: string;
  hasLines: boolean;
  status: string;
  canFinalize: boolean;
}) {
  const finalized = status === "FINALIZED";

  return (
    <section className="grid gap-4 sm:grid-cols-2">
      <Step
        title="1. 재무제표 업로드"
        description="XBRL·XLSX·CSV·PDF. 형식은 파일 내용으로 판별하며, 같은 파일을 다시 올려도 중복 생성되지 않습니다. DART 공시의 XBRL 원본(.xbrl)을 올리면 계정과목을 글자로 맞출 필요가 없고, 주석의 금융수익·기타수익 내역까지 그대로 읽습니다."
        action={uploadStatement}
        projectId={projectId}
        disabled={finalized}
      >
        {/*
          No `accept` filter, deliberately.

          `.xbrl` has no registered type on any operating system, and a browser
          handed an extension it does not recognise does not fall back to
          showing everything — Safari and the mobile pickers grey the file out
          instead. The file the person came to upload becomes the one file they
          cannot choose, with nothing on screen to say why.

          Nothing is lost by dropping it. The filter was never what decides the
          format: the API sniffs the leading bytes and ignores the filename
          entirely (spec §31), and it refuses anything else by name. A filter
          that silently hides the right file is worse than no filter, and the
          accepted formats are written above where they can be read.
        */}
        <input
          type="file"
          name="file"
          required
          className="block w-full text-xs text-muted file:mr-3 file:rounded-md file:border file:border-border file:bg-canvas file:px-3 file:py-1.5 file:text-xs file:text-ink"
        />
        <SubmitButton variant="secondary" pendingLabel="업로드 중…" disabled={finalized}>
          업로드
        </SubmitButton>
      </Step>

      <Step
        title="2. 손익계산서 추출"
        description="시트와 열은 자동으로 찾습니다. 추출 결과는 문서가 인쇄한 소계와 대조해 검증합니다."
        action={runExtraction}
        projectId={projectId}
        disabled={finalized}
      >
        <SubmitButton variant="secondary" pendingLabel="추출 중…" disabled={finalized}>
          추출 실행
        </SubmitButton>
      </Step>

      <Step
        title="3. IFRS 18 분류"
        description="규칙이 판단할 수 있는 것을 판단하고, 판단할 수 없는 것은 사람에게 넘깁니다. 다시 실행해도 사람의 결정은 유지됩니다."
        action={runClassification}
        projectId={projectId}
        disabled={!hasLines || finalized}
      >
        <SubmitButton pendingLabel="분류 중…" disabled={!hasLines || finalized}>
          분류 실행
        </SubmitButton>
      </Step>

      {finalized ? (
        <Step
          title="4. 확정됨"
          description="확정된 분석은 이미 외부에 전달되었을 수 있습니다. 수정하려면 먼저 검토 상태로 되돌리세요."
          action={reopenProject}
          projectId={projectId}
        >
          <SubmitButton variant="secondary" pendingLabel="되돌리는 중…">
            검토 상태로 되돌리기
          </SubmitButton>
        </Step>
      ) : (
        <Step
          title="4. 확정"
          description="모든 검토와 질문이 끝나야 실행할 수 있습니다. 정합성 검증을 통과하지 못하면 확정되지 않습니다."
          action={finalizeProject}
          projectId={projectId}
          disabled={!canFinalize}
        >
          <SubmitButton pendingLabel="검증 중…" disabled={!canFinalize}>
            확정하기
          </SubmitButton>
          {!canFinalize ? (
            <p className="text-xs text-muted">검토 탭에 남은 항목이 있습니다.</p>
          ) : null}
        </Step>
      )}
    </section>
  );
}

function Step({
  title,
  description,
  action,
  projectId,
  children,
  disabled,
}: {
  title: string;
  description: string;
  action: (state: ActionState, formData: FormData) => Promise<ActionState>;
  projectId: string;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  const [state, formAction] = useActionState<ActionState, FormData>(action, {});

  return (
    <form action={formAction} className="rounded-md border border-border bg-surface p-4">
      <input type="hidden" name="project_id" value={projectId} />
      <h2 className="text-sm font-medium">{title}</h2>
      <p className="mt-1 text-xs leading-relaxed text-muted">{description}</p>
      {/* A fieldset so a step that cannot run is inert as a whole, rather than
          leaving a live file picker beside a disabled button. */}
      <fieldset
        disabled={disabled}
        className="mt-3 flex flex-wrap items-center gap-2 border-0 p-0 disabled:opacity-60"
      >
        {children}
      </fieldset>
      <FormError message={state.error} />
      {state.notice ? (
        <p className="mt-2 text-xs leading-relaxed text-positive" role="status">
          {state.notice}
        </p>
      ) : null}
    </form>
  );
}
