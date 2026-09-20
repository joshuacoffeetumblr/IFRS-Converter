import { clsx } from "clsx";
import { formatAmount, formatSigned, signOf } from "@/lib/format";

/**
 * A monetary figure.
 *
 * Tabular numerals and right alignment, because a column of financial figures
 * that does not align is a column nobody can scan. The value arrives as a
 * string and is only ever formatted here — never parsed into a number and back
 * (architecture §13).
 *
 * **Colour means movement, not sign.** Under this product's sign convention
 * every expense is negative, so colouring by sign would paint half of any
 * statement red and say nothing. A `signed` figure is a change — an effect on
 * operating profit, a delta against a check — and there the direction is the
 * information.
 */
export function Figure({
  value,
  signed = false,
  className,
}: {
  value: string | null | undefined;
  /** Render an explicit + or −, and colour by direction. For movements. */
  signed?: boolean;
  className?: string;
}) {
  const sign = signOf(value);
  return (
    <span
      className={clsx(
        "figure tabular-nums",
        signed && sign === "positive" && "text-positive",
        signed && sign === "negative" && "text-negative",
        className,
      )}
    >
      {signed ? formatSigned(value) : formatAmount(value)}
    </span>
  );
}
