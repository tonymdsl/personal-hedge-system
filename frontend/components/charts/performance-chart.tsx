"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatChartDate, formatChartMonth, formatPercent } from "@/lib/utils";

export function PerformanceChart({ data, title = "Performance" }: { data: { date: string; value: number }[]; title?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data}>
              <defs>
                <linearGradient id="performanceFill" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#38bdf8" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#1f2937" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 12 }} tickFormatter={formatChartMonth} tickMargin={10} minTickGap={28} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickFormatter={(value) => `${(Number(value) * 100).toFixed(0)}%`} />
              <Tooltip
                contentStyle={{ background: "#0d1117", border: "1px solid #1f2937", borderRadius: 8 }}
                formatter={(value) => formatPercent(Number(value))}
                labelFormatter={formatChartDate}
              />
              <Area type="monotone" dataKey="value" stroke="#38bdf8" fill="url(#performanceFill)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
