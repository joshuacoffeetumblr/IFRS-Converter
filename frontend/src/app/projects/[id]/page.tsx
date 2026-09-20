import { ApiError, api, type ExtractionReport, type Line } from "@/lib/api";
import { Figure } from "@/components/Figure";
import { StatusPill } from "@/components/StatusPill";
import { ExtractionControls } from "./ExtractionControls";

/**
 * Upload and extraction (spec §17, §18).
 *
 * The extracted lines are shown with the cell each figure came from, and with
 * the reconciliation that verified them. Extraction that cannot reproduce the
 * statement's own subtotals is reported as failed here rather than being
 * passed downstream — classification of misread figures is worse than no
 * classification at all.
 */
export default async function ExtractionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const project = await api.project(id);

  let lines: Line[] = [];
  let report: ExtractionReport | null = null;
  try {
    const response = await api.lines(id);
    lines = response.items;
    report = response.report;
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
  }

  return (
    <main className="py-8">
      <ExtractionControls
        projectId={id}
        hasLines={lines.length > 0}
        status={project.status}
        canFinalize={project.progress?.can_finalize ?? false}
      />

      {report ? <ExtractionReportPanel report={report} /> : null}

      {lines.length > 0 ? (
        <LineTable lines={lines} />
      ) : (
        <p className="mt-8 rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
          아직 추출된 라인이 없습니다. 손익계산서 파일(XLSX 또는 CSV)을 업로드하고 추출을
          실행하세요.
        </p>
      )}
    </main>
  );
}

function ExtractionReportPanel({ report }: { report: ExtractionReport }) {
  return (
    <section className="mt-8" data-testid="extraction-report">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-medium">추출 정합성</h2>
        <StatusPill tone={report.passed ? "good" : "bad"}>
          {report.passed ? "원본 소계와 일치" : "원본 소계와 불일치"}
        </StatusPill>
        {report.signs_inferred ? (
          <StatusPill tone="warn">부호 추론됨</StatusPill>
        ) : null}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted">
        추출한 상세 라인을 합산해 문서가 직접 인쇄한 소계와 대조합니다. IFRS 18 작업은 이
        검증을 통과한 뒤에 시작됩니다.
        {report.signs_inferred
          ? " 이 문서는 모든 금액을 부호 없이 인쇄했고, 소계가 맞아떨어지는 하나의 해석만 채택했습니다."
          : ""}
      </p>

      {report.blockers.length > 0 ? (
        <ul className="mt-3 space-y-1">
          {report.blockers.map((blocker) => (
            <li key={blocker} className="text-xs text-caution">
              — {blocker}
            </li>
          ))}
        </ul>
      ) : null}

      {report.checks.length > 0 ? (
        <table className="mt-4 w-full text-xs">
          <thead className="text-muted">
            <tr className="border-y border-border text-left">
              <th className="py-2 font-medium">검증</th>
              <th className="py-2 text-right font-medium">문서 표시</th>
              <th className="py-2 text-right font-medium">합산 결과</th>
              <th className="py-2 text-right font-medium">차이</th>
              <th className="py-2 text-right font-medium">결과</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {report.checks.map((check) => (
              <tr key={check.check}>
                <td className="py-2">{check.check}</td>
                <td className="py-2 text-right">
                  <Figure value={check.reported} />
                </td>
                <td className="py-2 text-right">
                  <Figure value={check.computed} />
                </td>
                <td className="py-2 text-right">
                  <Figure value={check.delta} signed />
                </td>
                <td className="py-2 text-right">
                  <span className={check.passed ? "text-positive" : "text-negative"}>
                    {check.passed ? "통과" : "실패"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

function LineTable({ lines }: { lines: Line[] }) {
  return (
    <section className="mt-10">
      <h2 className="text-sm font-medium">추출된 라인</h2>
      <p className="mt-1 text-xs text-muted">
        모든 금액은 손익에 미치는 부호 있는 효과입니다. 수익은 양수, 비용은 음수로
        저장되므로 모든 소계가 단순 합계가 됩니다.
      </p>

      <table className="mt-4 w-full text-sm">
        <thead className="text-xs text-muted">
          <tr className="border-y border-border text-left">
            <th className="py-2 font-medium">과목</th>
            <th className="py-2 font-medium">정규 계정</th>
            <th className="py-2 text-right font-medium">금액</th>
            <th className="py-2 text-right font-medium">원본</th>
            <th className="py-2 text-right font-medium">출처 셀</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {lines.map((line) => (
            <tr key={line.id} className={line.is_subtotal ? "bg-surface font-medium" : undefined}>
              <td className="py-2" style={{ paddingLeft: `${line.depth * 12}px` }}>
                {line.raw_label}
                {line.is_subtotal ? (
                  <span className="ml-2 text-[11px] text-muted">소계</span>
                ) : null}
              </td>
              <td className="py-2 text-xs text-muted">{line.normalized_account_code ?? "—"}</td>
              <td className="py-2 text-right">
                <Figure value={line.amount} />
              </td>
              <td className="py-2 text-right text-xs text-muted">{line.raw_value}</td>
              <td className="py-2 text-right text-xs text-muted">
                {/* Spec §18: every figure traces back to the cell it came from. */}
                {line.source_locator.sheet ? `${line.source_locator.sheet}!` : ""}
                {line.source_locator.cell ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
