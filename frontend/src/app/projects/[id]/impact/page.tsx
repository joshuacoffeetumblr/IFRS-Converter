import { ApiError, api, type Kpi, type Reclassification } from "@/lib/api";
import { Disclaimer } from "@/components/Disclaimer";
import { Figure } from "@/components/Figure";
import { ReconciliationBanner, UnreconciledFrame } from "@/components/ReconciliationBanner";
import { formatDateTime, formatPercent, scaleLabel } from "@/lib/format";
import { Waterfall } from "./Waterfall";

/**
 * The impact screen (spec §22, §23).
 *
 * Every figure here is computed server-side, including the percentages: two
 * implementations of "how much did operating profit move" would eventually
 * disagree, and the one in the browser would be the one nobody could audit
 * (architecture §13).
 */
export default async function ImpactPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let impact;
  try {
    impact = await api.impact(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 422) {
      return (
        <main className="py-8">
          <p className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
            {error.message}
          </p>
        </main>
      );
    }
    throw error;
  }

  return (
    <main className="py-8">
      <ReconciliationBanner reconciliation={impact.reconciliation} />

      <UnreconciledFrame reconciliation={impact.reconciliation}>
        {impact.headline ? <Headline kpi={impact.headline} comparable={impact.comparable} /> : null}

        {!impact.comparable ? (
          <p className="mt-4 rounded-md border border-caution/40 bg-caution/5 px-4 py-3 text-xs leading-relaxed text-caution">
            원본 문서에 영업이익 소계가 인쇄되어 있지 않아 비교 기준이 없습니다. 변동이 없다고
            표시하는 것은 사실과 다르므로, 변화가 아니라 IFRS 18 기준 값만 제시합니다.
          </p>
        ) : null}

        <section className="mt-8">
          <h2 className="text-sm font-medium">주요 지표</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            변동이 0이어야 하는 지표도 그대로 표시합니다. 그것이 IFRS 18이 표시만 바꾸고
            이익은 바꾸지 않았다는 증거이기 때문입니다.
          </p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {impact.kpis.map((kpi) => (
              <KpiCard key={kpi.key} kpi={kpi} />
            ))}
          </div>
        </section>

        <section className="mt-10">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-medium">영업이익 변동 경로</h2>
            <p className="text-xs text-muted">
              단위: {impact.currency} {scaleLabel(impact.scale)}
              {impact.waterfall_balances ? " · 합계 검증됨" : " · 합계 불일치"}
            </p>
          </div>
          <Waterfall steps={impact.waterfall} />
        </section>

        <TopReclassifications
          projectId={id}
          items={impact.top_reclassifications}
        />
      </UnreconciledFrame>

      {impact.computed_at ? (
        <p className="mt-8 text-xs text-muted">
          스냅샷 기준 시각 {formatDateTime(impact.computed_at)}
        </p>
      ) : null}

      <Disclaimer text={impact.disclaimer} limitations={impact.limitations} />
    </main>
  );
}

const KPI_LABELS: Record<string, string> = {
  REVENUE: "수익",
  OPERATING_PROFIT: "영업이익",
  OPERATING_PROFIT_MARGIN: "영업이익률",
  PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES: "재무·법인세차감전이익",
  PROFIT_BEFORE_TAX: "법인세차감전순이익",
  PROFIT_FOR_THE_PERIOD: "당기순이익",
  INVESTING_RESULT: "투자 손익",
  FINANCING_RESULT: "재무 손익",
};

function Headline({ kpi, comparable }: { kpi: Kpi; comparable: boolean }) {
  return (
    <section className="mt-6 rounded-md border border-border bg-surface p-5" data-testid="headline">
      <p className="text-xs uppercase tracking-[0.14em] text-muted">
        {KPI_LABELS[kpi.key] ?? kpi.key}
      </p>
      <div className="mt-3 flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <div>
          <p className="text-[11px] text-muted">보고 기준</p>
          <Figure value={kpi.before} className="text-figure-md" />
        </div>
        <div>
          <p className="text-[11px] text-muted">IFRS 18</p>
          <Figure value={kpi.after} className="text-figure-lg" />
        </div>
        {comparable ? (
          <div>
            <p className="text-[11px] text-muted">변동</p>
            <p>
              <Figure value={kpi.change} signed className="text-figure-md" />
              {kpi.change_pct ? (
                <span className="ml-2 text-sm text-muted">{formatPercent(kpi.change_pct)}</span>
              ) : null}
            </p>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function KpiCard({ kpi }: { kpi: Kpi }) {
  const isPercent = kpi.unit === "PERCENT";
  return (
    <div className="rounded-md border border-border bg-surface px-4 py-3">
      <p className="text-[11px] text-muted">{KPI_LABELS[kpi.key] ?? kpi.key}</p>
      <p className="mt-1">
        {isPercent ? (
          <span className="figure tabular-nums">{formatPercent(kpi.after)}</span>
        ) : (
          <Figure value={kpi.after} />
        )}
      </p>
      <p className="mt-1 text-xs text-muted">
        {kpi.before === null ? (
          // IFRS 18 introduced this measure, so there is no "before" at all.
          <span>IFRS 18에서 새로 도입된 구분</span>
        ) : (
          <>
            보고 기준{" "}
            {isPercent ? formatPercent(kpi.before) : <Figure value={kpi.before} />}
            {" · "}
            {isPercent ? (
              <span className="figure">{formatPercent(kpi.change)}</span>
            ) : (
              <Figure value={kpi.change} signed />
            )}
          </>
        )}
      </p>
    </div>
  );
}

function TopReclassifications({
  projectId,
  items,
}: {
  projectId: string;
  items: Reclassification[];
}) {
  if (items.length === 0) return null;
  return (
    <section className="mt-10">
      <h2 className="text-sm font-medium">영업이익을 움직인 항목</h2>
      <p className="mt-1 text-xs text-muted">
        각 행은 해당 분류 결정으로 이동합니다 — 왜 그렇게 분류되었는지, 어느 셀에서 왔는지
        확인할 수 있습니다.
      </p>
      <table className="mt-4 w-full text-sm">
        <thead className="text-xs text-muted">
          <tr className="border-y border-border text-left">
            <th className="py-2 font-medium">과목</th>
            <th className="py-2 font-medium">보고서 위치</th>
            <th className="py-2 font-medium">IFRS 18 범주</th>
            <th className="py-2 text-right font-medium">금액</th>
            <th className="py-2 text-right font-medium">영업이익 영향</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {items.map((item) => (
            <tr key={item.classification_id ?? item.original_account}>
              <td className="py-2">
                {item.classification_id ? (
                  <a
                    className="text-accent hover:underline"
                    href={`/projects/${projectId}/review#classification-${item.classification_id}`}
                  >
                    {item.original_account}
                  </a>
                ) : (
                  item.original_account
                )}
                {item.rationale_summary ? (
                  <p className="mt-0.5 text-xs text-muted">{item.rationale_summary}</p>
                ) : null}
              </td>
              <td className="py-2 text-xs text-muted">
                {item.reported_placement === "INSIDE_OPERATING"
                  ? "영업이익 내"
                  : item.reported_placement === "OUTSIDE_OPERATING"
                    ? "영업이익 밖"
                    : "알 수 없음"}
              </td>
              <td className="py-2 text-xs">{item.ifrs18_category}</td>
              <td className="py-2 text-right">
                <Figure value={item.amount} />
              </td>
              <td className="py-2 text-right">
                <Figure value={item.impact_on_operating_profit} signed />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
