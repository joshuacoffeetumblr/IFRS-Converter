"use server";

import { revalidatePath } from "next/cache";
import { ApiError, api } from "@/lib/api";

export interface ReviewState {
  error?: string;
  notice?: string;
}

function message(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message;
  return fallback;
}

function refresh(projectId: string): void {
  revalidatePath(`/projects/${projectId}`, "layout");
}

/**
 * A human decision about one classification (spec §1 layer 3).
 *
 * An override needs a reason and the API refuses it without one; that refusal
 * is shown to the user rather than pre-empted here, so there is one definition
 * of the rule and it is the server's.
 */
export async function reviewClassification(
  _: ReviewState,
  formData: FormData,
): Promise<ReviewState> {
  const projectId = String(formData.get("project_id") ?? "");
  const classificationId = String(formData.get("classification_id") ?? "");
  const action = String(formData.get("action") ?? "ACCEPTED");

  const payload: Record<string, unknown> = { action };
  const category = String(formData.get("final_ifrs18_category") ?? "").trim();
  const reason = String(formData.get("override_reason") ?? "").trim();
  if (action === "OVERRIDDEN") {
    if (category) payload.final_ifrs18_category = category;
    if (reason) payload.override_reason = reason;
  } else if (reason) {
    payload.override_reason = reason;
  }

  try {
    await api.review(projectId, classificationId, payload);
    refresh(projectId);
    return {
      notice:
        action === "OVERRIDDEN"
          ? "수동 분류를 기록했습니다."
          : action === "DEFERRED"
            ? "보류했습니다. 검토 대기 상태로 남습니다."
            : "제안을 그대로 승인했습니다.",
    };
  } catch (error) {
    return { error: message(error, "검토 결과를 기록하지 못했습니다.") };
  }
}

/**
 * Answer a question a rule raised (spec §5).
 *
 * `NOT_SURE` is a real answer and is stored, but it does not unblock anything —
 * the API decides that, and the screen simply reports what came back.
 */
export async function answerQuestion(_: ReviewState, formData: FormData): Promise<ReviewState> {
  const projectId = String(formData.get("project_id") ?? "");
  const questionId = String(formData.get("question_id") ?? "");
  const answer = String(formData.get("answer") ?? "");
  const category = String(formData.get("resolved_category") ?? "").trim();
  const undueCost = formData.get("undue_cost_or_effort") === "on";
  const note = String(formData.get("note") ?? "").trim();

  const payload: Record<string, unknown> = { answer };
  if (undueCost) payload.undue_cost_or_effort = true;
  else if (category) payload.resolved_category = category;
  if (note) payload.note = note;

  try {
    const result = await api.answer(projectId, questionId, payload);
    refresh(projectId);
    return {
      notice: result.question.is_resolved
        ? "답변을 기록하고 분류를 다시 실행했습니다."
        : "답변을 기록했습니다. 확신할 수 없다는 답변은 규칙을 해제하지 않습니다.",
    };
  } catch (error) {
    return { error: message(error, "답변을 기록하지 못했습니다.") };
  }
}

/**
 * Confirm, deny or withdraw a main business activity (spec §10).
 *
 * "확인할 수 없음" sets the fact back to null, which blocks the rules that
 * depend on it. That is not the same as "아니오", and the two are kept apart
 * here exactly as they are in the database.
 */
export async function setActivity(_: ReviewState, formData: FormData): Promise<ReviewState> {
  const projectId = String(formData.get("project_id") ?? "");
  const activityType = String(formData.get("activity_type") ?? "");
  const value = String(formData.get("value") ?? "");
  const description = String(formData.get("description") ?? "").trim();

  const payload: Record<string, unknown> = {
    is_main_business_activity: value === "unknown" ? null : value === "yes",
  };
  if (description) payload.description = description;

  try {
    await api.setActivity(projectId, activityType, payload);
    refresh(projectId);
    return { notice: "확인 내용을 반영하고 분류를 다시 실행했습니다." };
  } catch (error) {
    return { error: message(error, "확인 내용을 저장하지 못했습니다.") };
  }
}
