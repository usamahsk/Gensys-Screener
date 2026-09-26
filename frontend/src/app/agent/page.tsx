"use client";

import { useCallback, useEffect, useState } from "react";
import { apiClient, fmtDateTime, fmtDuration, fmtMoney, type CallRow, type OrderRow } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DispositionBadge } from "@/app/page";
import { PageHeader } from "@/components/page-header";
import { FileText, Headset, ShoppingCart, User } from "lucide-react";

/**
 * Agent View — what a human agent sees when a call is escalated:
 * escalation reason, AI summary, customer context, and related orders.
 */
export default function AgentViewPage() {
  const [transferred, setTransferred] = useState<CallRow[]>([]);
  const [selected, setSelected] = useState<CallRow | null>(null);
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const calls = await apiClient.calls({ disposition: "transferred" });
      setTransferred(calls);
      setSelected((prev) => prev && calls.find((c) => c.call_id === prev.call_id) || calls[0] || null);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (selected?.customer_phone) {
      apiClient.orders({ search: selected.customer_phone }).then(setOrders).catch(() => setOrders([]));
    } else if (selected) {
      apiClient.orders({ search: selected.order_numbers[0] || "" }).then(setOrders).catch(() => setOrders([]));
    } else {
      setOrders([]);
    }
  }, [selected]);

  return (
    <div className="space-y-4">
      <PageHeader
        icon={Headset}
        title="Agent View — Escalated Calls"
        description="Context handed over by the AI when it transfers a call. The agent sees this before picking up — the caller never has to repeat themselves."
      />

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-2">
          {transferred.map((c) => (
            <button
              key={c.call_id}
              onClick={() => setSelected(c)}
              className={`w-full rounded-md border p-3 text-left transition-colors ${
                selected?.call_id === c.call_id ? "border-primary bg-accent" : "hover:bg-muted/50"
              }`}
            >
              <div className="flex items-center justify-between text-sm font-medium">
                {c.customer_name || c.customer_phone || "Unknown"}
                <span className="text-xs text-muted-foreground">{fmtDateTime(c.started_at)}</span>
              </div>
              <div className="mt-1 truncate text-xs text-muted-foreground">{c.escalation_reason || c.summary}</div>
            </button>
          ))}
          {transferred.length === 0 && !error && (
            <div className="rounded-md border p-6 text-center text-sm text-muted-foreground">
              No escalated calls. 🎉
            </div>
          )}
        </div>

        {selected && (
          <div className="space-y-4">
            <Card className="border-amber-200 bg-amber-50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm text-amber-800">
                  <Badge variant="secondary" className="bg-amber-200 text-amber-900 hover:bg-amber-200">
                    Escalation reason
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm text-amber-900">
                {selected.escalation_reason || "Not specified"}
              </CardContent>
            </Card>

            <Card className="card-clean">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
                  <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
                    <FileText className="h-4 w-4 text-[#0068FF]" />
                  </span>
                  AI Summary
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm">{selected.summary}</CardContent>
            </Card>

            <div className="grid gap-4 md:grid-cols-2">
              <Card className="card-clean">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
                    <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
                      <User className="h-4 w-4 text-[#0068FF]" />
                    </span>
                    Customer
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-1 text-sm">
                  <div className="font-medium">{selected.customer_name || "Unknown"}</div>
                  <div className="text-muted-foreground">{selected.customer_phone || "—"}</div>
                  <div className="text-xs text-muted-foreground">
                    Call duration {fmtDuration(selected.duration_seconds)} · {fmtDateTime(selected.started_at)}
                  </div>
                </CardContent>
              </Card>
              <Card className="card-clean">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
                    <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
                      <ShoppingCart className="h-4 w-4 text-[#0068FF]" />
                    </span>
                    Related Sessions
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {(selected.order_numbers.length > 0 || orders.length > 0
                    ? orders.filter((o) => selected.order_numbers.includes(o.order_number)).length > 0
                      ? orders.filter((o) => selected.order_numbers.includes(o.order_number))
                      : orders.slice(0, 3)
                    : []
                  ).map((o) => (
                    <div key={o.order_number} className="flex items-center justify-between">
                      <span className="font-mono text-xs font-semibold">{o.order_number}</span>
                      <Badge variant="secondary" className="capitalize">{o.status.replace("_", " ")}</Badge>
                      <span className="text-xs">{fmtMoney(o.total)}</span>
                    </div>
                  ))}
                  {selected.order_numbers.length === 0 && orders.length === 0 && (
                    <div className="text-muted-foreground">No orders on this call.</div>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
