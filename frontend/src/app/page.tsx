"use client";

import { useCallback, useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiClient, fmtDuration, fmtMoney, type Analytics, type CallRow } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/page-header";
import {
  RefreshCw,
  LayoutDashboard,
  PhoneCall,
  CheckCheck,
  ArrowLeftRight,
  TriangleAlert,
  ShoppingCart,
  Wallet,
  Clock,
  Gauge,
  BarChart3,
} from "lucide-react";
import { Button } from "@/components/ui/button";

export default function OverviewPage() {
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [recent, setRecent] = useState<CallRow[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [a, c] = await Promise.all([apiClient.analytics(), apiClient.calls()]);
      setAnalytics(a);
      setRecent(c.slice(0, 8));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const stats = analytics
    ? [
        { title: "Total Calls", value: analytics.total_calls, icon: PhoneCall },
        { title: "AI Resolved", value: analytics.ai_resolved, icon: CheckCheck },
        { title: "Transferred", value: analytics.transferred, icon: ArrowLeftRight },
        { title: "Failed / Abandoned", value: analytics.failed, icon: TriangleAlert },
        { title: "Orders Created", value: analytics.orders_created, icon: ShoppingCart },
        { title: "Voice Revenue", value: fmtMoney(analytics.revenue), icon: Wallet },
        { title: "Avg Call Duration", value: fmtDuration(analytics.avg_duration_seconds), icon: Clock },
        { title: "AI Resolution Rate", value: `${analytics.total_calls ? Math.round((analytics.ai_resolved / analytics.total_calls) * 100) : 0}%`, icon: Gauge },
      ]
    : [];

  return (
    <div className="space-y-6">
      <PageHeader
        icon={LayoutDashboard}
        title="Overview"
        description="Live voice commerce performance at a glance."
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

      {analytics && (
        <Card className="card-clean">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
                <BarChart3 className="h-4 w-4 text-[#0068FF]" />
              </span>
              Last 14 days — calls & orders
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={analytics.daily}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(d: string) => d.slice(5)} />
                  <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                  <Tooltip />
                  <Bar dataKey="calls" fill="#0044BA" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="orders" fill="#0068FF" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="transferred" fill="#000E49" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      )}

      <Card className="card-clean">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
              <PhoneCall className="h-4 w-4 text-[#0068FF]" />
            </span>
            Recent calls
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {recent.map((c) => (
                <div key={c.call_id} className="flex items-start justify-between gap-4 rounded-xl bg-[#F5F8FD] p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-medium">
                  {c.customer_name || c.customer_phone || "Unknown caller"}
                  <DispositionBadge disposition={c.disposition} />
                </div>
                <div className="truncate text-xs text-muted-foreground">{c.summary}</div>
              </div>
              <div className="shrink-0 text-xs text-muted-foreground">{fmtDuration(c.duration_seconds)}</div>
            </div>
          ))}
          {recent.length === 0 && !error && <div className="text-sm text-muted-foreground">No calls yet.</div>}
        </CardContent>
      </Card>
    </div>
  );
}

export function DispositionBadge({ disposition }: { disposition: string }) {
  const map: Record<string, string> = {
    completed: "bg-green-100 text-green-800 hover:bg-green-100",
    transferred: "bg-amber-100 text-amber-800 hover:bg-amber-100",
    failed: "bg-red-100 text-red-800 hover:bg-red-100",
    abandoned: "bg-gray-100 text-gray-700 hover:bg-gray-100",
  };
  return <Badge variant="secondary" className={map[disposition] || ""}>{disposition}</Badge>;
}
