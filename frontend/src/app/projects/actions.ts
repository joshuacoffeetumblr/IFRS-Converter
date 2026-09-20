"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/api";

export interface CreateState {
  error?: string;
}

/**
 * Create a project.
 *
 * The presentation scale is asked for rather than guessed: a Korean statement
 * headed "(단위: 백만원)" is stated in millions, and reading it as 원 would be
 * wrong by six orders of magnitude in every figure.
 */
export async function createProject(_: CreateState, formData: FormData): Promise<CreateState> {
  const name = String(formData.get("name") ?? "").trim();
  const companyName = String(formData.get("company_name") ?? "").trim();
  const identifier = String(formData.get("identifier") ?? "").trim();
  const fiscalYear = Number(formData.get("fiscal_year"));

  if (!name || !companyName || !fiscalYear) {
    return { error: "프로젝트명, 회사명, 회계연도는 필수입니다." };
  }

  let created;
  try {
    created = await api.createProject({
      name,
      company: {
        name: companyName,
        jurisdiction: "KR",
        // A registered number identifies the entity across periods; a name
        // does not, so it is only sent when the user actually supplied one.
        ...(identifier ? { identifier, identifier_scheme: "KR_BRN" } : {}),
      },
      fiscal_year: fiscalYear,
      period_start: String(formData.get("period_start") ?? `${fiscalYear}-01-01`),
      period_end: String(formData.get("period_end") ?? `${fiscalYear}-12-31`),
      basis: String(formData.get("basis") ?? "CONSOLIDATED"),
      presentation_currency: "KRW",
      presentation_scale: Number(formData.get("presentation_scale") ?? 6),
    });
  } catch (error) {
    if (error instanceof ApiError) return { error: error.message };
    return { error: "프로젝트를 만들지 못했습니다." };
  }

  revalidatePath("/projects");
  redirect(`/projects/${created.id}`);
}
