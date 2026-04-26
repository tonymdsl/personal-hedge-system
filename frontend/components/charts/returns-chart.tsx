"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { PricePoint } from "@/lib/types";
import { formatChartDate, formatChartMonth, formatPercent } from "@/lib/utils";

function buildReturns(data: PricePoint[]) {
  return data.slice(1).map((point, index) => ({
    date: point.date,
    daily_return: point.close / data[index].close - 1
  }));
}

export function ReturnsChart({ data }: { data: PricePoint[] }) {
  const chartData = buildReturns(data).slice(-90);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Daily returns</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData}>
              <CartesianGrid stroke="#1f2937" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 12 }} tickFormatter={formatChartMonth} tickMargin={10} minTickGap={24} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} tickFormatter={(value) => `${(Number(value) * 100).toFixed(0)}%`} />
              <Tooltip
                contentStyle={{ background: "#0d1117", border: "1px solid #1f2937", borderRadius: 8 }}
                formatter={(value) => formatPercent(Number(value))}
                labelFormatter={formatChartDate}
              />
              <Bar dataKey="daily_return" fill="#38bdf8" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
