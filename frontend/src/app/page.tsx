import { api, type Disclaimer } from "@/lib/api";

/**
 * Landing page (spec §21 `/`).
 *
 * The §24 disclaimer and the product's scope limitations are fetched from the
 * API rather than hardcoded here. That is deliberate: they are an API-level
 * guarantee (see `app/api/routers/meta.py`), so they cannot drift between the
 * screen and an export, and cannot be lost in a UI refactor. If the API is
 * unreachable we say so instead of substituting a stale local copy.
 */
export default async function HomePage() {
  let disclaimer: Disclaimer | null = null;
  try {
    disclaimer = await api.disclaimer();
  } catch {
    disclaimer = null;
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-20 sm:px-6">
      <p className="text-xs uppercase tracking-[0.18em] text-muted">IFRS 18</p>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">
        Impact Analyzer
      </h1>
      <p className="mt-4 max-w-xl text-base leading-relaxed text-muted">
        재무제표를 업로드하면 IFRS 18 기준으로 손익계산서를 재구성하고, 영업이익이
        왜 변했는지 계정 단위까지 추적해 보여줍니다.
      </p>

      <div className="mt-10">
        <button
          type="button"
          disabled
          className="rounded-md border border-border bg-surface px-5 py-3 text-sm font-medium text-ink disabled:cursor-not-allowed disabled:opacity-60"
        >
          Upload Financial Statements
        </button>
        <p className="mt-2 text-xs text-muted">
          Phase 3에서 활성화됩니다. 현재는 Phase 1(기반 구조) 단계입니다.
        </p>
      </div>

      <section className="mt-16 border-t border-border pt-8">
        {disclaimer ? (
          <>
            <p className="text-sm leading-relaxed text-muted">{disclaimer.disclaimer_ko}</p>
            <p className="mt-2 text-xs leading-relaxed text-muted">{disclaimer.disclaimer_en}</p>
            <h2 className="mt-8 text-xs font-medium uppercase tracking-[0.14em] text-muted">
              Scope limitations
            </h2>
            <ul className="mt-3 space-y-1.5">
              {disclaimer.limitations_en.map((limitation) => (
                <li key={limitation} className="text-xs leading-relaxed text-muted">
                  — {limitation}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-caution">
            API에 연결할 수 없어 고지 문구를 불러오지 못했습니다. 백엔드가 실행 중인지
            확인해 주세요.
          </p>
        )}
      </section>
    </main>
  );
}
