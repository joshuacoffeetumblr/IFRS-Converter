import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "IFRS 18 Impact Analyzer",
  description:
    "Restructure a reported income statement under IFRS 18 and see exactly why operating profit changes.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
