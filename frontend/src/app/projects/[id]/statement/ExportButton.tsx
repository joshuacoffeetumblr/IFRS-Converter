"use client";

import { useState } from "react";

/**
 * Downloading the workbook (spec §19, §34).
 *
 * When the analysis did not reconcile the plain download is refused by the
 * API. Rather than hiding the button, the screen makes the acknowledgement
 * explicit — and says what the file will carry — so someone investigating a
 * failure can still take it away, and nobody does so by accident.
 */
export function ExportButton({
  projectId,
  reconciled,
}: {
  projectId: string;
  reconciled: boolean;
}) {
  const [acknowledged, setAcknowledged] = useState(false);

  if (reconciled) {
    return (
      <a
        href={`/projects/${projectId}/export`}
        className="rounded-md border border-accent bg-accent/10 px-4 py-2 text-sm font-medium text-accent hover:bg-accent/15"
        data-testid="export-link"
      >
        Excel 내보내기
      </a>
    );
  }

  return (
    <div className="text-right">
      <label className="flex items-center justify-end gap-2 text-xs text-muted">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(event) => setAcknowledged(event.target.checked)}
        />
        정합성 실패를 확인했으며, 파일 전면에 경고가 표시됨을 이해합니다
      </label>
      <a
        href={`/projects/${projectId}/export?acknowledge_unreconciled=true`}
        aria-disabled={!acknowledged}
        onClick={(event) => {
          if (!acknowledged) event.preventDefault();
        }}
        className={
          acknowledged
            ? "mt-2 inline-block rounded-md border border-negative/50 bg-negative/5 px-4 py-2 text-sm font-medium text-negative"
            : "mt-2 inline-block cursor-not-allowed rounded-md border border-border px-4 py-2 text-sm font-medium text-muted opacity-60"
        }
        data-testid="export-link-unreconciled"
      >
        경고 표시된 파일로 내보내기
      </a>
    </div>
  );
}
