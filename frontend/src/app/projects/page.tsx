import Link from "next/link";
import { api, type ProjectSummary } from "@/lib/api";
import { requireSession } from "@/lib/guard";
import { ProjectStatusPill } from "@/components/StatusPill";
import { scaleLabel } from "@/lib/format";
import { NewProjectForm } from "./NewProjectForm";

/**
 * The project list (spec §21).
 *
 * One project is one entity, one reporting period, one restructuring run.
 * Nothing here is shared between users: ownership is applied in the API's
 * repository layer, so this list is simply what the caller owns.
 */
export default async function ProjectsPage() {
  await requireSession();
  const page = await api.projects();

  return (
    <main className="mx-auto max-w-4xl px-4 py-12 sm:px-6">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">분석 프로젝트</h1>
          <p className="mt-1 text-sm text-muted">
            {page.total ?? page.items.length}개
          </p>
        </div>
      </div>

      {page.items.length === 0 ? (
        <p className="mt-8 rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
          아직 프로젝트가 없습니다. 아래에서 첫 번째 분석을 시작하세요.
        </p>
      ) : (
        <ul className="mt-8 divide-y divide-border border-y border-border">
          {page.items.map((project) => (
            <ProjectRow key={project.id} project={project} />
          ))}
        </ul>
      )}

      <NewProjectForm />
    </main>
  );
}

function ProjectRow({ project }: { project: ProjectSummary }) {
  return (
    <li>
      <Link
        href={`/projects/${project.id}`}
        className="flex flex-wrap items-center justify-between gap-3 px-1 py-4 hover:bg-surface"
      >
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{project.name}</p>
          <p className="mt-1 text-xs text-muted">
            {project.company_name} · {project.fiscal_year}기 · {project.basis === "CONSOLIDATED" ? "연결" : "별도"} ·{" "}
            {scaleLabel(project.presentation_scale)}
          </p>
        </div>
        <ProjectStatusPill status={project.status} />
      </Link>
    </li>
  );
}
