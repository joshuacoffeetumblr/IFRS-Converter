import { ApiError, api, type AuditEntry } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

/**
 * The audit trail (spec §8).
 *
 * Read as a narrative, oldest first: the upload, the extraction, each
 * classification run, every question a rule raised, every answer and every
 * override — each with who did it and what changed.
 */
export default async function AuditPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let entries: AuditEntry[] = [];
  let total = 0;
  try {
    const log = await api.auditLogs(id);
    entries = log.items;
    total = log.total;
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
  }

  return (
    <main className="py-8">
      <h1 className="text-sm font-medium">감사 추적 ({total})</h1>
      <p className="mt-1 text-xs leading-relaxed text-muted">
        이 프로젝트를 바꾼 모든 사건입니다. 기록은 추가만 가능하며 수정하거나 삭제할 수
        없습니다.
      </p>

      {entries.length === 0 ? (
        <p className="mt-6 rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
          아직 기록이 없습니다.
        </p>
      ) : (
        <ol className="mt-6 space-y-3">
          {entries.map((entry) => (
            <li key={entry.id} className="rounded-md border border-border bg-surface px-4 py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-sm font-medium">
                  {ACTION_LABELS[entry.action] ?? entry.action}
                  <span className="ml-2 text-xs font-normal text-muted">{entry.entity_type}</span>
                </p>
                <p className="text-xs text-muted">
                  {formatDateTime(entry.occurred_at)} ·{" "}
                  {entry.actor_type === "SYSTEM" ? "시스템" : "사용자"}
                </p>
              </div>
              {entry.before || entry.after ? (
                <dl className="mt-2 grid gap-2 sm:grid-cols-2">
                  {entry.before ? <Payload label="이전" value={entry.before} /> : null}
                  {entry.after ? <Payload label="이후" value={entry.after} /> : null}
                </dl>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </main>
  );
}

function Payload({ label, value }: { label: string; value: Record<string, unknown> }) {
  return (
    <div>
      <dt className="text-[11px] text-muted">{label}</dt>
      <dd className="mt-0.5 break-words font-mono text-[11px] leading-relaxed text-muted">
        {Object.entries(value)
          .map(([key, item]) => `${key}: ${item === null ? "—" : String(item)}`)
          .join("  ·  ")}
      </dd>
    </div>
  );
}

const ACTION_LABELS: Record<string, string> = {
  CREATED: "생성",
  UPDATED: "수정",
  DELETED: "삭제",
  EXTRACTED: "추출",
  DECOMPOSED: "분해",
  CLASSIFIED: "분류",
  ACCEPTED: "승인",
  OVERRIDDEN: "수동 분류",
  QUESTION_ANSWERED: "질문 답변",
  FINALIZED: "확정",
  EXPORTED: "내보내기",
};
