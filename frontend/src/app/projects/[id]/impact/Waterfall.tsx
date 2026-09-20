"use client";

import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { WaterfallStep } from "@/lib/api";
import { formatAmount, formatSigned, toChartNumber } from "@/lib/format";

interface BridgeDatum {
  key: string;
  label: string;
  kind: WaterfallStep["kind"];
  raw: string;
  /** The invisible floor a delta bar sits on. A drawing concern, not a figure. */
  base: number;
  magnitude: number;
}

/**
 * Turn the server's steps into bar positions.
 *
 * A module-level function, not a loop inside the component: the running total
 * is scaffolding for the drawing, and keeping it out of render makes that
 * explicit. Nothing here changes a value — every number still comes from the
 * server, and `raw` carries the exact string for the tooltip.
 */
function toBridge(steps: WaterfallStep[]): BridgeDatum[] {
  const data: BridgeDatum[] = [];
  let running = 0;

  for (const step of steps) {
    const value = toChartNumber(step.value);
    const anchor = step.kind === "START" || step.kind === "END";
    const base = anchor ? 0 : value >= 0 ? running : running + value;
    running = anchor ? value : running + value;
    data.push({
      key: step.key,
      label: step.label_ko,
      kind: step.kind,
      raw: step.value,
      base,
      magnitude: Math.abs(value),
    });
  }
  return data;
}

/**
 * The operating-profit bridge (spec §22).
 *
 * Drawn as a stacked bar with a transparent base — the standard way to make a
 * waterfall out of a bar chart. The *cumulative* positions are computed here,
 * because they are a property of the drawing, not of the accounting: every
 * value and every total already came from the server, and this chart never
 * changes one.
 */
export function Waterfall({ steps }: { steps: WaterfallStep[] }) {
  if (steps.length === 0) {
    return (
      <p className="mt-4 rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
        비교할 영업이익 소계가 없어 변동 경로를 그릴 수 없습니다.
      </p>
    );
  }

  const data = toBridge(steps);

  return (
    <div className="mt-4 h-80 w-full" data-testid="waterfall">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 48, left: 8 }}>
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11 }}
            interval={0}
            angle={-30}
            textAnchor="end"
            stroke="var(--ink-muted)"
          />
          <YAxis
            tick={{ fontSize: 11 }}
            stroke="var(--ink-muted)"
            tickFormatter={(value: number) => formatAmount(String(value))}
            width={80}
          />
          <Tooltip
            cursor={{ fill: "var(--border)", fillOpacity: 0.3 }}
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              fontSize: 12,
            }}
            formatter={(_value, _name, entry) => {
              const item = entry?.payload as { raw: string; kind: string } | undefined;
              if (!item) return ["", ""];
              return [
                item.kind === "DELTA" || item.kind === "UNATTRIBUTED"
                  ? formatSigned(item.raw)
                  : formatAmount(item.raw),
                item.kind === "START" ? "보고 기준" : item.kind === "END" ? "IFRS 18" : "변동",
              ];
            }}
          />
          {/* The invisible base is what turns a bar chart into a waterfall. */}
          <Bar dataKey="base" stackId="bridge" fill="transparent" isAnimationActive={false} />
          <Bar dataKey="magnitude" stackId="bridge" isAnimationActive={false}>
            {data.map((item) => (
              <Cell
                key={item.key}
                fill={
                  item.kind === "START" || item.kind === "END"
                    ? "var(--accent)"
                    : item.raw.startsWith("-")
                      ? "var(--negative)"
                      : "var(--positive)"
                }
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
