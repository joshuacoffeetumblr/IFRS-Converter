import type { Config } from "tailwindcss";

/**
 * "Financial terminal + modern SaaS" (spec §20): dense, neutral, analytical.
 * Deliberately no gradients and minimal motion. Numbers are the interface.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--canvas)",
        surface: "var(--surface)",
        border: "var(--border)",
        ink: "var(--ink)",
        muted: "var(--ink-muted)",
        accent: "var(--accent)",
        positive: "var(--positive)",
        negative: "var(--negative)",
        caution: "var(--caution)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        // Tabular figures are non-negotiable: financial columns must align.
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        "figure-lg": ["2.25rem", { lineHeight: "1.1", letterSpacing: "-0.02em" }],
        "figure-md": ["1.5rem", { lineHeight: "1.15", letterSpacing: "-0.01em" }],
      },
    },
  },
  plugins: [],
};

export default config;
