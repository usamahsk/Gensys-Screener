"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Pagination, PAGE_SIZE } from "@/components/pagination";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  apiClient,
  fmtDateTime,
  fmtDuration,
  fmtMoney,
  API_URL,
  type CallDetail,
  type OrderDetail,
  type OrderRow,
  type TransactionCall,
  type TransactionRow,
} from "@/lib/api";
import { ChatTranscript, parseRawTranscript } from "@/components/chat-transcript";
import { PageHeader } from "@/components/page-header";
import { DispositionBadge } from "@/app/page";
import { toast } from "sonner";
import {
  ClipboardList,
  PhoneCall,
  ShoppingCart,
  RefreshCw,
  Search,
  Clock,
  User,
  FileText,
  ExternalLink,
  Volume2,
  Layers,
  Download,
  Database,
} from "lucide-react";

const DISPOSITIONS = ["completed", "transferred", "failed", "abandoned"];

const STATUSES = ["confirmed", "preparing", "ready", "in_transit", "completed", "cancelled"];

function statusClass(s: string) {
  const map: Record<string, string> = {
    confirmed: "bg-blue-100 text-blue-800",
    preparing: "bg-amber-100 text-amber-800",
    ready: "bg-purple-100 text-purple-800",
    in_transit: "bg-cyan-100 text-cyan-800",
    completed: "bg-green-100 text-green-800",
    cancelled: "bg-red-100 text-red-800",
  };
  return map[s] || "bg-gray-100 text-gray-700";
}

function TypeBadge({ type }: { type: string }) {
  if (type === "create_order") {
    return (
      <Badge className="bg-[#0068FF] text-white hover:bg-[#0057DC]">
        <ShoppingCart className="mr-1 h-3 w-3" /> create order
      </Badge>
    );
  }
  if (type === "order_status") {
    return (
      <Badge variant="secondary" className="bg-cyan-100 text-cyan-900 hover:bg-cyan-100">
        <FileText className="mr-1 h-3 w-3" /> order status
      </Badge>
    );
  }
  return (
    <Badge variant="outline">
      <PhoneCall className="mr-1 h-3 w-3" /> {type || "call"}
    </Badge>
  );
}

interface RunToolCall {
  name: string;
  args: string;
  result: string;
}

interface RunEvidence {
  recordingUrl: string | null;
  transcriptUrl: string | null;
  transcript: string;
  gatheredContext: Record<string, unknown>;
  initialContext: Record<string, unknown>;
  toolCalls: RunToolCall[];
  runId: number | null;
  workflowId: number | null;
}

function stringifyToolValue(v: unknown, max = 2000): string {
  if (typeof v === "string") return v.slice(0, max);
  if (v === null || v === undefined) return "";
  try {
    return JSON.stringify(v, null, 2).slice(0, max);
  } catch {
    return String(v).slice(0, max);
  }
}

/** Full tool-call log like the Dograh run details: name + arguments + result. */
function toolCallsFromRunDetail(detail: Record<string, unknown>): RunToolCall[] {
  const logs = (detail["logs"] as Record<string, unknown>) || {};
  const raw = logs["realtime_feedback_events"];
  if (!Array.isArray(raw)) return [];
  const started = new Map<string, { name: string; args: string }>();
  const out: RunToolCall[] = [];
  for (const e of raw) {
    if (typeof e !== "object" || e === null) continue;
    const type = String((e as Record<string, unknown>)["type"] || "");
    const p = ((e as Record<string, unknown>)["payload"] as Record<string, unknown>) || {};
    if (type === "rtf-function-call-start") {
      started.set(String(p["tool_call_id"] || ""), {
        name: String(p["function_name"] || "?"),
        args: stringifyToolValue(p["arguments"]),
      });
    } else if (type === "rtf-function-call-end") {
      const s = started.get(String(p["tool_call_id"] || "")) || {
        name: String(p["function_name"] || "?"),
        args: "",
      };
      started.delete(String(p["tool_call_id"] || ""));
      out.push({ name: s.name, args: s.args, result: stringifyToolValue(p["result"]) });
    }
  }
  for (const s of started.values()) out.push({ name: s.name, args: s.args, result: "" });
  return out;
}

function proxiedFileUrl(url: string, filename?: string, download = false) {
  const qs = new URLSearchParams({ url });
  if (download) qs.set("download", "1");
  if (filename) qs.set("filename", filename);
  return `${API_URL}/api/integration/dograh/file?${qs.toString()}`;
}

function downloadTextFile(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
}

function turnsToText(turns: { role: string; content: string }[]) {
  return turns.map((t) => `${t.role}: ${t.content}`).join("\n");
}

function isProviderSid(id: string) {
  const s = id.trim();
  return (
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s) ||
    /^[A-Za-z]{0,4}[0-9a-f-]{16,}$/i.test(s)
  );
}

function effectiveProviderId(call: TransactionCall | null, orders: OrderRow[]): string {
  // Only real provider ids from the run's context: stored provider id,
  // Genesys conversation, or a session-UUID-like call_id. Never the
  // numeric Dograh run id — that lives in the Run field. Use the
  // "Backfill provider IDs" button to pull context ids into old rows.
  const direct = call?.provider_call_id || orders[0]?.provider_call_id || "";
  if (direct) return direct;
  if (call?.genesys_conversation_id) return call.genesys_conversation_id;
  const cid = (call?.call_id || "").trim();
  if (cid && !cid.startsWith("voice_") && isProviderSid(cid)) return cid;
  return "";
}

export default function TransactionsPage() {
  const [sessions, setSessions] = useState<TransactionRow[]>([]);
  const [search, setSearch] = useState("");
  const [disposition, setDisposition] = useState("all");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [selected, setSelected] = useState<TransactionRow | null>(null);
  const [backfilling, setBackfilling] = useState(false);
  const [callDetail, setCallDetail] = useState<CallDetail | null>(null);
  const [orderDetails, setOrderDetails] = useState<OrderDetail[]>([]);
  const [runEvidence, setRunEvidence] = useState<RunEvidence | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClient.transactions({
        disposition: disposition === "all" ? undefined : disposition,
        search: search.trim() || undefined,
      });
      setSessions(data.transactions);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load transactions");
    } finally {
      setLoading(false);
    }
  }, [disposition, search]);

  useEffect(() => {
    const t = setTimeout(load, search ? 400 : 0);
    return () => clearTimeout(t);
  }, [load]);

  useEffect(() => {
    setPage(1);
  }, [disposition, search]);

  const backfill = async () => {
    setBackfilling(true);
    try {
      const r = await apiClient.backfillProviderIds();
      const bits = [
        `${r.updated_calls} calls`,
        `${r.updated_orders} orders`,
      ];
      if (r.updated_customers) bits.push(`${r.updated_customers} customer details`);
      toast.success(`Backfilled ${bits.join(", ")}`);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Backfill failed");
    } finally {
      setBackfilling(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(sessions.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const paged = sessions.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const openSession = async (s: TransactionRow) => {
    setSelected(s);
    setCallDetail(null);
    setOrderDetails([]);
    setRunEvidence(null);
    setDetailLoading(true);
    try {
      if (s.call) {
        try {
          setCallDetail(await apiClient.callDetail(s.call.call_id));
        } catch {
          setCallDetail(null);
        }
      }
      const ods: OrderDetail[] = [];
      for (const o of s.orders) {
        try {
          ods.push(await apiClient.orderDetail(o.order_number));
        } catch {
          /* order row already carries the summary */
        }
      }
      setOrderDetails(ods);
      await loadRunEvidence(s);
    } finally {
      setDetailLoading(false);
    }
  };

  const loadRunEvidence = async (s: TransactionRow) => {
    let runId = s.call?.run_id || null;
    let workflowId = s.call?.workflow_id || null;
    if ((!runId || !workflowId) && s.call?.call_id) {
      try {
        const found = await apiClient.findRunByCallId(s.call.call_id);
        if (found.found && found.run_id && found.workflow_id) {
          runId = found.run_id;
          workflowId = found.workflow_id;
        }
      } catch {
        /* no run on the voice platform */
      }
    }
    if (!runId || !workflowId) return;
    try {
      const [t, detail] = await Promise.all([
        apiClient.dograhRunTranscript(runId, workflowId),
        apiClient.dograhRunDetail(runId, workflowId),
      ]);
      const rec = detail.recording_public_url || detail.recording_url;
      const tUrl =
        (typeof detail.transcript_public_url === "string" && detail.transcript_public_url) ||
        (typeof detail.transcript_url === "string" && detail.transcript_url) ||
        null;
      setRunEvidence({
        recordingUrl: typeof rec === "string" ? rec : null,
        transcriptUrl: tUrl,
        transcript: t.transcript,
        gatheredContext: (detail.gathered_context as Record<string, unknown>) || {},
        initialContext: (detail.initial_context as Record<string, unknown>) || {},
        toolCalls: toolCallsFromRunDetail(detail),
        runId,
        workflowId,
      });
    } catch {
      setRunEvidence(null);
    }
  };

  const openTrace = async () => {
    const c = selected?.call;
    if (!c) return;
    try {
      const params =
        c.run_id && c.workflow_id
          ? { run_id: c.run_id, workflow_id: c.workflow_id }
          : { call_id: c.call_id };
      const { url } = await apiClient.langfuseTraceUrl(params);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not build trace link (check Langfuse settings)");
    }
  };

  const stats = useMemo(() => {
    const withOrders = sessions.filter((s) => s.orders.length > 0).length;
    const talk = sessions.reduce((sum, s) => sum + (s.call?.duration_seconds || 0), 0);
    return [
      { label: "Total transactions", value: sessions.length, icon: Layers },
      { label: "With orders", value: withOrders, icon: ShoppingCart },
      { label: "Talk time", value: fmtDuration(talk), icon: Clock },
    ];
  }, [sessions]);

  const chain = selected
    ? {
        provider: selected.call?.provider || selected.orders[0]?.provider || "dograh",
        providerCallId: effectiveProviderId(selected.call, selected.orders),
        genesys: selected.call?.genesys_conversation_id || "",
        runId: selected.call?.run_id || null,
        callId: selected.call?.call_id || selected.orders[0]?.call_id || "",
      }
    : null;

  // ---- dialog view-model ----
  // Single recording: the combined call recording first, run recording next,
  // per-channel audio only as a last resort.
  const recordingSrc =
    callDetail?.recording_url ||
    (typeof runEvidence?.recordingUrl === "string" ? runEvidence.recordingUrl : "") ||
    callDetail?.user_recording_url ||
    callDetail?.bot_recording_url ||
    "";
  // Conversation: saved turns first, run transcript text as fallback.
  const turns =
    callDetail && callDetail.transcript.length > 0
      ? callDetail.transcript
      : runEvidence?.transcript
        ? parseRawTranscript(runEvidence.transcript)
        : [];
  // Tool calls: full args/result from the Dograh run when linked, else the
  // saved tool-call log.
  const savedTools = (callDetail?.tool_calls || []).map((t) => ({
    name: t.name,
    args: t.args_summary || "",
    result:
      typeof t.result === "string"
        ? t.result
        : t.result == null
          ? ""
          : JSON.stringify(t.result, null, 2),
  }));
  const allTools = runEvidence && runEvidence.toolCalls.length > 0 ? runEvidence.toolCalls : savedTools;
  const initialContext = runEvidence?.initialContext || {};
  const gatheredContext = runEvidence?.gatheredContext || {};

  return (
    <div className="space-y-4">
      <PageHeader
        icon={ClipboardList}
        title="Transactions"
        description="Every call and order in one place — transaction record, linked orders, run evidence and the provider chain."
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={backfill} disabled={backfilling} title="Fill empty Provider IDs and missing customer details on old rows from run details">
              <Database className={`mr-2 h-4 w-4 ${backfilling ? "animate-pulse" : ""}`} />
              {backfilling ? "Backfilling…" : "Backfill missing data"}
            </Button>
            <Button variant="outline" size="sm" onClick={load} disabled={loading}>
              <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
            </Button>
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 sm:gap-3">
        {stats.map(({ label, value, icon: Icon }) => (
          <Card key={label} className="card-clean">
            <CardContent className="flex items-center gap-3 p-4">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#EAF1FF]">
                <Icon className="h-5 w-5 text-[#0068FF]" />
              </span>
              <span>
                <span className="block text-2xl font-extrabold leading-none text-[#000E49]">{value}</span>
                <span className="text-xs text-muted-foreground">{label}</span>
              </span>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search name, phone, order #, provider id…"
            className="w-72 pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Select value={disposition} onValueChange={setDisposition}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="All dispositions" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All dispositions</SelectItem>
            {DISPOSITIONS.map((d) => (
              <SelectItem key={d} value={d}>{d}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground">{sessions.length} transactions</span>
      </div>

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <div className="card-clean overflow-x-auto">
        <Table className="min-w-[760px]">
          <TableHeader>
            <TableRow>
              <TableHead>Transaction</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>Provider ID</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Orders</TableHead>
              <TableHead>Duration</TableHead>
              <TableHead>When</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paged.map((s) => (
              <TableRow key={s.key} className="cursor-pointer hover:bg-[#F5F8FD]" onClick={() => openSession(s)}>
                <TableCell className="max-w-[150px] truncate font-mono text-xs font-semibold" title={s.call?.call_id || s.orders[0]?.order_number || s.key}>
                  {s.call ? s.call.call_id.slice(0, 18) : s.orders[0]?.order_number || "—"}
                  {s.call?.run_id ? <div className="font-normal text-muted-foreground">run #{s.call.run_id}</div> : null}
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-1.5 text-sm">
                    <User className="h-3.5 w-3.5 text-muted-foreground" />
                    {s.call?.customer_name || s.call?.first_name || s.orders[0]?.customer_name || "—"}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {s.call?.customer_phone || s.orders[0]?.customer_phone || "—"}
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className="text-[11px]">
                    {s.call?.provider || s.orders[0]?.provider || "dograh"}
                  </Badge>
                </TableCell>
                <TableCell className="max-w-[140px] truncate font-mono text-[11px]" title={effectiveProviderId(s.call, s.orders)}>
                  {effectiveProviderId(s.call, s.orders) || "—"}
                </TableCell>
                <TableCell>
                  <div className="flex flex-col items-start gap-1">
                    {s.call ? (
                      <>
                        <TypeBadge type={s.call.call_type} />
                        <DispositionBadge disposition={s.call.disposition} />
                      </>
                    ) : (
                      s.orders.map((o) => (
                        <Badge key={o.order_number} variant="secondary" className={`${statusClass(o.status)} hover:${statusClass(o.status)}`}>
                          {o.status.replace("_", " ")}
                        </Badge>
                      ))
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  {s.orders.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {s.orders.map((o) => (
                        <Badge key={o.order_number} variant="outline" className="font-mono text-[11px]">
                          {o.order_number}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell>
                  <span className="flex items-center gap-1 text-sm">
                    <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                    {fmtDuration(s.call?.duration_seconds || 0)}
                  </span>
                </TableCell>
                <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                  {s.started_at ? fmtDateTime(s.started_at) : "—"}
                </TableCell>
              </TableRow>
            ))}
            {sessions.length === 0 && !error && (
              <TableRow>
                <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
                  {loading ? "Loading transactions…" : "No transactions yet."}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <Pagination page={safePage} totalPages={totalPages} onChange={setPage} />

      <Dialog open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-h-[90vh] w-[calc(100vw-2rem)] max-w-[calc(100vw-2rem)] overflow-y-auto overflow-x-hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden sm:w-[90vw] sm:max-w-[90vw]">
          {selected && (
            <div className="grid min-w-0 gap-4">
              <DialogHeader className="min-w-0">
                <DialogTitle className="flex min-w-0 flex-wrap items-center gap-2 pr-10">
                  <PhoneCall className="h-4 w-4 text-[#0068FF]" />
                  <span className="break-all font-mono text-sm">
                    {selected.call?.call_id || selected.orders[0]?.order_number || "Transaction"}
                  </span>
                  {selected.call && (
                    <>
                      <TypeBadge type={selected.call.call_type} />
                      <DispositionBadge disposition={selected.call.disposition} />
                    </>
                  )}
                </DialogTitle>
              </DialogHeader>

              {detailLoading && (
                <div className="text-sm text-muted-foreground">Loading full transaction…</div>
              )}

              {/* provider chain: order → run → provider */}
              {chain && (
                <div className="grid grid-cols-1 gap-2 rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3 text-sm sm:grid-cols-2">
                  <div className="min-w-0">
                    <div className="text-xs text-muted-foreground">Provider</div>
                    <div className="font-medium">{chain.provider}</div>
                  </div>
                  <div className="min-w-0">
                    <div className="text-xs text-muted-foreground">Run</div>
                    <div className="font-mono text-xs">{chain.runId ? `#${chain.runId}` : (chain.callId && /^\d+$/.test(chain.callId) ? `#${chain.callId}` : "—")}</div>
                  </div>
                  <div className="min-w-0 sm:col-span-2">
                    <div className="text-xs text-muted-foreground">Provider ID</div>
                    <div className="break-all font-mono text-xs">{chain.providerCallId || "—"}</div>
                  </div>
                  <div className="min-w-0">
                    <div className="text-xs text-muted-foreground">Genesys conversation</div>
                    <div className="break-all font-mono text-xs">{chain.genesys || "—"}</div>
                  </div>
                  <div className="min-w-0">
                    <div className="text-xs text-muted-foreground">Duration</div>
                    <div className="text-xs">{fmtDuration(selected.call?.duration_seconds || 0)}</div>
                  </div>
                </div>
              )}

              {callDetail?.summary ? (
                <div className="rounded-xl bg-[#EAF1FF] p-3 text-sm">{callDetail.summary}</div>
              ) : selected.call?.summary ? (
                <div className="rounded-xl bg-[#EAF1FF] p-3 text-sm">{selected.call.summary}</div>
              ) : null}

              <div className="grid min-w-0 gap-6 xl:grid-cols-3">
              <div className="grid min-w-0 max-w-full content-start gap-4 overflow-x-clip">
                <div className="text-xs font-semibold tracking-wide text-muted-foreground">CUSTOMER & ORDERS</div>

                <div className="rounded-xl border border-[#E2E8F4] p-3">
                  <div className="flex items-center gap-1.5 text-sm font-medium">
                    <User className="h-3.5 w-3.5 text-muted-foreground" />
                    {callDetail?.customer_name || selected.call?.customer_name || selected.orders[0]?.customer_name || "Unknown"}
                  </div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {callDetail?.customer_phone || selected.call?.customer_phone || selected.orders[0]?.customer_phone || "—"}
                    {(callDetail?.first_name || selected.call?.first_name) && (
                      <> · {callDetail?.first_name || selected.call?.first_name}</>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {fmtDuration(selected.call?.duration_seconds || 0)}
                    {selected.started_at && <> · {fmtDateTime(selected.started_at)}</>}
                  </div>
                </div>

              {/* linked orders with items + timeline */}
              {(orderDetails.length > 0 ? orderDetails : selected.orders).map((o) => (
                <div key={o.order_number} className="rounded-xl border border-[#E2E8F4] p-3">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-semibold">{o.order_number}</span>
                    <Badge variant="secondary" className={`${statusClass(o.status)} hover:${statusClass(o.status)}`}>
                      {o.status.replace("_", " ")}
                    </Badge>
                    <span className="ml-auto text-sm font-bold">{fmtMoney(o.total)}</span>
                  </div>
                  <div className="mb-2 text-sm text-muted-foreground">
                    {o.customer_name || "—"} · {o.customer_phone || "—"} · <span className="capitalize">{o.order_type}</span>
                  </div>
                  {"items" in o && (
                    <div className="min-w-0 overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Item</TableHead>
                          <TableHead>Qty</TableHead>
                          <TableHead className="text-right">Price</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(o as OrderDetail).items.map((i, idx) => (
                          <TableRow key={idx}>
                            <TableCell className="text-sm">
                              {i.name}
                              {i.size ? <span className="text-muted-foreground"> ({i.size})</span> : null}
                            </TableCell>
                            <TableCell className="text-sm">{i.quantity}</TableCell>
                            <TableCell className="text-right text-sm">{fmtMoney(Number(i.unit_price) * i.quantity)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                    </div>
                  )}
                  {"events" in o && (o as OrderDetail).events.length > 0 && (
                    <ol className="mt-2 space-y-1 border-l border-l-[#E2E8F4] pl-4">
                      {(o as OrderDetail).events.map((e, idx) => (
                        <li key={idx} className="relative text-xs">
                          <span className="absolute -left-[21px] top-1 h-2 w-2 rounded-full bg-primary" />
                          <span className="font-medium capitalize">{e.status.replace("_", " ")}</span>
                          <span className="ml-2 text-muted-foreground">{fmtDateTime(e.created_at)}</span>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              ))}

              </div>
              <div className="grid min-w-0 max-w-full content-start gap-4 overflow-x-clip xl:border-l xl:border-[#E2E8F4] xl:pl-6">
                <div className="text-xs font-semibold tracking-wide text-muted-foreground">RECORDING & CONVERSATION</div>
              {/* single recording + chat + tool calls */}
              <div className="min-w-0 space-y-2">
                {recordingSrc ? (
                  <div className="rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                    <div className="mb-1 flex flex-wrap items-center justify-between gap-1 text-xs font-semibold text-muted-foreground">
                      <span className="flex items-center gap-1"><Volume2 className="h-3.5 w-3.5" /> Recording</span>
                      <a
                        href={proxiedFileUrl(recordingSrc, `recording-${selected.call?.call_id || "call"}.wav`, true)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <Button size="sm" variant="outline"><Download className="mr-1 h-3 w-3" /> Download</Button>
                      </a>
                    </div>
                    <audio controls className="w-full min-w-0" src={proxiedFileUrl(recordingSrc)} />
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground">No recording available.</div>
                )}
                <div className="min-w-0">
                  <div className="mb-1 flex flex-wrap items-center justify-between gap-1 text-xs font-semibold text-muted-foreground">
                    <span className="flex items-center gap-1">
                      Conversation
                      {turns.length > 0 && <Badge variant="outline" className="ml-1">{turns.length} turns</Badge>}
                    </span>
                    {turns.length > 0 && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          downloadTextFile(
                            `transcript-${selected.call?.call_id || "call"}.txt`,
                            turnsToText(turns)
                          )
                        }
                      >
                        <Download className="mr-1 h-3 w-3" /> Download transcript
                      </Button>
                    )}
                  </div>
                  {detailLoading && turns.length === 0 ? (
                    <div className="text-sm text-muted-foreground">Loading conversation…</div>
                  ) : (
                    <ChatTranscript turns={turns} />
                  )}
                </div>
                {allTools.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs font-semibold text-muted-foreground">Tool calls</div>
                    <div className="space-y-1">
                      {allTools.map((t, i) => (
                        <details key={i} className="group rounded-lg bg-muted/60 px-3 py-1.5">
                          <summary className="flex min-w-0 cursor-pointer list-none items-center gap-2 font-mono text-xs [&::-webkit-details-marker]:hidden">
                            <span className="inline-block shrink-0 transition-transform group-open:rotate-90">▸</span>
                            <Badge variant="outline" className="shrink-0">{t.name}</Badge>
                            <span className="min-w-0 flex-1 truncate text-muted-foreground">{t.args.slice(0, 120)}</span>
                          </summary>
                          {t.args && (
                            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-muted-foreground">{t.args}</pre>
                          )}
                          {t.result && (
                            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words border-t border-[#E2E8F4] pt-1 font-mono text-[11px]">{t.result}</pre>
                          )}
                        </details>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              </div>
              <div className="grid min-w-0 max-w-full content-start gap-4 overflow-x-clip xl:border-l xl:border-[#E2E8F4] xl:pl-6">
                <div className="text-xs font-semibold tracking-wide text-muted-foreground">CONTEXT</div>
                <div className="rounded-xl border border-[#E2E8F4] p-3">
                  <div className="mb-1 text-xs font-semibold text-muted-foreground">Initial context</div>
                  {Object.keys(initialContext).length > 0 ? (
                    <pre className="max-h-72 overflow-auto break-words whitespace-pre-wrap font-mono text-xs">
                      {JSON.stringify(initialContext, null, 2)}
                    </pre>
                  ) : (
                    <div className="text-xs text-muted-foreground">No initial context.</div>
                  )}
                </div>
                <div className="rounded-xl border border-[#E2E8F4] p-3">
                  <div className="mb-1 text-xs font-semibold text-muted-foreground">Gathered context</div>
                  {Object.keys(gatheredContext).length > 0 ? (
                    <pre className="max-h-72 overflow-auto break-words whitespace-pre-wrap font-mono text-xs">
                      {JSON.stringify(gatheredContext, null, 2)}
                    </pre>
                  ) : (
                    <div className="text-xs text-muted-foreground">No gathered context.</div>
                  )}
                </div>
              </div>
              </div>

              <div className="sticky bottom-0 flex justify-end gap-2 border-t border-[#E2E8F4] bg-background py-3">
                <Button variant="outline" onClick={() => setSelected(null)}>Close</Button>
                {selected.call && (
                  <Button variant="outline" onClick={openTrace}>
                    <ExternalLink className="mr-2 h-4 w-4" /> View trace
                  </Button>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
