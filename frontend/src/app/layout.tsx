import type { Metadata } from "next";
import Link from "next/link";

import { signOut } from "@/app/login/actions";
import { currentUser } from "@/lib/guard";
import "./globals.css";

export const metadata: Metadata = {
  title: "IFRS 18 Impact Analyzer",
  description:
    "Restructure a reported income statement under IFRS 18 and see exactly why operating profit changes.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const user = await currentUser();

  return (
    <html lang="ko">
      <body className="font-sans antialiased">
        <div className="border-b border-border">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3 sm:px-6">
            <Link href="/" className="text-xs font-medium tracking-tight">
              IFRS 18 Impact Analyzer
            </Link>
            {user ? (
              <form action={signOut} className="flex items-center gap-3">
                <span className="text-xs text-muted">{user.email}</span>
                <button type="submit" className="text-xs text-muted hover:text-ink">
                  로그아웃
                </button>
              </form>
            ) : (
              <Link href="/login" className="text-xs text-muted hover:text-ink">
                로그인
              </Link>
            )}
          </div>
        </div>
        {children}
      </body>
    </html>
  );
}
