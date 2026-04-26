"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { PricePoint } from "@/lib/types";
import { formatChartDate, formatChartMonth, formatCurrency } from "@/lib/utils";

export function PriceChart({ data, symbol }: { data: PricePoint[]; symbol: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{symbol} price</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <CartesianGrid stroke="#1f2937" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 12 }} tickFormatter={formatChartMonth} tickMargin={10} minTickGap={28} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} domain={["auto", "auto"]} />
              <Tooltip
                contentStyle={{ background: "#0d1117", border: "1px solid #1f2937", borderRadius: 8 }}
                formatter={(value) => formatCurrency(Number(value))}
                labelFormatter={formatChartDate}
              />
              <Line type="monotone" dataKey="close" stroke="#38bdf8" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
