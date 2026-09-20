import { ApiError, api } from "@/lib/api";
import type {
  BusinessActivity,
  Classification,
  ClassificationSummary,
  Question,
} from "@/lib/api";
import { Figure } from "@/components/Figure";
import { StatusPill } from "@/components/StatusPill";
import { ActivityForm, QuestionForm, ReviewForm } from "./ReviewForms";

/**
 * The review screen (spec §1 layer 3, §5, §7, §10).
 *
 * Three things a person, and only a person, can settle:
 *
 *   1. facts a rule needs and cannot read from the statement;
 *   2. whether an activity is a main business activity of the entity;
 *   3. every classification the engine could not settle on its own.
 *
 * The queue is ordered by the size of the effect on operating profit, because
 * that is the order in which a reviewer's attention is worth most.
 */
export default async function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let classifications: Classification[] = [];
  let summary: ClassificationSummary | null = null;
  let questions: Question[] = [];
  let activities: BusinessActivity[] = [];

  try {
    const [rows, questionList, activityList] = await Promise.all([
      api.classifications(id),
      api.questions(id),
      api.activities(id),
    ]);
    classifications = rows.items;
    summary = rows.summary;
    questions = questionList.items;
    activities = activityList.items;
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
  }

  if (classifications.length === 0) {
    return (
      <main className="py-8">
        <p className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
          아직 분류가 실행되지 않았습니다. 추출 탭에서 분류를 실행하세요.
        </p>
      </main>
    );
  }

  const open = questions.filter((question) => question.blocks_finalization && !question.is_resolved);
  const queue = classifications.filter(
    (row) => row.requires_human_review && row.reviewed_at === null,
  );
  const settled = classifications.filter(
    (row) => !row.requires_human_review || row.reviewed_at !== null,
  );

  return (
    <main className="space-y-10 py-8">
      {summary ? <SummaryBar summary={summary} /> : null}

      {open.length > 0 ? (
        <section data-testid="questions">
          <h2 className="text-sm font-medium">규칙이 기다리는 사실</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            아래 질문은 모델이 아니라 규칙이 제기한 것입니다. 각 질문에는 답변에 따라
            달라지는 금액이 함께 표시됩니다.
          </p>
          <div className="mt-4 space-y-4">
            {open.map((question) => (
              <QuestionForm key={question.id} projectId={id} question={question} />
            ))}
          </div>
        </section>
      ) : null}

      <ActivitySection projectId={id} activities={activities} />

      <section data-testid="review-queue">
        <h2 className="text-sm font-medium">검토 대기 ({queue.length})</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          영업이익에 미치는 영향이 큰 순서입니다. 수동 분류에는 사유가 필요합니다.
        </p>
        {queue.length === 0 ? (
          <p className="mt-4 rounded-md border border-dashed border-border px-4 py-6 text-center text-sm text-muted">
            검토가 필요한 항목이 없습니다.
          </p>
        ) : (
          <div className="mt-4 space-y-3">
            {queue.map((row) => (
              <ReviewForm key={row.id} projectId={id} classification={row} />
            ))}
          </div>
        )}
      </section>

      <SettledTable rows={settled} />
    </main>
  );
}

function SummaryBar({ summary }: { summary: ClassificationSummary }) {
  const byMethod = Object.entries(summary.by_method).sort(([a], [b]) => a.localeCompare(b));
  return (
    <section className="rounded-md border border-border bg-surface p-4">
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
        <span>
          전체 <strong className="font-medium">{summary.total}</strong>
        </span>
        <span className={summary.unreviewed > 0 ? "text-caution" : undefined}>
          미검토 <strong className="font-medium">{summary.unreviewed}</strong>
        </span>
        <span className={summary.open_questions > 0 ? "text-caution" : undefined}>
          미답변 질문 <strong className="font-medium">{summary.open_questions}</strong>
        </span>
        {byMethod.map(([method, count]) => (
          <span key={method} className="text-muted">
            {METHOD_LABELS[method] ?? method} {count}
          </span>
        ))}
      </div>
    </section>
  );
}

const METHOD_LABELS: Record<string, string> = {
  RULE: "규칙",
  RESIDUAL_DEFAULT: "잔여(영업)",
  AI: "AI 제안",
  USER: "수동",
  UNRESOLVED: "미해결",
};

const ACTIVITY_LABELS: Record<string, string> = {
  INVESTING_IN_ASSETS: "자산에 대한 투자",
  PROVIDING_FINANCING_TO_CUSTOMERS: "고객에 대한 금융 제공",
};

function ActivitySection({
  projectId,
  activities,
}: {
  projectId: string;
  activities: BusinessActivity[];
}) {
  const declared = new Map(activities.map((item) => [item.activity_type, item]));

  return (
    <section data-testid="activities">
      <h2 className="text-sm font-medium">주된 영업활동</h2>
      <p className="mt-1 text-xs leading-relaxed text-muted">
        IFRS 18이 지정한 두 가지 활동입니다. 산업분류코드로 추정하지 않으며, 사람이 확인한
        경우에만 규칙에 반영됩니다. 확인을 철회하면 다시 &quot;모름&quot;이 되어 관련 규칙이
        멈춥니다 — &quot;아니오&quot;와는 다릅니다.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {Object.keys(ACTIVITY_LABELS).map((type) => (
          <ActivityForm
            key={type}
            projectId={projectId}
            activityType={type}
            label={ACTIVITY_LABELS[type] ?? type}
            activity={declared.get(type) ?? null}
          />
        ))}
      </div>
    </section>
  );
}

function SettledTable({ rows }: { rows: Classification[] }) {
  if (rows.length === 0) return null;
  return (
    <section>
      <h2 className="text-sm font-medium">확인된 분류 ({rows.length})</h2>
      <table className="mt-4 w-full text-sm">
        <thead className="text-xs text-muted">
          <tr className="border-y border-border text-left">
            <th className="py-2 font-medium">과목</th>
            <th className="py-2 font-medium">범주</th>
            <th className="py-2 font-medium">근거</th>
            <th className="py-2 text-right font-medium">금액</th>
            <th className="py-2 text-right font-medium">영업이익 영향</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row) => (
            <tr key={row.id}>
              <td className="py-2">{row.original_account}</td>
              <td className="py-2">
                <StatusPill tone={row.user_override ? "active" : "neutral"}>
                  {CATEGORY_LABELS[row.final_ifrs18_category ?? ""] ?? row.final_ifrs18_category}
                </StatusPill>
              </td>
              <td className="py-2 text-xs text-muted">
                {row.user_override
                  ? `수동 · ${row.override_reason ?? ""}`
                  : (row.rule_source_reference ?? METHOD_LABELS[row.classification_method] ?? "—")}
              </td>
              <td className="py-2 text-right">
                <Figure value={row.amount} />
              </td>
              <td className="py-2 text-right">
                <Figure value={row.impact_on_operating_profit} signed />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export const CATEGORY_LABELS: Record<string, string> = {
  OPERATING: "영업",
  INVESTING: "투자",
  FINANCING: "재무",
  INCOME_TAX: "법인세",
  DISCONTINUED_OPERATION: "중단영업",
  UNCLASSIFIED: "미분류",
};
