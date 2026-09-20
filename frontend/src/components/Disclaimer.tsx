/**
 * Spec §24. The text comes from the API, never from a constant here, so the
 * screen and every export say exactly the same thing and a UI refactor cannot
 * drop it.
 */
export function Disclaimer({
  text,
  limitations,
}: {
  text: string;
  limitations?: string[];
}) {
  return (
    <section className="mt-12 border-t border-border pt-6" data-testid="disclaimer">
      <p className="text-xs leading-relaxed text-muted">{text}</p>
      {limitations && limitations.length > 0 ? (
        <>
          <h2 className="mt-6 text-[11px] font-medium uppercase tracking-[0.14em] text-muted">
            이 분석의 한계
          </h2>
          <ul className="mt-2 space-y-1">
            {limitations.map((item) => (
              <li key={item} className="text-xs leading-relaxed text-muted">
                — {item}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
