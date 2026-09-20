"use server";

import { revalidatePath } from "next/cache";
import { ApiError, api } from "@/lib/api";

export interface ActionState {
  error?: string;
  notice?: string;
}

function message(error: unknown, fallback: string): string {
  // The API's problem documents are written for a person and name the rule
  // that was broken, so they are surfaced rather than replaced.
  if (error instanceof ApiError) return error.message;
  return fallback;
}

export async function uploadStatement(
  _: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const projectId = String(formData.get("project_id") ?? "");
  const file = formData.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return { error: "업로드할 파일을 선택해 주세요." };
  }

  const forward = new FormData();
  forward.append("file", file);

  try {
    const upload = await api.uploads(projectId, forward);
    revalidatePath(`/projects/${projectId}`);
    return { notice: `${upload.original_filename} 업로드됨 (SHA-256 ${upload.sha256.slice(0, 12)}…)` };
  } catch (error) {
    return { error: message(error, "업로드에 실패했습니다.") };
  }
}

export async function runExtraction(_: ActionState, formData: FormData): Promise<ActionState> {
  const projectId = String(formData.get("project_id") ?? "");
  try {
    const result = await api.extract(projectId);
    revalidatePath(`/projects/${projectId}`);
    return {
      notice: result.report.passed
        ? `${result.line_count}개 라인을 추출했고, 원본의 소계와 일치합니다.`
        : `${result.line_count}개 라인을 추출했지만 원본 소계와 일치하지 않습니다.`,
    };
  } catch (error) {
    return { error: message(error, "추출에 실패했습니다.") };
  }
}

export async function correctLine(_: ActionState, formData: FormData): Promise<ActionState> {
  const projectId = String(formData.get("project_id") ?? "");
  const lineId = String(formData.get("line_id") ?? "");
  const amount = String(formData.get("amount") ?? "").trim();
  const label = String(formData.get("raw_label") ?? "").trim();

  const payload: Record<string, unknown> = {};
  if (amount) payload.amount = amount;
  if (label) payload.raw_label = label;
  if (Object.keys(payload).length === 0) return { error: "변경할 내용이 없습니다." };

  try {
    await api.updateLine(projectId, lineId, payload);
    revalidatePath(`/projects/${projectId}`);
    return { notice: "수정했습니다. 이 라인에서 파생된 분류는 다시 검토해야 합니다." };
  } catch (error) {
    return { error: message(error, "수정하지 못했습니다.") };
  }
}

export async function runClassification(
  _: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const projectId = String(formData.get("project_id") ?? "");
  try {
    const result = await api.classify(projectId);
    revalidatePath(`/projects/${projectId}`);
    revalidatePath(`/projects/${projectId}/review`);
    const parts = [`${result.summary.total}개 라인을 분류했습니다.`];
    if (result.summary.requires_review > 0) {
      parts.push(`${result.summary.requires_review}건은 사람의 확인이 필요합니다.`);
    }
    if (result.summary.open_questions > 0) {
      parts.push(`${result.summary.open_questions}개 질문에 답해야 합니다.`);
    }
    if (result.preserved_overrides > 0) {
      parts.push(`기존 수동 결정 ${result.preserved_overrides}건은 그대로 두었습니다.`);
    }
    return { notice: parts.join(" ") };
  } catch (error) {
    return { error: message(error, "분류에 실패했습니다.") };
  }
}

export async function finalizeProject(_: ActionState, formData: FormData): Promise<ActionState> {
  const projectId = String(formData.get("project_id") ?? "");
  try {
    const result = await api.finalize(projectId);
    revalidatePath(`/projects/${projectId}`, "layout");
    return { notice: `확정되었습니다 (정합성 ${result.reconciliation.status}).` };
  } catch (error) {
    if (error instanceof ApiError && error.code === "reconciliation-failed") {
      // Spec §19: the figures are not presented as normal, and the reason is
      // on the statement screen with every failing check.
      return {
        error:
          "정합성 검증에 실패하여 확정하지 못했습니다. IFRS 18 손익계산서 화면에서 실패한 검증 항목을 확인하세요.",
      };
    }
    return { error: message(error, "확정하지 못했습니다.") };
  }
}

export async function reopenProject(_: ActionState, formData: FormData): Promise<ActionState> {
  const projectId = String(formData.get("project_id") ?? "");
  try {
    await api.reopen(projectId);
    revalidatePath(`/projects/${projectId}`, "layout");
    return { notice: "검토 상태로 되돌렸습니다. 이전 스냅샷은 그대로 보관됩니다." };
  } catch (error) {
    return { error: message(error, "되돌리지 못했습니다.") };
  }
}
