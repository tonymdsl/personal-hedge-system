"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { PricePoint } from "@/lib/types";
import { formatChartDate, formatChartMonth, formatPercent } from "@/lib/utils";

function buildDrawdown(data: PricePoint[]) {
  let peak = Number.NEGATIVE_INFINITY;
  return data.map((point) => {
    peak = Math.max(peak, point.close);
    return { date: point.date, drawdown: point.close / peak - 1 };
  });
}

export function DrawdownChart({ data }: { data: PricePoint[] }) {
  const chartData = buildDrawdown(data);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Drawdown</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData}>
              <CartesianGrid stroke="#1f2937" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 12 }} tickFormatter={formatChartMonth} tickMargin={10} minTickGap={28} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickFormatter={(value) => `${(Number(value) * 100).toFixed(0)}%`} />
              <Tooltip
                contentStyle={{ background: "#0d1117", border: "1px solid #1f2937", borderRadius: 8 }}
                formatter={(value) => formatPercent(Number(value))}
                labelFormatter={formatChartDate}
              />
              <Area type="monotone" dataKey="drawdown" stroke="#ef4444" fill="#ef444420" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
