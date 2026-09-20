/**
 * What went wrong, in the words the API used.
 *
 * The problem document's `detail` is written for a person — "An override has to
 * say why", "12500 is not an account in the dictionary" — so it is shown as-is
 * rather than being replaced with a generic message that hides which rule was
 * broken.
 */
export function FormError({ message }: { message?: string | null | undefined }) {
  if (!message) return null;
  return (
    <p className="mt-2 text-xs leading-relaxed text-negative" role="alert">
      {message}
    </p>
  );
}
