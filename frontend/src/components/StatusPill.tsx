import { clsx } from "clsx";

const TONES = {
  neutral: "border-border text-muted",
  active: "border-accent/40 text-accent",
  good: "border-positive/40 text-positive",
  bad: "border-negative/40 text-negative",
  warn: "border-caution/40 text-caution",
} as const;

export type Tone = keyof typeof TONES;

export function StatusPill({
  children,
  tone = "neutral",
  className,
}: {
  children: React.ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const PROJECT_TONES: Record<string, Tone> = {
  DRAFT: "neutral",
  UPLOADED: "neutral",
  EXTRACTED: "active",
  EXTRACTION_FAILED: "bad",
  CLASSIFIED: "active",
  IN_REVIEW: "warn",
  RECONCILIATION_FAILED: "bad",
  FINALIZED: "good",
};

const PROJECT_LABELS: Record<string, string> = {
  DRAFT: "작성 중",
  UPLOADED: "업로드됨",
  EXTRACTED: "추출됨",
  EXTRACTION_FAILED: "추출 실패",
  CLASSIFIED: "분류됨",
  IN_REVIEW: "검토 중",
  RECONCILIATION_FAILED: "정합성 실패",
  FINALIZED: "확정됨",
};

export function ProjectStatusPill({ status }: { status: string }) {
  return (
    <StatusPill tone={PROJECT_TONES[status] ?? "neutral"}>
      {PROJECT_LABELS[status] ?? status}
    </StatusPill>
  );
}
