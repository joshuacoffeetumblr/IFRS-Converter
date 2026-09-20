"use client";

import { useActionState, useState } from "react";
import type { BusinessActivity, Classification, Question } from "@/lib/api";
import { Figure } from "@/components/Figure";
import { FormError } from "@/components/FormError";
import { StatusPill } from "@/components/StatusPill";
import { SubmitButton } from "@/components/SubmitButton";
import { answerQuestion, reviewClassification, setActivity, type ReviewState } from "./actions";

const CATEGORIES = [
  ["OPERATING", "영업"],
  ["INVESTING", "투자"],
  ["FINANCING", "재무"],
  ["INCOME_TAX", "법인세"],
  ["DISCONTINUED_OPERATION", "중단영업"],
] as const;

const CATEGORY_LABELS: Record<string, string> = {
  ...Object.fromEntries(CATEGORIES),
  // Not offered as a choice — it is what the engine says when it could not
  // decide — but it still has to read as Korean where it is displayed.
  UNCLASSIFIED: "미분류",
};

/**
 * One item in the review queue.
 *
 * Everything the engine knows is on the card before any decision is asked for:
 * the account, the figure, what the engine proposed, which rule said so and
 * with what citation. A reviewer should not have to click to find out what
 * they are agreeing with.
 */
export function ReviewForm({
  projectId,
  classification,
}: {
  projectId: string;
  classification: Classification;
}) {
  const [state, action] = useActionState<ReviewState, FormData>(reviewClassification, {});
  const [overriding, setOverriding] = useState(false);

  const proposed = classification.final_ifrs18_category ?? classification.proposed_ifrs18_category;
  // There is nothing to agree with: the engine did not place this line, and
  // operating is IFRS 18's residual category, so leaving it unplaced is not a
  // classification. The gate refuses it, so the screen does not offer it.
  const unplaced = proposed === "UNCLASSIFIED" || proposed === null;

  return (
    <form
      action={action}
      className="rounded-md border border-border bg-surface p-4"
      data-testid="review-card"
      // The account names the card, and an unplaced one is marked as such:
      // both are what a reviewer picks a card by, and what a test does too.
      data-account={classification.original_account}
      data-unplaced={unplaced}
    >
      <input type="hidden" name="project_id" value={projectId} />
      <input type="hidden" name="classification_id" value={classification.id} />

      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <p className="text-sm font-medium">{classification.original_account}</p>
          <p className="mt-0.5 text-xs text-muted">
            {classification.normalized_account_code ?? "정규 계정 미지정"}
            {classification.confidence_band ? ` · 확신도 ${classification.confidence_band}` : ""}
          </p>
        </div>
        <div className="text-right">
          <Figure value={classification.amount} className="text-base" />
          <p className="mt-0.5 text-xs text-muted">
            영업이익 영향 <Figure value={classification.impact_on_operating_profit} signed />
          </p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <StatusPill tone={proposed === "UNCLASSIFIED" ? "warn" : "active"}>
          제안: {CATEGORY_LABELS[proposed ?? ""] ?? proposed ?? "없음"}
        </StatusPill>
        {classification.rule_id ? (
          <span className="text-xs text-muted">
            {classification.rule_id}
            {classification.rule_source_reference
              ? ` · ${classification.rule_source_reference}`
              : ""}
          </span>
        ) : null}
      </div>

      {classification.ai_reasoning ? (
        <p className="mt-2 rounded border border-border bg-canvas px-3 py-2 text-xs leading-relaxed text-muted">
          {classification.ai_reasoning}
        </p>
      ) : null}

      {overriding ? (
        <div className="mt-4 space-y-3 border-t border-border pt-3">
          <label className="block text-xs font-medium text-muted">
            IFRS 18 범주
            <select
              name="final_ifrs18_category"
              defaultValue={proposed === "UNCLASSIFIED" ? "OPERATING" : (proposed ?? "OPERATING")}
              className="mt-1 w-full rounded-md border border-border bg-canvas px-3 py-2 text-sm text-ink"
            >
              {CATEGORIES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium text-muted">
            사유 (필수)
            <input
              name="override_reason"
              required
              placeholder="예: 주석 24 확인 결과 영업 관련 수익"
              className="mt-1 w-full rounded-md border border-border bg-canvas px-3 py-2 text-sm text-ink"
            />
            <span className="mt-1 block font-normal">
              사유 없는 수동 분류는 감사 추적이 되지 않으므로 기록되지 않습니다.
            </span>
          </label>
          <div className="flex gap-2">
            <SubmitButton name="action" value="OVERRIDDEN" pendingLabel="기록 중…">
              이 범주로 분류
            </SubmitButton>
            <button
              type="button"
              onClick={() => setOverriding(false)}
              className="rounded-md border border-border px-4 py-2 text-sm text-muted"
            >
              취소
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-4 border-t border-border pt-3">
          {unplaced ? (
            <p className="mb-2 text-xs leading-relaxed text-caution">
              이 항목은 규칙이 범주를 정하지 못했습니다. 그대로 두면 확정할 수 없으므로,
              범주를 직접 지정하거나 보류하세요.
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {unplaced ? null : (
              <SubmitButton
                name="action"
                value="ACCEPTED"
                variant="secondary"
                pendingLabel="기록 중…"
              >
                제안 승인
              </SubmitButton>
            )}
            <button
              type="button"
              onClick={() => setOverriding(true)}
              className="rounded-md border border-border px-4 py-2 text-sm text-ink hover:bg-border/40"
            >
              {unplaced ? "범주 지정" : "다르게 분류"}
            </button>
            <SubmitButton name="action" value="DEFERRED" variant="secondary" pendingLabel="기록 중…">
              보류
            </SubmitButton>
          </div>
        </div>
      )}

      <FormError message={state.error} />
      {state.notice ? (
        <p className="mt-2 text-xs text-positive" role="status">
          {state.notice}
        </p>
      ) : null}
    </form>
  );
}

/**
 * A question a rule raised.
 *
 * Company-scoped questions are yes / no / not sure. Line-scoped ones (B65,
 * B72) ask *which* category the underlying item belongs to, so they offer the
 * categories and the standard's own undue-cost relief instead — "no" would
 * answer nothing there.
 */
export function QuestionForm({
  projectId,
  question,
}: {
  projectId: string;
  question: Question;
}) {
  const [state, action] = useActionState<ReviewState, FormData>(answerQuestion, {});
  const perLine = question.scope === "LINE";

  return (
    <form action={action} className="rounded-md border border-caution/40 bg-surface p-4">
      <input type="hidden" name="project_id" value={projectId} />
      <input type="hidden" name="question_id" value={question.id} />

      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm leading-relaxed">{question.question_text_ko}</p>
        {question.affected_amount ? (
          <p className="text-xs text-muted">
            영향 {question.affected_line_count}건 ·{" "}
            <Figure value={question.affected_amount} />
          </p>
        ) : null}
      </div>

      {question.help_ko ? (
        <p className="mt-2 rounded border border-border bg-canvas px-3 py-2 text-xs leading-relaxed text-muted">
          {question.help_ko}
        </p>
      ) : null}

      <p className="mt-2 text-[11px] text-muted">
        {question.raised_by_rule_id ? `규칙 ${question.raised_by_rule_id}` : ""}
      </p>

      {perLine ? (
        <div className="mt-3 space-y-3">
          <label className="block text-xs font-medium text-muted">
            해당 항목의 범주
            <select
              name="resolved_category"
              defaultValue="OPERATING"
              className="mt-1 w-full rounded-md border border-border bg-canvas px-3 py-2 text-sm text-ink"
            >
              {CATEGORIES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {question.allows_undue_cost_or_effort ? (
            <label className="flex items-start gap-2 text-xs text-muted">
              <input type="checkbox" name="undue_cost_or_effort" className="mt-0.5" />
              <span>
                원인 항목을 추적하는 데 과도한 원가 또는 노력이 듭니다 (이 경우 기준서에 따라
                영업으로 분류됩니다)
              </span>
            </label>
          ) : null}
          <input
            name="note"
            placeholder="근거 메모 (선택)"
            className="w-full rounded-md border border-border bg-canvas px-3 py-2 text-sm text-ink"
          />
          <div className="flex flex-wrap gap-2">
            <SubmitButton name="answer" value="YES" pendingLabel="반영 중…">
              이 범주로 확정
            </SubmitButton>
            <SubmitButton name="answer" value="NOT_SURE" variant="secondary" pendingLabel="기록 중…">
              확신할 수 없음
            </SubmitButton>
          </div>
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <input
            name="note"
            placeholder="근거 메모 (예: 2025 사업보고서 II-1)"
            className="w-full rounded-md border border-border bg-canvas px-3 py-2 text-sm text-ink"
          />
          <div className="flex flex-wrap gap-2">
            <SubmitButton name="answer" value="YES" pendingLabel="반영 중…">
              예
            </SubmitButton>
            <SubmitButton name="answer" value="NO" variant="secondary" pendingLabel="반영 중…">
              아니오
            </SubmitButton>
            <SubmitButton
              name="answer"
              value="NOT_SURE"
              variant="secondary"
              pendingLabel="기록 중…"
            >
              확신할 수 없음
            </SubmitButton>
          </div>
        </div>
      )}

      <p className="mt-2 text-[11px] text-muted">
        &quot;확신할 수 없음&quot;도 기록되지만, 규칙을 해제하지는 않습니다.
      </p>
      <FormError message={state.error} />
      {state.notice ? (
        <p className="mt-2 text-xs text-positive" role="status">
          {state.notice}
        </p>
      ) : null}
    </form>
  );
}

export function ActivityForm({
  projectId,
  activityType,
  label,
  activity,
}: {
  projectId: string;
  activityType: string;
  label: string;
  activity: BusinessActivity | null;
}) {
  const [state, action] = useActionState<ReviewState, FormData>(setActivity, {});
  const value =
    activity === null || activity.is_main_business_activity === null
      ? "unknown"
      : activity.is_main_business_activity
        ? "yes"
        : "no";

  return (
    <form action={action} className="rounded-md border border-border bg-surface p-4">
      <input type="hidden" name="project_id" value={projectId} />
      <input type="hidden" name="activity_type" value={activityType} />

      <div className="flex items-baseline justify-between gap-2">
        <p className="text-sm font-medium">{label}</p>
        <StatusPill tone={value === "unknown" ? "warn" : value === "yes" ? "active" : "neutral"}>
          {value === "unknown" ? "모름" : value === "yes" ? "주된 영업활동" : "해당 없음"}
        </StatusPill>
      </div>

      {activity && !activity.confirmed_by_user ? (
        <p className="mt-2 text-xs text-caution">
          AI가 제안한 항목입니다. 사람이 확인하기 전까지 규칙에는 반영되지 않습니다.
        </p>
      ) : null}

      <label className="mt-3 block text-xs font-medium text-muted">
        확인
        <select
          name="value"
          defaultValue={value}
          className="mt-1 w-full rounded-md border border-border bg-canvas px-3 py-2 text-sm text-ink"
        >
          <option value="yes">예, 주된 영업활동입니다</option>
          <option value="no">아니오</option>
          <option value="unknown">확인할 수 없음 (규칙 대기)</option>
        </select>
      </label>
      <input
        name="description"
        defaultValue={activity?.description ?? ""}
        placeholder="근거 (선택)"
        className="mt-2 w-full rounded-md border border-border bg-canvas px-3 py-2 text-sm text-ink"
      />

      <div className="mt-3">
        <SubmitButton variant="secondary" pendingLabel="반영 중…">
          저장하고 재분류
        </SubmitButton>
      </div>

      <FormError message={state.error} />
      {state.notice ? (
        <p className="mt-2 text-xs text-positive" role="status">
          {state.notice}
        </p>
      ) : null}
    </form>
  );
}
