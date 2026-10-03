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
import { ChatTranscript, normalizeTurns, parseRawTranscript } from "@/components/chat-transcript";
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

const PURPOSES = ["order_creation", "order_status", "reservation", "general"];

const STATUSES = ["confirmed", "preparing", "ready", "in_transit", "completed", "cancelled", "incomplete"];

function statusClass(s: string) {
  const map: Record<string, string> = {
    confirmed: "bg-blue-100 text-blue-800",
    preparing: "bg-amber-100 text-amber-800",
    ready: "bg-purple-100 text-purple-800",
    in_transit: "bg-cyan-100 text-cyan-800",
    completed: "bg-green-100 text-green-800",
    cancelled: "bg-red-100 text-red-800",
    incomplete: "bg-orange-100 text-orange-800",
  };
  return map[s] || "bg-gray-100 text-gray-700";
}

function CallPurposeBadge({ type, purpose }: { type: string; purpose?: string }) {
  // Single source of truth for "what was this call for".
  // Purpose (classifier/tool tag) wins; legacy call_type is the fallback so
  // old rows without a purpose still show the right chip.
  const p = (purpose || "").trim();
  const t = (type || "").trim();
  const kind =
    p === "reservation" || t === "reservation"
      ? "reservation"
      : p === "order_status" || t === "order_status"
        ? "order_status"
        : p === "order_creation" || t === "create_order"
          ? "order_creation"
          : p === "general"
            ? "general"
            : "call";
  if (kind === "reservation") {
    return (
      <Badge variant="secondary" className="bg-violet-100 text-violet-900 hover:bg-violet-100">
        <FileText className="mr-1 h-3 w-3" /> reservation
      </Badge>
    );
  }
  if (kind === "order_creation") {
    return (
      <Badge className="bg-[#0068FF] text-white hover:bg-[#0057DC]">
        <ShoppingCart className="mr-1 h-3 w-3" /> create order
      </Badge>
    );
  }
  if (kind === "order_status") {
    return (
      <Badge variant="secondary" className="bg-cyan-100 text-cyan-900 hover:bg-cyan-100">
        <FileText className="mr-1 h-3 w-3" /> order status
      </Badge>
    );
  }
  if (kind === "general") {
    return (
      <Badge variant="secondary" className="bg-gray-100 text-gray-700 hover:bg-gray-100">
        <PhoneCall className="mr-1 h-3 w-3" /> general
      </Badge>
    );
  }
  return (
    <Badge variant="outline">
      <PhoneCall className="mr-1 h-3 w-3" /> {t || "call"}
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
  // Provider ID IS the Genesys conversation id (never the session id).
  // Never the numeric Dograh run id — that lives in the Run field. Use the
  // "Backfill provider IDs" button to pull context ids into old rows.
  if (call?.genesys_conversation_id) return call.genesys_conversation_id;
  const direct = call?.provider_call_id || orders[0]?.provider_call_id || "";
  if (direct) return direct;
  const cid = (call?.call_id || "").trim();
  if (cid && !cid.startsWith("voice_") && isProviderSid(cid)) return cid;
  return "";
}

export default function TransactionsPage() {
  const [view, setView] = useState<"orders" | "calls">("orders");
  // Transactions tab = orders only (order + its details).
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<OrderDetail | null>(null);
  const [orderLoading, setOrderLoading] = useState(false);
  // Call Logs tab = call logs with full mapped detail.
  const [callLogs, setCallLogs] = useState<import("@/lib/api").CallRow[]>([]);
  const [search, setSearch] = useState("");
  const [disposition, setDisposition] = useState("all");
  const [purpose, setPurpose] = useState("all");
  const [orderStatus, setOrderStatus] = useState("all");
  const [providerOnly, setProviderOnly] = useState(false);
  const [runOnly, setRunOnly] = useState(false);
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
      if (view === "calls") {
        const rows = await apiClient.calls({
          disposition: disposition === "all" ? undefined : disposition,
          purpose: purpose === "all" ? undefined : purpose,
          search: search.trim() || undefined,
          has_provider_id: providerOnly || undefined,
          has_run: runOnly || undefined,
        });
        setCallLogs(rows);
        setError("");
      } else {
        const rows = await apiClient.orders({
          status: orderStatus === "all" ? undefined : orderStatus,
          search: search.trim() || undefined,
        });
        setOrders(rows);
        setError("");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load transactions");
    } finally {
      setLoading(false);
    }
  }, [view, disposition, purpose, orderStatus, search, providerOnly, runOnly]);

  useEffect(() => {
    const t = setTimeout(load, search ? 400 : 0);
    return () => clearTimeout(t);
  }, [load]);

  useEffect(() => {
    setPage(1);
  }, [view, disposition, purpose, orderStatus, search, providerOnly, runOnly]);

  const backfill = async () => {
    setBackfilling(true);
    try {
      const [r, p] = await Promise.all([
        apiClient.backfillProviderIds(),
        apiClient.backfillPurposes(),
      ]);
      const bits = [
        `${r.updated_calls} calls`,
        `${r.updated_orders} orders`,
      ];
      if (r.updated_customers) bits.push(`${r.updated_customers} customer details`);
      if (p.updated) bits.push(`${p.updated} call purposes`);
      toast.success(`Backfilled ${bits.join(", ")}`);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Backfill failed");
    } finally {
      setBackfilling(false);
    }
  };

  const rows = view === "calls" ? callLogs : orders;
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pagedOrders = (orders as OrderRow[]).slice(
    (safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);
  const pagedCalls = (callLogs as import("@/lib/api").CallRow[]).slice(
    (safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  // Open one order (Transactions tab): order + its details only.
  const openOrder = async (orderNumber: string) => {
    setOrderLoading(true);
    try {
      setSelectedOrder(await apiClient.orderDetail(orderNumber));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not load order");
    } finally {
      setOrderLoading(false);
    }
  };

  // From an order's linked run → switch to Call Logs and open that run's
  // call with intent, provider, tools, initial/gathered context and all.
  const jumpToLinkedCall = async (callId: string) => {
    if (!callId.trim()) {
      toast.error("This order has no linked call yet");
      return;
    }
    setSelectedOrder(null);
    setView("calls");
    setDetailLoading(true);
    try {
      const detail = await apiClient.callDetail(callId.trim());
      const pseudo: TransactionRow = {
        key: `call:${detail.call_id}`,
        started_at: detail.started_at,
        call: {
          call_id: detail.call_id,
          call_type: detail.call_type,
          call_purpose: detail.call_purpose,
          disposition: detail.disposition,
          started_at: detail.started_at,
          duration_seconds: detail.duration_seconds,
          customer_name: detail.customer_name,
          customer_phone: detail.customer_phone,
          first_name: detail.first_name,
          rsvp: detail.rsvp,
          summary: detail.summary,
          escalation_reason: detail.escalation_reason,
          order_numbers: detail.order_numbers,
          sentiment: detail.sentiment,
          recording_url: detail.recording_url,
          transcript_url: detail.transcript_url,
          provider: detail.provider,
          provider_call_id: detail.provider_call_id,
          genesys_conversation_id: detail.genesys_conversation_id,
          run_id: detail.run_id,
          workflow_id: detail.workflow_id,
          workflow_name: detail.workflow_name,
          customer_intent: detail.customer_intent,
          initial_context: detail.initial_context,
          gathered_context: detail.gathered_context,
        },
        orders: [],
      };
      setSelected(pseudo);
      setCallDetail(detail);
      setOrderDetails([]);
      setRunEvidence(null);
      const ods: OrderDetail[] = [];
      for (const n of detail.order_numbers || []) {
        try {
          ods.push(await apiClient.orderDetail(n));
        } catch {
          /* summary row is enough */
        }
      }
      setOrderDetails(ods);
      await loadRunEvidence(pseudo);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Linked call not found");
    } finally {
      setDetailLoading(false);
    }
  };

  const openCallLog = async (c: import("@/lib/api").CallRow) => {
    const pseudo: TransactionRow = {
      key: `call:${c.call_id}`,
      started_at: c.started_at,
      call: {
        ...c,
        genesys_conversation_id: c.genesys_conversation_id || "",
        run_id: c.run_id ?? null,
        workflow_id: c.workflow_id ?? null,
        workflow_name: c.workflow_name || "",
      },
      orders: (c.order_numbers || []).map((n) => ({
        order_number: n,
        customer_name: c.customer_name,
        customer_phone: c.customer_phone,
        order_type: "",
        table_number: "",
        pickup_time: "",
        delivery_address: "",
        payment_method: "",
        status: "",
        total: 0,
        source: "",
        item_count: 0,
        created_at: c.started_at,
        provider: c.provider,
        provider_call_id: c.provider_call_id,
        call_id: c.call_id,
      })),
    };
    await openSession(pseudo);
  };

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

  const [recovering, setRecovering] = useState(false);

  // Re-run transcript extraction for the open call — recovers orders the
  // webhook missed (empty tool-call items, empty transcript turns).
  // Needs the Google key + recovery enabled in Settings.
  const retryExtraction = async () => {
    const c = selected?.call;
    if (!c) return;
    setRecovering(true);
    try {
      const r = await apiClient.recoverCallOrder(c.call_id);
      if (r.status === "recovered" && r.order_number) {
        const ost = (r as { order_status?: string }).order_status;
        toast.success(`Recovered order ${r.order_number}${ost ? ` (${ost})` : ""}`);
      } else {
        toast.info(`No order recovered: ${r.reason || r.status}`);
      }
      // Refresh the open dialog + lists so a recovered order shows up.
      try {
        setCallDetail(await apiClient.callDetail(c.call_id));
      } catch {
        /* keep old detail */
      }
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Recovery failed (check Google key + recovery toggle)");
    } finally {
      setRecovering(false);
    }
  };

  const stats = useMemo(() => {
    if (view === "calls") {
      const talk = callLogs.reduce((sum, c) => sum + (c.duration_seconds || 0), 0);
      const withProvider = callLogs.filter((c) => (c.provider_call_id || "").trim() || (c.genesys_conversation_id || "").trim()).length;
      return [
        { label: "Total call logs", value: callLogs.length, icon: PhoneCall },
        { label: "With provider ID", value: withProvider, icon: Layers },
        { label: "Talk time", value: fmtDuration(talk), icon: Clock },
      ];
    }
    const revenue = orders.reduce((sum, o) => sum + (Number(o.total) || 0), 0);
    const items = orders.reduce((sum, o) => sum + (o.item_count || 0), 0);
    return [
      { label: "Total orders", value: orders.length, icon: ShoppingCart },
      { label: "Items ordered", value: items, icon: Layers },
      { label: "Order revenue", value: fmtMoney(revenue), icon: Clock },
    ];
  }, [orders, callLogs, view]);

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
  // normalizeTurns expands blob turns with inline [ts] markers so the
  // bubble count is real turns, not "1 turns".
  const turns = normalizeTurns(
    callDetail && callDetail.transcript.length > 0
      ? callDetail.transcript
      : runEvidence?.transcript
        ? parseRawTranscript(runEvidence.transcript)
        : []
  );
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
  // Context: prefer live Dograh run detail, fall back to the saved Call row
  // snapshots so provider/initial/gathered/intent render even without a key.
  const initialContext = Object.keys(runEvidence?.initialContext || {}).length > 0
    ? runEvidence!.initialContext
    : (callDetail?.initial_context || selected?.call?.initial_context || {});
  const gatheredContext = Object.keys(runEvidence?.gatheredContext || {}).length > 0
    ? runEvidence!.gatheredContext
    : (callDetail?.gathered_context || selected?.call?.gathered_context || {});
  const customerIntent = callDetail?.customer_intent || selected?.call?.customer_intent
    || callDetail?.call_purpose || selected?.call?.call_purpose || "";
  const customerLastIntent = callDetail?.customer_last_intent || "";
  const customerLastOrder = callDetail?.customer_last_order || "";

  return (
    <div className="space-y-4">
      <PageHeader
        icon={ClipboardList}
        title="Transactions & Call Logs"
        description="Transactions = orders only — open one, follow its linked run into Call Logs. Call Logs = full call detail with intent, contexts, recordings, transcripts and tool calls."
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={backfill} disabled={backfilling} title="Fill empty Provider IDs and missing customer details on old rows from run details">
              <Database className={`mr-2 h-4 w-4 ${backfilling ? "animate-pulse" : ""}`} />
              {backfilling ? "Backfilling…" : "Backfill missing data"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                try {
                  const r = await apiClient.repairDispositions();
                  toast.success(`Checked ${r.scanned} transferred calls: kept ${r.kept_transferred}, fixed ${r.fixed_to_completed} to completed`);
                  load();
                } catch (e) {
                  toast.error(e instanceof Error ? e.message : "Repair failed");
                }
              }}
              title="Fix blanket-transfer damage: transferred calls with no reason and no handoff evidence go back to completed; real handoffs keep their reason"
            >
              Fix transfer tags
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
        <div className="inline-flex rounded-lg bg-muted p-1">
          <Button
            size="sm"
            variant={view === "orders" ? "default" : "ghost"}
            onClick={() => setView("orders")}
          >
            <ShoppingCart className="mr-1 h-3.5 w-3.5" /> Transactions
          </Button>
          <Button
            size="sm"
            variant={view === "calls" ? "default" : "ghost"}
            onClick={() => setView("calls")}
          >
            <PhoneCall className="mr-1 h-3.5 w-3.5" /> Call Logs
          </Button>
        </div>
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
        {view === "calls" && (
          <Select value={purpose} onValueChange={setPurpose}>
            <SelectTrigger className="w-48">
              <SelectValue placeholder="All purposes" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All purposes</SelectItem>
              {PURPOSES.map((p) => (
                <SelectItem key={p} value={p}>{p.replace("_", " ")}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {view === "orders" && (
          <Select value={orderStatus} onValueChange={setOrderStatus}>
            <SelectTrigger className="w-48">
              <SelectValue placeholder="All order statuses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All order statuses</SelectItem>
              {STATUSES.map((s) => (
                <SelectItem key={s} value={s}>{s.replace("_", " ")}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {view === "calls" && (
          <>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground" title="Only rows with a provider/telephone id or Genesys conversation id">
              <input type="checkbox" checked={providerOnly} onChange={(e) => setProviderOnly(e.target.checked)} />
              Has provider ID
            </label>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground" title="Only rows linked to a Dograh run ID">
              <input type="checkbox" checked={runOnly} onChange={(e) => setRunOnly(e.target.checked)} />
              Has run ID
            </label>
          </>
        )}
        <span className="text-sm text-muted-foreground">
          {view === "calls" ? `${callLogs.length} call logs` : `${orders.length} orders`}
        </span>
      </div>

      <div className="text-xs text-muted-foreground">
        {view === "orders"
          ? "Transactions = orders only — customer, items, total, status. Open an order, then follow its linked run into Call Logs."
          : "Call Logs = every call with provider, customer intent, initial + gathered context, recordings, transcripts and tool calls."}
      </div>

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      {view === "calls" ? (
        <div className="card-clean overflow-x-auto">
          <Table className="min-w-[860px]">
            <TableHeader>
              <TableRow>
                <TableHead>Call</TableHead>
                <TableHead>Customer</TableHead>
                <TableHead>Intent</TableHead>
                <TableHead>Provider</TableHead>
                <TableHead>Provider ID</TableHead>
                <TableHead>Run</TableHead>
                <TableHead>Duration</TableHead>
                <TableHead>When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pagedCalls.map((c) => (
                <TableRow key={c.call_id} className="cursor-pointer hover:bg-[#F5F8FD]" onClick={() => openCallLog(c)}>
                  <TableCell className="max-w-[150px] truncate font-mono text-xs font-semibold" title={c.call_id}>
                    {c.call_id.slice(0, 18)}
                    <div className="mt-1 flex flex-col items-start gap-1">
                      <CallPurposeBadge type={c.call_type} purpose={c.call_purpose} />
                      <DispositionBadge disposition={c.disposition} />
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1.5 text-sm">
                      <User className="h-3.5 w-3.5 text-muted-foreground" />
                      {c.customer_name || c.first_name || "—"}
                    </div>
                    <div className="text-xs text-muted-foreground">{c.customer_phone || "—"}</div>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary" className="bg-emerald-100 text-emerald-900 hover:bg-emerald-100">
                      {(c.customer_intent || c.call_purpose || "—").replace("_", " ")}
                    </Badge>
                    {(c.order_numbers || []).length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {(c.order_numbers || []).map((n) => (
                          <Badge key={n} variant="outline" className="font-mono text-[11px]">{n}</Badge>
                        ))}
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-[11px]">{c.provider || "dograh"}</Badge>
                  </TableCell>
                  <TableCell className="max-w-[140px] truncate font-mono text-[11px]" title={c.provider_call_id || c.genesys_conversation_id || ""}>
                    {c.provider_call_id || c.genesys_conversation_id || "—"}
                  </TableCell>
                  <TableCell className="font-mono text-[11px]">{c.run_id ? `#${c.run_id}` : "—"}</TableCell>
                  <TableCell>
                    <span className="flex items-center gap-1 text-sm">
                      <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                      {fmtDuration(c.duration_seconds || 0)}
                    </span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {c.started_at ? fmtDateTime(c.started_at) : "—"}
                  </TableCell>
                </TableRow>
              ))}
              {callLogs.length === 0 && !error && (
                <TableRow>
                  <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
                    {loading ? "Loading call logs…" : "No call logs yet."}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div></div>
      )}
      {view === "orders" && (
      <div className="card-clean overflow-x-auto">
        <Table className="min-w-[900px]">
          <TableHeader>
            <TableRow>
              <TableHead>Order</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead className="text-right">Items</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Linked run</TableHead>
              <TableHead>When</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {pagedOrders.map((o) => (
              <TableRow key={o.order_number} className="cursor-pointer hover:bg-[#F5F8FD]" onClick={() => openOrder(o.order_number)}>
                <TableCell className="font-mono text-xs font-semibold" title={o.order_number}>
                  {o.order_number}
                  <div className="font-normal text-muted-foreground">{o.source || "voice_agent"}</div>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-1.5 text-sm">
                    <User className="h-3.5 w-3.5 text-muted-foreground" />
                    {o.customer_name || "—"}
                  </div>
                  <div className="text-xs text-muted-foreground">{o.customer_phone || "—"}</div>
                </TableCell>
                <TableCell className="text-right text-sm font-medium">{o.item_count}</TableCell>
                <TableCell className="text-right text-sm font-bold">{fmtMoney(o.total)}</TableCell>
                <TableCell>
                  <Badge variant="secondary" className={`${statusClass(o.status)} hover:${statusClass(o.status)}`}>
                    {o.status.replace("_", " ")}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs capitalize">{o.order_type || "—"}</TableCell>
                <TableCell>
                  {o.call_id || o.run_id ? (
                    <Button
                      size="sm"
                      variant="outline"
                      className="font-mono text-[11px]"
                      onClick={(e) => {
                        e.stopPropagation();
                        jumpToLinkedCall(o.call_id);
                      }}
                      title={o.provider_call_id || o.genesys_conversation_id || o.call_id}
                    >
                      <ExternalLink className="mr-1 h-3 w-3" />
                      {o.run_id ? `run #${o.run_id}` : o.call_id.slice(0, 12)}
                    </Button>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                  {o.created_at ? fmtDateTime(o.created_at) : "—"}
                </TableCell>
              </TableRow>
            ))}
            {orders.length === 0 && !error && (
              <TableRow>
                <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
                  {loading ? "Loading orders…" : "No orders yet."}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      )}
      <Pagination page={safePage} totalPages={totalPages} onChange={setPage} />

      {/* Order dialog — order + its details only */}
      <Dialog open={!!selectedOrder} onOpenChange={(open) => !open && setSelectedOrder(null)}>
        <DialogContent className="max-h-[90vh] w-[calc(100vw-2rem)] max-w-2xl overflow-y-auto">
          {selectedOrder && (
            <div className="grid gap-4">
              <DialogHeader>
                <DialogTitle className="flex flex-wrap items-center gap-2 pr-10">
                  <ShoppingCart className="h-4 w-4 text-[#0068FF]" />
                  <span className="font-mono text-sm">{selectedOrder.order_number}</span>
                  <Badge variant="secondary" className={`${statusClass(selectedOrder.status)} hover:${statusClass(selectedOrder.status)}`}>
                    {selectedOrder.status.replace("_", " ")}
                  </Badge>
                  <span className="ml-auto text-sm font-bold">{fmtMoney(selectedOrder.total)}</span>
                </DialogTitle>
              </DialogHeader>

              {orderLoading && <div className="text-sm text-muted-foreground">Loading order…</div>}

              <div className="rounded-xl border border-[#E2E8F4] p-3">
                <div className="flex items-center gap-1.5 text-sm font-medium">
                  <User className="h-3.5 w-3.5 text-muted-foreground" />
                  {selectedOrder.customer_name || "Unknown"}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {selectedOrder.customer_phone || "—"}
                  {selectedOrder.email ? ` · ${selectedOrder.email}` : ""}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  <span className="capitalize">{selectedOrder.order_type}</span>
                  {selectedOrder.table_number ? ` · table ${selectedOrder.table_number}` : ""}
                  {selectedOrder.pickup_time ? ` · pickup ${selectedOrder.pickup_time}` : ""}
                  {selectedOrder.payment_method ? ` · ${selectedOrder.payment_method}` : ""}
                </div>
                {selectedOrder.delivery_address && (
                  <div className="mt-1 text-xs text-muted-foreground">Deliver: {selectedOrder.delivery_address}</div>
                )}
              </div>

              <div className="rounded-xl border border-[#E2E8F4] p-3">
                <div className="mb-2 text-xs font-semibold text-muted-foreground">
                  ITEMS ({selectedOrder.items.reduce((s, i) => s + i.quantity, 0)} total)
                </div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Item</TableHead>
                      <TableHead>Qty</TableHead>
                      <TableHead className="text-right">Price</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {selectedOrder.items.map((i, idx) => (
                      <TableRow key={idx}>
                        <TableCell className="text-sm">
                          {i.name}
                          {i.size ? <span className="text-muted-foreground"> ({i.size})</span> : null}
                          {i.notes ? <div className="text-xs text-muted-foreground">{i.notes}</div> : null}
                        </TableCell>
                        <TableCell className="text-sm">{i.quantity}</TableCell>
                        <TableCell className="text-right text-sm">{fmtMoney(Number(i.unit_price) * i.quantity)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              {selectedOrder.events.length > 0 && (
                <div className="rounded-xl border border-[#E2E8F4] p-3">
                  <div className="mb-2 text-xs font-semibold text-muted-foreground">TIMELINE</div>
                  <ol className="space-y-1 border-l border-l-[#E2E8F4] pl-4">
                    {selectedOrder.events.map((e, idx) => (
                      <li key={idx} className="relative text-xs">
                        <span className="absolute -left-[21px] top-1 h-2 w-2 rounded-full bg-primary" />
                        <span className="font-medium capitalize">{e.status.replace("_", " ")}</span>
                        {e.note ? <span className="ml-2 text-muted-foreground">{e.note}</span> : null}
                        <span className="ml-2 text-muted-foreground">{fmtDateTime(e.created_at)}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              <div className="rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                <div className="mb-1 text-xs font-semibold text-muted-foreground">LINKED RUN</div>
                {selectedOrder.call_id || selectedOrder.run_id ? (
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge variant="outline">{selectedOrder.provider || "dograh"}</Badge>
                    {(selectedOrder.provider_call_id || selectedOrder.genesys_conversation_id) && (
                      <span className="break-all font-mono text-[11px]">
                        {selectedOrder.provider_call_id || selectedOrder.genesys_conversation_id}
                      </span>
                    )}
                    {selectedOrder.run_id && (
                      <span className="font-mono text-[11px] text-muted-foreground">run #{selectedOrder.run_id}</span>
                    )}
                    <Button size="sm" onClick={() => jumpToLinkedCall(selectedOrder.call_id)}>
                      <ExternalLink className="mr-1 h-3 w-3" /> Open in Call Logs
                    </Button>
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground">No linked call for this order.</div>
                )}
              </div>

              <div className="sticky bottom-0 flex justify-end gap-2 border-t border-[#E2E8F4] bg-background py-3">
                <Button variant="outline" onClick={() => setSelectedOrder(null)}>Close</Button>
                {(selectedOrder.call_id || selectedOrder.run_id) && (
                  <Button onClick={() => jumpToLinkedCall(selectedOrder.call_id)}>
                    <ExternalLink className="mr-2 h-4 w-4" /> Open linked run
                  </Button>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

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
                      <CallPurposeBadge type={selected.call.call_type} purpose={selected.call.call_purpose} />
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
                  <div className="min-w-0 sm:col-span-2">
                    <div className="text-xs text-muted-foreground">Duration</div>
                    <div className="text-xs">{fmtDuration(selected.call?.duration_seconds || 0)}</div>
                  </div>
                  <div className="min-w-0 sm:col-span-2">
                    <div className="text-xs text-muted-foreground">Customer intent captured</div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      {customerIntent ? (
                        <Badge className="bg-emerald-600 text-white hover:bg-emerald-600">
                          {customerIntent.replace(/_/g, " ")}
                        </Badge>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                      {customerLastIntent && customerLastIntent !== customerIntent && (
                        <Badge variant="outline" className="text-[11px]">
                          memory: {customerLastIntent.replace(/_/g, " ")}
                        </Badge>
                      )}
                      {customerLastOrder && (
                        <Badge variant="outline" className="font-mono text-[11px]">
                          last order {customerLastOrder}
                        </Badge>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {(() => {
                const summaryText = callDetail?.summary || selected.call?.summary || "";
                // Hide the retired transfer placeholder on old rows — the
                // transfer reason already shows as the disposition chip.
                if (!summaryText.trim() || /full conversation summary arrives when the call ends/i.test(summaryText)) {
                  return null;
                }
                return <div className="rounded-xl bg-[#EAF1FF] p-3 text-sm">{summaryText}</div>;
              })()}

              <div className="grid min-w-0 gap-6 xl:grid-cols-3">
              <div className="grid min-w-0 max-w-full content-start gap-4 overflow-x-clip">
                <div className="text-xs font-semibold tracking-wide text-muted-foreground">CUSTOMER & ORDERS</div>

                <div className="min-w-0 rounded-xl border border-[#E2E8F4] p-3">
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
                <div key={o.order_number} className="min-w-0 rounded-xl border border-[#E2E8F4] p-3">
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
                  <div className="min-w-0 rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
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
                <div className="min-w-0 rounded-xl border border-[#E2E8F4] p-3">
                  <div className="mb-1 text-xs font-semibold text-muted-foreground">Provider</div>
                  <div className="flex flex-wrap items-center gap-1.5 text-xs">
                    <Badge variant="outline">{selected.call?.provider || selected.orders[0]?.provider || "dograh"}</Badge>
                    <span className="break-all font-mono text-[11px]">{effectiveProviderId(selected.call, selected.orders) || "—"}</span>
                  </div>
                  {selected.call?.genesys_conversation_id && (
                    <div className="mt-1 break-all font-mono text-[11px] text-muted-foreground">
                      genesys: {selected.call.genesys_conversation_id}
                    </div>
                  )}
                  {selected.call?.run_id && (
                    <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                      dograh run #{selected.call.run_id}
                      {selected.call.workflow_id ? ` · workflow ${selected.call.workflow_id}` : ""}
                    </div>
                  )}
                </div>
                <div className="min-w-0 rounded-xl border border-[#E2E8F4] p-3">
                  <div className="mb-1 text-xs font-semibold text-muted-foreground">Customer intent captured</div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {customerIntent ? (
                      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600">
                        {customerIntent.replace(/_/g, " ")}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">No intent captured.</span>
                    )}
                  </div>
                  {(customerLastIntent || customerLastOrder) && (
                    <div className="mt-1 text-[11px] text-muted-foreground">
                      {customerLastIntent && <>memory intent: {customerLastIntent.replace(/_/g, " ")} · </>}
                      {customerLastOrder && <>last order: <span className="font-mono">{customerLastOrder}</span></>}
                    </div>
                  )}
                </div>
                <div className="min-w-0 rounded-xl border border-[#E2E8F4] p-3">
                  <div className="mb-1 text-xs font-semibold text-muted-foreground">Initial context</div>
                  {Object.keys(initialContext).length > 0 ? (
                    <pre className="max-h-72 overflow-auto break-words whitespace-pre-wrap font-mono text-xs">
                      {JSON.stringify(initialContext, null, 2)}
                    </pre>
                  ) : (
                    <div className="text-xs text-muted-foreground">No initial context.</div>
                  )}
                </div>
                <div className="min-w-0 rounded-xl border border-[#E2E8F4] p-3">
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
                  <Button
                    variant="outline"
                    disabled={recovering}
                    onClick={retryExtraction}
                    title="Re-run transcript extraction for this call (recovers orders the webhook missed)"
                  >
                    <RefreshCw className={`mr-2 h-4 w-4 ${recovering ? "animate-spin" : ""}`} />
                    {recovering ? "Recovering…" : "Retry extraction"}
                  </Button>
                )}
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
