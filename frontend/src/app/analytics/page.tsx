"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiClient, fmtDuration, fmtMoney, type Analytics } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import {
  RefreshCw,
  BarChart3,
  PhoneCall,
  CheckCheck,
  ArrowLeftRight,
  TriangleAlert,
  ShoppingCart,
  Wallet,
  Clock,
  Gauge,
  PieChart as PieIcon,
} from "lucide-react";

const PIE_COLORS = ["#16a34a", "#f59e0b", "#ef4444", "#64748b", "#0068FF"];

export default function AnalyticsPage() {
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await apiClient.analytics());
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load analytics");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const stats = data
    ? [
        { title: "Total Calls", value: data.total_calls, icon: PhoneCall },
        { title: "AI Resolved", value: data.ai_resolved, icon: CheckCheck },
        { title: "Transferred", value: data.transferred, icon: ArrowLeftRight },
        { title: "Failed / Abandoned", value: data.failed, icon: TriangleAlert },
        { title: "Transactions", value: data.orders_created, icon: ShoppingCart },
        { title: "Voice Revenue", value: fmtMoney(data.revenue), icon: Wallet },
        { title: "Avg Call Duration", value: fmtDuration(data.avg_duration_seconds), icon: Clock },
        { title: "AI Resolution Rate", value: `${data.total_calls ? Math.round((data.ai_resolved / data.total_calls) * 100) : 0}%`, icon: Gauge },
      ]
    : [];

  const dispositionPie = useMemo(() => {
    if (!data) return [];
    const other = Math.max(0, data.total_calls - data.ai_resolved - data.transferred - data.failed);
    return [
      { name: "AI resolved", value: data.ai_resolved },
      { name: "Transferred", value: data.transferred },
      { name: "Failed / abandoned", value: data.failed },
      ...(other > 0 ? [{ name: "Other", value: other }] : []),
    ].filter((d) => d.value > 0);
  }, [data]);

  const revenueDaily = useMemo(
    () => (data?.daily || []).map((d) => ({ ...d, day: d.date.slice(5) })),
    [data]
  );

  return (
    <div className="space-y-6">
      <PageHeader
        icon={BarChart3}
        title="Analytics"
        description="Call outcomes, revenue and trends across every transaction."
        actions={
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className="mr-2 h-4 w-4" /> Refresh
          </Button>
        }
      />

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((s) => (
          <Card key={s.title} className="card-clean">
            <CardContent className="flex items-center gap-3 p-4">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#EAF1FF]">
                <s.icon className="h-5 w-5 text-[#0068FF]" />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-2xl font-extrabold leading-tight text-[#000E49]">
                  {s.value}
                </span>
                <span className="block text-xs text-muted-foreground">{s.title}</span>
              </span>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <Card className="card-clean">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
                <BarChart3 className="h-4 w-4 text-[#0068FF]" />
              </span>
              Last 14 days — calls, transactions & transfers
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={revenueDaily}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="calls" name="Calls" fill="#0044BA" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="orders" name="Transactions" fill="#0068FF" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="transferred" name="Transferred" fill="#f59e0b" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        <Card className="card-clean">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
                <PieIcon className="h-4 w-4 text-[#0068FF]" />
              </span>
              Call outcomes
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={dispositionPie} dataKey="value" nameKey="name" outerRadius={90} label={{ fontSize: 11 }}>
                    {dispositionPie.map((_, i) => (
                      <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="card-clean">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
              <Wallet className="h-4 w-4 text-[#0068FF]" />
            </span>
            Daily revenue
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={revenueDaily}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v) => fmtMoney(Number(v))} />
                <Bar dataKey="revenue" name="Revenue" fill="#16a34a" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
