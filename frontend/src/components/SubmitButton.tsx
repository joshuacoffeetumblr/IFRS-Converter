"use client";

import { clsx } from "clsx";
import { useFormStatus } from "react-dom";

/**
 * A button that disables itself while its form action runs.
 *
 * Every mutation in this product is a server action, and several of them —
 * extraction, classification, finalization — take long enough that a second
 * click is a real possibility. Re-running classification twice is harmless but
 * confusing; finalizing twice is a 409.
 */
export function SubmitButton({
  children,
  pendingLabel,
  variant = "primary",
  className,
  ...props
}: {
  children: React.ReactNode;
  pendingLabel?: string;
  variant?: "primary" | "secondary" | "danger";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending || props.disabled}
      {...props}
      className={clsx(
        "rounded-md border px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60",
        variant === "primary" && "border-accent bg-accent/10 text-accent hover:bg-accent/15",
        variant === "secondary" && "border-border bg-surface text-ink hover:bg-border/40",
        variant === "danger" && "border-negative/50 bg-negative/5 text-negative hover:bg-negative/10",
        className,
      )}
    >
      {pending && pendingLabel ? pendingLabel : children}
    </button>
  );
}
