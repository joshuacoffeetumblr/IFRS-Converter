import { checkReachable, type Reachability } from "@/lib/api";
import { LoginForm } from "./LoginForm";

/**
 * The sign-in screen (spec §31).
 *
 * A server component, so it can ask whether the app is usable *before* anyone
 * types a password. A deployment whose API is up but whose database is not
 * fails at exactly the same place as a wrong password — the form comes back
 * with an error — and the first thing a person concludes is that sign-up is
 * broken. Naming the piece that is down, on the screen where they are stuck,
 * is worth more than any message the form can produce afterwards.
 */
export default async function LoginPage() {
  const reachable = await checkReachable();

  return (
    <main className="mx-auto max-w-sm px-4 py-24 sm:px-6">
      <p className="text-xs uppercase tracking-[0.18em] text-muted">IFRS 18</p>
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">Impact Analyzer</h1>
      <p className="mt-3 text-sm leading-relaxed text-muted">
        분석은 업로드한 사람에게만 보입니다. 계정이 없다면 아래에서 바로 만들 수 있습니다.
      </p>

      {reachable.ok ? null : <ServiceDown reachable={reachable} />}

      <LoginForm />
    </main>
  );
}

/** What is down, and which setting points at it. */
function ServiceDown({ reachable }: { reachable: Extract<Reachability, { ok: false }> }) {
  const noApi = reachable.reason === "api";
  return (
    <div
      role="alert"
      className="mt-6 rounded-md border border-negative/40 bg-negative/5 px-3 py-3 text-xs leading-relaxed"
    >
      <p className="font-medium text-negative">
        {noApi
          ? "서버에 연결할 수 없습니다."
          : "서버는 켜져 있지만 데이터베이스에 연결되지 않았습니다."}
      </p>
      <p className="mt-1 text-muted">
        이 상태에서는 계정을 만들 수도, 로그인할 수도 없습니다. 입력하신 정보의 문제가 아닙니다.
      </p>
      <p className="mt-2 text-muted">
        {noApi ? (
          <>
            웹 서비스의 <code className="font-mono">IFRS18_API_URL</code> 이{" "}
            <code className="font-mono break-all">{reachable.apiUrl}</code> 로 되어 있습니다. 주소가
            맞는지, API 서비스가 실행 중인지 확인해 주세요.
          </>
        ) : (
          <>
            API 서비스의 <code className="font-mono">IFRS18_DATABASE_URL</code> 이 맞는지,
            데이터베이스가 실행 중인지 확인해 주세요.{" "}
            <code className="font-mono break-all">{reachable.apiUrl}/api/ready</code> 를 열면 같은
            점검 결과를 직접 볼 수 있습니다.
          </>
        )}
      </p>
    </div>
  );
}
