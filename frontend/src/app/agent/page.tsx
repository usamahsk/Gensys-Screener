"use client";

import { useCallback, useEffect, useState } from "react";
import { apiClient, fmtDateTime, fmtDuration, fmtMoney, type CallDetail, type CallRow, type OrderRow } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DispositionBadge } from "@/app/page";
import { PageHeader } from "@/components/page-header";
import { ChatTranscript } from "@/components/chat-transcript";
import { FileText, Headset, RefreshCw, ShoppingCart, User, Volume2 } from "lucide-react";

/**
 * Agent View — what a human agent sees when a call is transferred from Dograh:
 * transfer reason, AI conversation summary, and the transcript tail so the
 * agent can continue the call without making the caller repeat themselves.
 *
 * Data source: every call that ends with a transfer status
 * (POST /api/webhooks/call-ended with disposition=transfer*, or
 * POST /api/webhooks/transfer) lands here with disposition=transferred.
 */
export default function AgentViewPage() {
  const [transferred, setTransferred] = useState<CallRow[]>([]);
  const [selected, setSelected] = useState<CallRow | null>(null);
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
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

  // Full conversation for the selected transfer: transcript + tool calls +
  // summary to continue the call.
  useEffect(() => {
    if (!selected) {
      setDetail(null);
      setOrders([]);
      return;
    }
    setDetailLoading(true);
    apiClient.callDetail(selected.call_id)
      .then((d) => setDetail(d))
      .catch(() => setDetail(null))
      .finally(() => setDetailLoading(false));
    if (selected.customer_phone) {
      apiClient.orders({ search: selected.customer_phone }).then(setOrders).catch(() => setOrders([]));
    } else if (selected.order_numbers[0]) {
      apiClient.orders({ search: selected.order_numbers[0] }).then(setOrders).catch(() => setOrders([]));
    } else {
      setOrders([]);
    }
  }, [selected]);

  const summary = detail?.summary || selected?.summary || "";
  const escalationReason = detail?.escalation_reason || selected?.escalation_reason || "";
  const transcript = detail?.transcript || [];
  const toolCalls = detail?.tool_calls || [];

  return (
    <div className="space-y-4">
      <PageHeader
        icon={Headset}
        title="Agent View — Escalated Calls"
        description="Transferred calls land here with the handoff summary + conversation so far. Read the summary, then continue the call — the caller never repeats themselves."
        actions={
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className="mr-2 h-4 w-4" /> Refresh
          </Button>
        }
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
              <div className="flex items-center justify-between gap-2 text-sm font-medium">
                <span className="truncate">{c.customer_name || c.customer_phone || "Unknown"}</span>
                <DispositionBadge disposition={c.disposition} />
              </div>
              <div className="mt-1 flex items-center justify-between gap-2">
                <div className="truncate text-xs text-muted-foreground">{c.escalation_reason || c.summary || "Transferred"}</div>
                <span className="shrink-0 text-xs text-muted-foreground">{fmtDateTime(c.started_at)}</span>
              </div>
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
                    Transfer — reason to continue from
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm text-amber-900">
                {escalationReason || "Transferred from the voice agent — see summary below."}
              </CardContent>
            </Card>

            <Card className="card-clean">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
                  <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
                    <FileText className="h-4 w-4 text-[#0068FF]" />
                  </span>
                  Conversation summary — continue the call from here
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm">{summary || "Summary arrives when the call ends."}</CardContent>
            </Card>

            <Card className="card-clean">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
                  Conversation so far
                  {transcript.length > 0 && (
                    <Badge variant="outline" className="ml-1">{transcript.length} turns</Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {detailLoading ? (
                  <div className="text-sm text-muted-foreground">Loading conversation…</div>
                ) : (
                  <ChatTranscript turns={transcript} />
                )}
                {toolCalls.length > 0 && (
                  <div className="mt-3 space-y-1">
                    <div className="text-xs font-semibold text-muted-foreground">What the voice agent already did</div>
                    {toolCalls.map((t, i) => (
                      <div key={i} className="flex min-w-0 items-center gap-2 rounded-lg bg-muted/60 px-3 py-1.5 font-mono text-xs">
                        <Badge variant="outline" className="shrink-0">{t.name}</Badge>
                        <span className="min-w-0 flex-1 truncate text-muted-foreground">{t.args_summary}</span>
                      </div>
                    ))}
                  </div>
                )}
                {(detail?.recording_url || selected.recording_url) && (
                  <div className="mt-3 rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                    <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                      <Volume2 className="h-3.5 w-3.5" /> Call recording
                    </div>
                    <audio controls className="w-full" src={detail?.recording_url || selected.recording_url} />
                  </div>
                )}
              </CardContent>
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
                  <div className="font-medium">{selected.customer_name || detail?.customer_name || "Unknown"}</div>
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
