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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiClient, fmtDateTime, fmtDuration, type CallDetail, type CallRow } from "@/lib/api";
import { DispositionBadge } from "@/app/page";
import { PageHeader } from "@/components/page-header";
import { Pagination, PAGE_SIZE } from "@/components/pagination";
import {
  PhoneCall,
  ShoppingCart,
  PackageSearch,
  RefreshCw,
  Search,
  Clock,
  User,
  Mic,
  Bot,
  FileText,
  ExternalLink,
  Volume2,
} from "lucide-react";

const TYPES = ["create_order", "order_status"] as const;

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
        <PackageSearch className="mr-1 h-3 w-3" /> order status
      </Badge>
    );
  }
  return (
    <Badge variant="outline">
      <PhoneCall className="mr-1 h-3 w-3" /> {type || "call"}
    </Badge>
  );
}

export default function CallLogsPage() {
  const [calls, setCalls] = useState<CallRow[]>([]);
  const [search, setSearch] = useState("");
  const [type, setType] = useState("all");
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await apiClient.calls({
        call_type: type === "all" ? undefined : type,
        search: search.trim() || undefined,
      });
      setCalls(rows);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load call logs");
    } finally {
      setLoading(false);
    }
  }, [type, search]);

  useEffect(() => {
    const t = setTimeout(load, search ? 400 : 0);
    return () => clearTimeout(t);
  }, [load]);

  useEffect(() => {
    setPage(1);
  }, [type, search]);

  const totalPages = Math.max(1, Math.ceil(calls.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const paged = calls.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const openDetail = async (callId: string) => {
    try {
      setDetail(await apiClient.callDetail(callId));
    } catch {
      setDetail(null);
    }
  };

  const stats = useMemo(() => {
    const created = calls.filter((c) => c.call_type === "create_order").length;
    const status = calls.filter((c) => c.call_type === "order_status").length;
    return [
      { label: "Total logs", value: calls.length, icon: PhoneCall },
      { label: "Create order", value: created, icon: ShoppingCart },
      { label: "Order status", value: status, icon: PackageSearch },
    ];
  }, [calls]);

  return (
    <div className="space-y-5">
      <PageHeader
        icon={PhoneCall}
        title="Call Logs"
        description={
          <>
            Every <code className="rounded bg-muted px-1">create_order</code> and{" "}
            <code className="rounded bg-muted px-1">order_status</code> tool call is logged here
            with its <code className="rounded bg-muted px-1">call_id</code> — plus webhook calls.
          </>
        }
        actions={
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
          </Button>
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
                <span className="block text-2xl font-extrabold leading-none">{value}</span>
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
            placeholder="Search call id, name, phone…"
            className="w-72 pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Select value={type} onValueChange={setType}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="All types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All types</SelectItem>
            {TYPES.map((t) => (
              <SelectItem key={t} value={t}>
                {t.replace("_", " ")}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground">{calls.length} logs</span>
      </div>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <div className="card-clean overflow-x-auto">
        <Table className="min-w-[640px]">
          <TableHeader>
            <TableRow>
              <TableHead>Call ID</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead>Duration</TableHead>
              <TableHead>Orders</TableHead>
              <TableHead>Media</TableHead>
              <TableHead>When</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paged.map((c) => (
              <TableRow key={c.call_id} className="cursor-pointer hover:bg-[#F5F8FD]" onClick={() => openDetail(c.call_id)}>
                <TableCell className="max-w-[140px] truncate font-mono text-xs font-semibold" title={c.call_id}>
                  {c.call_id}
                </TableCell>
                <TableCell>
                  <div className="flex flex-col items-start gap-1">
                    <TypeBadge type={c.call_type} />
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
                  <span className="flex items-center gap-1 text-sm">
                    <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                    {fmtDuration(c.duration_seconds)}
                  </span>
                </TableCell>
                <TableCell>
                  {c.order_numbers.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {c.order_numbers.map((o) => (
                        <Badge key={o} variant="outline" className="font-mono text-[11px]">
                          {o}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex gap-1.5 text-muted-foreground">
                    {c.recording_url ? <Volume2 className="h-4 w-4 text-[#0044BA]" /> : null}
                    {c.transcript_url ? <FileText className="h-4 w-4 text-[#0044BA]" /> : null}
                    {!c.recording_url && !c.transcript_url && <span className="text-xs">—</span>}
                  </div>
                </TableCell>
                <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                  {fmtDateTime(c.started_at)}
                </TableCell>
              </TableRow>
            ))}
            {calls.length === 0 && !error && (
              <TableRow>
                <TableCell colSpan={7} className="py-10 text-center text-sm text-muted-foreground">
                  {loading ? "Loading call logs…" : "No call logs yet. Place an order or check an order status with a call_id."}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <Pagination page={safePage} totalPages={totalPages} onChange={setPage} />

      <Dialog open={!!detail} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="max-h-[90vh] w-[calc(100vw-2rem)] max-w-2xl overflow-y-auto">
          {detail && (
            <>
              <DialogHeader>
                <DialogTitle className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm">{detail.call_id}</span>
                  <TypeBadge type={detail.call_type} />
                  <DispositionBadge disposition={detail.disposition} />
                </DialogTitle>
              </DialogHeader>

              <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                <div className="rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                  <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                    <User className="h-3.5 w-3.5" /> Customer
                  </div>
                  <div className="font-medium">{detail.customer_name || detail.first_name || "—"}</div>
                  <div className="text-xs text-muted-foreground">{detail.customer_phone || "—"}</div>
                  {detail.first_name && detail.customer_name ? (
                    <div className="text-xs text-muted-foreground">First name: {detail.first_name}</div>
                  ) : null}
                  {detail.rsvp ? <div className="mt-1 text-xs">RSVP: {detail.rsvp}</div> : null}
                </div>
                <div className="rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                  <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                    <Clock className="h-3.5 w-3.5" /> Call
                  </div>
                  <div>{fmtDuration(detail.duration_seconds)} · {fmtDateTime(detail.started_at)}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {detail.order_numbers.map((o) => (
                      <Badge key={o} variant="outline" className="font-mono text-[11px]">
                        {o}
                      </Badge>
                    ))}
                    {detail.order_numbers.length === 0 && (
                      <span className="text-xs text-muted-foreground">No orders linked</span>
                    )}
                  </div>
                </div>
              </div>

              {detail.summary ? (
                <div className="rounded-xl bg-[#EAF1FF] p-3 text-sm">
                  {detail.summary}
                </div>
              ) : null}

              <div className="space-y-2">
                {detail.recording_url && (
                  <div className="rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                    <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                      <Volume2 className="h-3.5 w-3.5" /> Recording
                    </div>
                    <audio controls className="w-full" src={detail.recording_url} />
                  </div>
                )}
                {detail.user_recording_url && (
                  <div className="rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                    <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                      <Mic className="h-3.5 w-3.5" /> Caller audio
                    </div>
                    <audio controls className="w-full" src={detail.user_recording_url} />
                  </div>
                )}
                {detail.bot_recording_url && (
                  <div className="rounded-xl border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                    <div className="mb-1 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
                      <Bot className="h-3.5 w-3.5" /> Agent audio
                    </div>
                    <audio controls className="w-full" src={detail.bot_recording_url} />
                  </div>
                )}
                {detail.transcript_url && (
                  <a
                    className="flex items-center gap-1 text-sm text-blue-700 underline"
                    href={detail.transcript_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <FileText className="h-4 w-4" /> Transcript file <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>

              {detail.tool_calls.length > 0 && (
                <div>
                  <div className="mb-1 text-xs font-semibold text-muted-foreground">Tool calls</div>
                  <div className="space-y-1">
                    {detail.tool_calls.map((t, i) => (
                      <div key={i} className="flex items-center gap-2 rounded-lg bg-muted/60 px-3 py-1.5 font-mono text-xs">
                        <Badge variant="outline">{t.name}</Badge>
                        <span className="truncate text-muted-foreground">{t.args_summary}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
