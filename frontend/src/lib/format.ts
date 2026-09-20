import { Decimal } from "decimal.js";

/**
 * Display formatting. **This module formats; it never computes.**
 *
 * Architecture §13 puts every accounting decision on the server, and API spec
 * §1 sends monetary values as strings for a reason: a JSON number is an
 * IEEE-754 double and cannot round-trip `numeric(38, 6)`. So values stay
 * strings all the way here, and `Decimal` is used only to place a decimal
 * point and a thousands separator — never to add, subtract or reconcile.
 */

/** A scale of 1e6 means the statement is stated in 백만원. */
export const SCALE_LABELS: Record<number, string> = {
  0: "원",
  3: "천원",
  6: "백만원",
  9: "십억원",
  12: "조원",
};

export function scaleLabel(scale: number): string {
  return SCALE_LABELS[scale] ?? `10^${scale}`;
}

/** A figure as it should appear in a column: grouped, no trailing zeros. */
export function formatAmount(value: string | null | undefined, fractionDigits = 0): string {
  if (value === null || value === undefined || value === "") return "—";
  try {
    const decimal = new Decimal(value);
    const [whole = "0", fraction] = decimal.toFixed(fractionDigits).split(".");
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return fraction ? `${grouped}.${fraction}` : grouped;
  } catch {
    // An unparseable figure is shown as it arrived rather than as zero: a
    // wrong number is worse than a visibly odd one.
    return value;
  }
}

export function formatPercent(value: string | null | undefined, fractionDigits = 2): string {
  if (value === null || value === undefined || value === "") return "—";
  try {
    return `${new Decimal(value).toFixed(fractionDigits)}%`;
  } catch {
    return value;
  }
}

export function formatSigned(value: string | null | undefined, fractionDigits = 0): string {
  if (value === null || value === undefined || value === "") return "—";
  try {
    const decimal = new Decimal(value);
    const rendered = formatAmount(decimal.abs().toString(), fractionDigits);
    if (decimal.isZero()) return rendered;
    return decimal.isNegative() ? `−${rendered}` : `+${rendered}`;
  } catch {
    return value;
  }
}

/** Sign as a three-way category, for colouring. Never derived from a parse of a number. */
export function signOf(value: string | null | undefined): "positive" | "negative" | "zero" {
  if (!value) return "zero";
  try {
    const decimal = new Decimal(value);
    if (decimal.isZero()) return "zero";
    return decimal.isNegative() ? "negative" : "positive";
  } catch {
    return "zero";
  }
}

/** For charts, which need a number. Display only — never fed back into a figure. */
export function toChartNumber(value: string | null | undefined): number {
  if (!value) return 0;
  try {
    return new Decimal(value).toNumber();
  } catch {
    return 0;
  }
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Seoul",
  }).format(parsed);
}
