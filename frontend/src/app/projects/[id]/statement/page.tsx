import { ApiError, api } from "@/lib/api";
import { Disclaimer } from "@/components/Disclaimer";
import { Figure } from "@/components/Figure";
import { ReconciliationBanner, UnreconciledFrame } from "@/components/ReconciliationBanner";
import { scaleLabel } from "@/lib/format";
import { ExportButton } from "./ExportButton";

/**
 * The IFRS 18 statement of profit or loss (spec §6, §19, §24).
 *
 * The reconciliation banner is above the figures, and when the gate failed the
 * figures themselves are framed as unreliable. Spec §19 is not a warning
 * somewhere on the page: a statement that does not reconcile must not be
 * capable of being read, or screenshotted, as a normal one.
 */
export default async function StatementPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let statement;
  try {
    statement = await api.statement(id);
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
      <ReconciliationBanner reconciliation={statement.reconciliation} />

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted">
          단위: {statement.currency} {scaleLabel(statement.scale)}
        </p>
        <ExportButton projectId={id} reconciled={statement.reconciliation.passed} />
      </div>

      <UnreconciledFrame reconciliation={statement.reconciliation}>
        <table className="mt-4 w-full text-sm" data-testid="statement">
          <tbody>
            {statement.sections.map((section) => (
              <SectionRows key={section.category} section={section} />
            ))}
          </tbody>
        </table>

        <section className="mt-8 border-t border-border pt-4">
          <h2 className="text-sm font-medium">IFRS 18 소계</h2>
          <table className="mt-3 w-full text-sm">
            <tbody className="divide-y divide-border">
              {statement.subtotals.map((subtotal) => (
                <tr key={subtotal.key}>
                  <td className="py-2">
                    {subtotal.label_ko}
                    <span className="ml-2 text-xs text-muted">{subtotal.label_en}</span>
                    {!subtotal.presented ? (
                      <p className="mt-1 text-xs text-caution">
                        표시 불가 — {subtotal.suppressed_reason}
                      </p>
                    ) : null}
                  </td>
                  <td className="py-2 text-right font-medium">
                    <Figure value={subtotal.amount} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </UnreconciledFrame>

      <Disclaimer text={statement.disclaimer} limitations={statement.limitations} />
    </main>
  );
}

function SectionRows({
  section,
}: {
  section: {
    category: string;
    label_ko: string;
    label_en: string;
    total: string;
    lines: {
      line_id: string | null;
      label_ko: string;
      amount: string;
      rule_id: string | null;
      user_override: boolean;
      normalized_account_code: string | null;
    }[];
  };
}) {
  if (section.lines.length === 0) return null;
  return (
    <>
      <tr className="border-y border-border bg-surface">
        <th colSpan={2} className="py-2 text-left text-xs font-medium uppercase tracking-wider">
          {section.label_ko}
          <span className="ml-2 font-normal text-muted">{section.label_en}</span>
        </th>
      </tr>
      {section.lines.map((line) => (
        <tr key={line.line_id ?? line.label_ko} className="border-b border-border/60">
          <td className="py-2 pl-3">
            {line.label_ko}
            {line.user_override ? (
              <span className="ml-2 text-[11px] text-accent">수동</span>
            ) : null}
            {line.rule_id ? (
              <span className="ml-2 text-[11px] text-muted">{line.rule_id}</span>
            ) : null}
          </td>
          <td className="py-2 text-right">
            <Figure value={line.amount} />
          </td>
        </tr>
      ))}
      <tr className="border-b border-border">
        <td className="py-2 pl-3 text-xs font-medium text-muted">{section.label_ko} 합계</td>
        <td className="py-2 text-right font-medium">
          <Figure value={section.total} />
        </td>
      </tr>
    </>
  );
}
