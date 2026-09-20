import type { Route } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import { requireSession } from "@/lib/guard";
import { ProjectStatusPill } from "@/components/StatusPill";
import { scaleLabel } from "@/lib/format";

/**
 * The shell every project screen sits in.
 *
 * The status and the progress counters live here rather than on each page so
 * that what still stands between this project and a finalized statement is
 * visible from every step of the flow, not only from the one that produces it.
 */
export default async function ProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  await requireSession();
  const { id } = await params;

  let project;
  try {
    project = await api.project(id);
  } catch (error) {
    // The API answers 404 for another user's project as well as for one that
    // does not exist, so that ids cannot be probed. The UI keeps that.
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const progress = project.progress;

  return (
    <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
      <header className="border-b border-border pb-5">
        <Link href="/projects" className="text-xs text-muted hover:text-ink">
          ← 프로젝트 목록
        </Link>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{project.name}</h1>
            <p className="mt-1 text-xs text-muted">
              {project.company.name} · {project.fiscal_year}기 ·{" "}
              {project.period_start} ~ {project.period_end} ·{" "}
              {project.presentation_currency} {scaleLabel(project.presentation_scale)}
              {project.rule_set_version ? ` · 룰셋 ${project.rule_set_version}` : ""}
            </p>
          </div>
          <ProjectStatusPill status={project.status} />
        </div>

        {progress ? (
          <dl className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
            <Counter label="추출 라인" value={progress.lines_total} />
            <Counter label="분류됨" value={progress.lines_classified} />
            <Counter
              label="검토 대기"
              value={progress.requires_review - progress.reviewed}
              tone={progress.requires_review > progress.reviewed ? "warn" : "muted"}
            />
            <Counter
              label="미답변 질문"
              value={progress.open_questions}
              tone={progress.open_questions > 0 ? "warn" : "muted"}
            />
          </dl>
        ) : null}

        <nav className="mt-5 flex flex-wrap gap-4 text-sm">
          <Tab href={`/projects/${id}`}>추출</Tab>
          <Tab href={`/projects/${id}/review`}>검토</Tab>
          <Tab href={`/projects/${id}/statement`}>IFRS 18 손익계산서</Tab>
          <Tab href={`/projects/${id}/impact`}>영향 분석</Tab>
          <Tab href={`/projects/${id}/audit`}>감사 추적</Tab>
        </nav>
      </header>

      {children}
    </div>
  );
}

function Counter({
  label,
  value,
  tone = "muted",
}: {
  label: string;
  value: number;
  tone?: "muted" | "warn";
}) {
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2">
      <dt className="text-[11px] text-muted">{label}</dt>
      <dd className={tone === "warn" ? "mt-0.5 font-medium text-caution" : "mt-0.5 font-medium"}>
        {value}
      </dd>
    </div>
  );
}

// Generic so a template-literal path (`/projects/${id}/review`) still type
// checks against Next's typed routes, which only resolve for a concrete
// literal type rather than for a bare `string`.
function Tab<T extends string>({
  href,
  children,
}: {
  href: Route<T>;
  children: React.ReactNode;
}) {
  return (
    <Link href={href} className="text-muted hover:text-ink">
      {children}
    </Link>
  );
}
