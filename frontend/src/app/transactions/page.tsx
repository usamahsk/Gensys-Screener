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
  Mic,
  Bot,
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

interface RunEvidence {
  recordingUrl: string | null;
  transcriptUrl: string | null;
  transcript: string;
  gatheredContext: Record<string, unknown>;
  initialContext: Record<string, unknown>;
  runId: number | null;
  workflowId: number | null;
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
  const [downloadingFile, setDownloadingFile] = useState(false);
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
        runId,
        workflowId,
      });
    } catch {
      setRunEvidence(null);
    }
  };

  const downloadRunFile = async (kind: "transcript" | "recording") => {
    // Dograh-style: open a freshly-minted public URL straight in the browser
    // so the file downloads directly to the PC (no backend hop, no egress
    // permission issues on the server). Falls back to the on-screen text for
    // transcripts when the voice file is gone.
    if (!runEvidence?.runId || !runEvidence?.workflowId) {
      toast.error("No run linked to this transaction");
      return;
    }
    setDownloadingFile(true);
    try {
      const detail = await apiClient.dograhRunDetail(runEvidence.runId, runEvidence.workflowId);
      const freshUrl =
        kind === "transcript"
          ? detail.transcript_public_url || detail.transcript_url
          : detail.recording_public_url || detail.recording_url;
      if (typeof freshUrl !== "string" || !freshUrl) {
        throw new Error(`No ${kind} URL on this run`);
      }
      window.open(freshUrl, "_blank", "noopener,noreferrer");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Download failed";
      if (kind === "transcript" && runEvidence?.transcript) {
        // The stored file is gone on the voice platform (expired/purged),
        // but we already have the text on screen — save that instead.
        downloadTextFile(`transcript-run-${runEvidence.runId}.txt`, runEvidence.transcript);
        toast.warning(`Voice file unavailable (${msg}) — saved the on-screen transcript text instead.`);
      } else {
        toast.error(msg);
      }
    } finally {
      setDownloadingFile(false);
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
        <DialogContent className="max-h-[90vh] w-[calc(100vw-2rem)] max-w-[calc(100vw-2rem)] overflow-y-auto overflow-x-hidden sm:w-[60vw] sm:max-w-[60vw]">
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

              <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <div className="grid min-w-0 content-start gap-4">
                <div className="text-xs font-semibold tracking-wide text-muted-foreground">ORDER DETAILS</div>

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
              <div className="grid min-w-0 content-start gap-4 xl:border-l xl:border-[#E2E8F4] xl:pl-6">
                <div className="text-xs font-semibold tracking-wide text-muted-foreground">CALL + RUN DETAILS</div>
              {/* call media + transcript */}
              {callDetail && (
                <div className="space-y-2">
                  {callDetail.recording_url && (
                    <div className="rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                      <div className="mb-1 flex flex-wrap items-center justify-between gap-1 text-xs font-semibold text-muted-foreground">
                        <span className="flex items-center gap-1"><Volume2 className="h-3.5 w-3.5" /> Recording</span>
                        <span className="flex gap-1">
                          <a
                            href={proxiedFileUrl(callDetail.recording_url, `recording-${callDetail.call_id}.wav`, true)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <Button size="sm" variant="outline"><Download className="mr-1 h-3 w-3" /> Download</Button>
                          </a>
                        </span>
                      </div>
                      <audio controls className="w-full min-w-0" src={proxiedFileUrl(callDetail.recording_url)} />
                    </div>
                  )}
                  {callDetail.user_recording_url && (
                    <div className="rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                      <div className="mb-1 flex flex-wrap items-center justify-between gap-1 text-xs font-semibold text-muted-foreground">
                        <span className="flex items-center gap-1"><Mic className="h-3.5 w-3.5" /> Caller audio</span>
                        <a
                          href={proxiedFileUrl(callDetail.user_recording_url, `caller-${callDetail.call_id}.wav`, true)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <Button size="sm" variant="outline"><Download className="mr-1 h-3 w-3" /> Download</Button>
                        </a>
                      </div>
                      <audio controls className="w-full min-w-0" src={proxiedFileUrl(callDetail.user_recording_url)} />
                    </div>
                  )}
                  {callDetail.bot_recording_url && (
                    <div className="rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                      <div className="mb-1 flex flex-wrap items-center justify-between gap-1 text-xs font-semibold text-muted-foreground">
                        <span className="flex items-center gap-1"><Bot className="h-3.5 w-3.5" /> Agent audio</span>
                        <a
                          href={proxiedFileUrl(callDetail.bot_recording_url, `agent-${callDetail.call_id}.wav`, true)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <Button size="sm" variant="outline"><Download className="mr-1 h-3 w-3" /> Download</Button>
                        </a>
                      </div>
                      <audio controls className="w-full min-w-0" src={proxiedFileUrl(callDetail.bot_recording_url)} />
                    </div>
                  )}
                  {callDetail.transcript.length > 0 && (
                    <div className="min-w-0">
                      <div className="mb-1 flex flex-wrap items-center justify-between gap-1 text-xs font-semibold text-muted-foreground">
                        <span>Transcript</span>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            downloadTextFile(
                              `transcript-${callDetail.call_id}.txt`,
                              turnsToText(callDetail.transcript)
                            )
                          }
                        >
                          <Download className="mr-1 h-3 w-3" /> Download transcript
                        </Button>
                      </div>
                      <ChatTranscript turns={callDetail.transcript} />
                    </div>
                  )}
                  {callDetail.tool_calls.length > 0 && (
                    <div>
                      <div className="mb-1 text-xs font-semibold text-muted-foreground">Tool calls</div>
                      <div className="space-y-1">
                        {callDetail.tool_calls.map((t, i) => (
                          <div key={i} className="flex items-center gap-2 rounded-lg bg-muted/60 px-3 py-1.5 font-mono text-xs">
                            <Badge variant="outline">{t.name}</Badge>
                            <span className="truncate text-muted-foreground">{t.args_summary}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* run evidence from the voice platform */}
              {runEvidence && (
                <div className="space-y-2 rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="text-xs font-semibold text-muted-foreground">RUN EVIDENCE — VOICE PLATFORM</div>
                    <div className="flex flex-wrap gap-1">
                      {runEvidence.recordingUrl && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={downloadingFile}
                          onClick={() => downloadRunFile("recording")}
                        >
                          <Download className="mr-1 h-3 w-3" /> Recording
                        </Button>
                      )}
                      {(runEvidence.transcriptUrl || runEvidence.transcript) && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={downloadingFile}
                          onClick={() => downloadRunFile("transcript")}
                        >
                          <Download className="mr-1 h-3 w-3" /> Transcript file
                        </Button>
                      )}
                      {runEvidence.transcript && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            downloadTextFile(
                              `transcript-run-${runEvidence.runId ?? "unknown"}.txt`,
                              runEvidence.transcript
                            )
                          }
                        >
                          <Download className="mr-1 h-3 w-3" /> Download transcript
                        </Button>
                      )}
                      <Button size="sm" variant="outline" onClick={openTrace}>
                        <ExternalLink className="mr-1 h-3 w-3" /> Trace
                      </Button>
                    </div>
                  </div>
                  {runEvidence.recordingUrl && (
                    <audio
                      controls
                      className="w-full min-w-0"
                      src={proxiedFileUrl(runEvidence.recordingUrl)}
                    />
                  )}
                  {runEvidence.transcript ? (
                    <div className="min-w-0">
                      <ChatTranscript turns={parseRawTranscript(runEvidence.transcript)} />
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">No run transcript.</div>
                  )}
                  {Object.keys(runEvidence.initialContext).length > 0 && (
                    <details className="group rounded-xl border border-[#E2E8F4] bg-white">
                      <summary className="cursor-pointer list-none px-3 py-2 text-xs font-semibold text-[#000E49] [&::-webkit-details-marker]:hidden">
                        <span className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>
                        Initial context
                      </summary>
                      <pre className="max-h-48 overflow-auto border-t border-[#E2E8F4] p-3 font-mono text-xs break-words whitespace-pre-wrap">
                        {JSON.stringify(runEvidence.initialContext, null, 2)}
                      </pre>
                    </details>
                  )}
                  {Object.keys(runEvidence.gatheredContext).length > 0 && (
                    <details className="group rounded-xl border border-[#E2E8F4] bg-white">
                      <summary className="cursor-pointer list-none px-3 py-2 text-xs font-semibold text-[#000E49] [&::-webkit-details-marker]:hidden">
                        <span className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>
                        Gathered context
                      </summary>
                      <pre className="max-h-48 overflow-auto border-t border-[#E2E8F4] p-3 font-mono text-xs break-words whitespace-pre-wrap">
                        {JSON.stringify(runEvidence.gatheredContext, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              )}
              </div>
              </div>

              <div className="flex justify-end gap-2">
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
