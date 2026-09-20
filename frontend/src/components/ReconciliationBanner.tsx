import { clsx } from "clsx";
import type { Reconciliation } from "@/lib/api";
import { Figure } from "@/components/Figure";

/**
 * The validation gate, on screen (spec §19).
 *
 * **A result that did not reconcile is never rendered as a normal one.** When
 * the gate fails this banner states it at the top of the page, names every
 * failing check with the size of the difference, and the figures below it are
 * framed as unreliable rather than shown plain. "Could not reconcile" on its
 * own is not actionable, so the numbers travel with the message.
 */
export function ReconciliationBanner({
  reconciliation,
  className,
}: {
  reconciliation: Reconciliation;
  className?: string;
}) {
  const failures = reconciliation.checks.filter((check) => !check.passed);
  const blocking = failures.filter((check) => check.severity === "BLOCKING");
  const warnings = failures.filter((check) => check.severity === "WARNING");

  if (reconciliation.passed && warnings.length === 0) {
    return (
      <div
        className={clsx(
          "rounded-md border border-positive/30 bg-surface px-4 py-3",
          className,
        )}
        data-testid="reconciliation-banner"
        data-status="PASSED"
      >
        <p className="text-sm font-medium text-positive">정합성 검증 통과</p>
        <p className="mt-1 text-xs text-muted">
          {reconciliation.checks.length}개 검증을 모두 통과했습니다. 재분류 전후의 손익
          합계는 동일합니다.
        </p>
      </div>
    );
  }

  const failed = !reconciliation.passed;
  return (
    <div
      className={clsx(
        "rounded-md border px-4 py-3",
        failed ? "border-negative/50 bg-negative/5" : "border-caution/40 bg-caution/5",
        className,
      )}
      data-testid="reconciliation-banner"
      data-status={reconciliation.status}
    >
      <p className={clsx("text-sm font-medium", failed ? "text-negative" : "text-caution")}>
        {failed
          ? "정합성 검증에 실패했습니다 — 아래 수치를 신뢰하지 마십시오"
          : "정합성 경고가 있습니다"}
      </p>
      <p className="mt-1 text-xs text-muted">
        {failed
          ? "IFRS 18 재구성이 원본 손익과 일치하지 않습니다. 확정 및 내보내기가 차단됩니다."
          : "출처 문서의 반올림으로 설명될 수 있는 차이입니다. 금액과 함께 확인하십시오."}
      </p>

      <ul className="mt-3 space-y-2">
        {[...blocking, ...warnings].map((check) => (
          <li key={check.check} className="border-t border-border pt-2 text-xs">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-medium">{check.check}</span>
              {check.delta ? (
                <span className="text-muted">
                  차이 <Figure value={check.delta} signed /> (허용 오차{" "}
                  <Figure value={check.tolerance} />)
                </span>
              ) : null}
            </div>
            <p className="mt-1 leading-relaxed text-muted">{check.detail}</p>
            {check.expected !== null && check.actual !== null ? (
              <p className="mt-1 text-muted">
                기대값 <Figure value={check.expected} /> · 실제값{" "}
                <Figure value={check.actual} />
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Frames figures produced by an analysis that did not reconcile.
 *
 * Used so a failing statement cannot be screenshotted and mistaken for a
 * normal one: the frame travels with the numbers, not just with the page.
 */
export function UnreconciledFrame({
  reconciliation,
  children,
}: {
  reconciliation: Reconciliation;
  children: React.ReactNode;
}) {
  if (reconciliation.passed) return <>{children}</>;
  return (
    <div className="relative rounded-md border border-dashed border-negative/50 p-3">
      <span className="absolute -top-2.5 left-3 bg-canvas px-2 text-[11px] font-medium uppercase tracking-wider text-negative">
        미검증 · 신뢰 불가
      </span>
      {children}
    </div>
  );
}
