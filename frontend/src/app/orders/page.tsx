"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiClient, fmtDateTime, fmtDuration, fmtMoney, type CallDetail, type OrderDetail, type OrderRow } from "@/lib/api";
import { ChatTranscript } from "@/components/chat-transcript";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Pagination, PAGE_SIZE } from "@/components/pagination";
import { CheckCheck, ClipboardList, ExternalLink, ShoppingCart, Wallet } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DispositionBadge } from "@/app/page";

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

export default function OrdersPage() {
  const router = useRouter();
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [status, setStatus] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<OrderDetail | null>(null);
  const [relatedCall, setRelatedCall] = useState<CallDetail | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setOrders(await apiClient.orders({ status: status === "all" ? undefined : status, search }));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load orders");
    }
  }, [status, search]);

  useEffect(() => {
    load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(orders.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const paged = orders.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const openDetail = async (orderNumber: string) => {
    try {
      const order = await apiClient.orderDetail(orderNumber);
      setDetail(order);
      setRelatedCall(null);
      if (order.call_id) {
        try {
          setRelatedCall(await apiClient.callDetail(order.call_id));
        } catch {
          setRelatedCall(null);
        }
      }
    } catch {
      setDetail(null);
      setRelatedCall(null);
    }
  };

  const openRunForOrder = async () => {
    if (relatedCall?.run_id && relatedCall?.workflow_id) {
      router.push(`/runs?run=${relatedCall.run_id}&workflow=${relatedCall.workflow_id}`);
      return;
    }
    const uuid = detail?.call_id;
    if (!uuid) return;
    try {
      const found = await apiClient.findRunByCallId(uuid);
      if (found.found && found.run_id && found.workflow_id) {
        router.push(`/runs?run=${found.run_id}&workflow=${found.workflow_id}`);
      } else {
        toast.info("Run not found on the voice platform (may be archived or from another instance)");
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Run lookup failed");
    }
  };

  const changeStatus = async (orderNumber: string, newStatus: string) => {
    try {
      await apiClient.updateOrderStatus(orderNumber, newStatus);
      toast.success(`${orderNumber} → ${newStatus.replace("_", " ")}`);
      await openDetail(orderNumber);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Status update failed");
    }
  };

  const stats = useMemo(() => {
    const revenue = orders.reduce((s, o) => s + Number(o.total || 0), 0);
    const completed = orders.filter((o) => o.status === "completed").length;
    return [
      { label: "Total orders", value: orders.length, icon: ShoppingCart },
      { label: "Revenue", value: fmtMoney(revenue), icon: Wallet },
      { label: "Completed", value: completed, icon: CheckCheck },
    ];
  }, [orders]);

  return (
    <div className="space-y-4">
      <PageHeader
        icon={ClipboardList}
        title="Orders"
        description="Voice orders with live tracking timelines."
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
        <Input
          placeholder="Search order #, name, phone…"
          className="w-72"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        />
        <Select value={status} onValueChange={(v) => { setStatus(v); setPage(1); }}>
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {STATUSES.map((s) => (
              <SelectItem key={s} value={s}>{s.replace("_", " ")}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground">{orders.length} orders</span>
      </div>

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <div className="card-clean overflow-x-auto">
        <Table className="min-w-[640px]">
          <TableHeader>
            <TableRow>
              <TableHead>Order #</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Items</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Placed</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paged.map((o) => (
              <TableRow key={o.order_number} className="cursor-pointer" onClick={() => openDetail(o.order_number)}>
                <TableCell className="font-mono text-xs font-semibold">{o.order_number}</TableCell>
                <TableCell>
                  <div className="text-sm">{o.customer_name || "—"}</div>
                  <div className="text-xs text-muted-foreground">{o.customer_phone}</div>
                </TableCell>
                <TableCell className="text-sm capitalize">
                  {o.order_type}
                  {o.order_type === "dinein" && o.table_number ? ` · T${o.table_number}` : ""}
                  {o.order_type === "takeaway" && o.pickup_time ? ` · ${o.pickup_time}` : ""}
                </TableCell>
                <TableCell>
                  <Badge variant="secondary" className={`${statusClass(o.status)} hover:${statusClass(o.status)}`}>
                    {o.status.replace("_", " ")}
                  </Badge>
                </TableCell>
                <TableCell className="text-sm">{o.item_count}</TableCell>
                <TableCell className="text-right text-sm font-semibold">{fmtMoney(o.total)}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{fmtDateTime(o.created_at)}</TableCell>
              </TableRow>
            ))}
            {orders.length === 0 && !error && (
              <TableRow>
                <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">No orders found.</TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <Pagination page={safePage} totalPages={totalPages} onChange={setPage} />

      <Dialog open={!!detail} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="max-h-[90vh] w-[calc(100vw-2rem)] max-w-2xl overflow-y-auto overflow-x-hidden">
          {detail && (
            <>
              <DialogHeader>
                <DialogTitle className="flex flex-wrap items-center gap-3">
                  <span className="font-mono">{detail.order_number}</span>
                  <Badge variant="secondary" className={`${statusClass(detail.status)} hover:${statusClass(detail.status)}`}>
                    {detail.status.replace("_", " ")}
                  </Badge>
                  {detail.source === "voice_agent" && <DispositionBadge disposition="completed" />}
                </DialogTitle>
              </DialogHeader>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-muted-foreground">Staff: update status →</span>
                {STATUSES.filter((s) => s !== detail.status).map((s) => (
                  <Button key={s} size="sm" variant="outline"
                          onClick={() => changeStatus(detail.order_number, s)}>
                    {s.replace("_", " ")}
                  </Button>
                ))}
              </div>
              <div className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
                <div>
                  <div className="text-xs text-muted-foreground">Customer</div>
                  <div>{detail.customer_name || "—"}</div>
                  <div className="text-xs text-muted-foreground">{detail.customer_phone}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">Fulfilment</div>
                  <div className="capitalize">{detail.order_type}</div>
                  <div className="text-xs text-muted-foreground">
                    {detail.order_type === "dinein" ? `Table ${detail.table_number}` : detail.pickup_time ? `Pickup ${detail.pickup_time}` : "—"}
                  </div>
                  {detail.delivery_address && (
                    <div className="mt-1 text-xs text-muted-foreground">📍 {detail.delivery_address}</div>
                  )}
                  {detail.payment_method && (
                    <div className="text-xs text-muted-foreground">💳 {detail.payment_method}</div>
                  )}
                </div>
              </div>
              <div className="rounded-md border border-[#E2E8F4]">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Item</TableHead>
                      <TableHead>Qty</TableHead>
                      <TableHead className="text-right">Price</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {detail.items.map((i, idx) => (
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
              <div className="text-right text-sm font-bold">Total: {fmtMoney(detail.total)}</div>
              <div>
                <div className="mb-2 text-xs font-medium text-muted-foreground">Tracking timeline</div>
                <ol className="space-y-2 border-l border-l-[#E2E8F4] pl-4">
                  {detail.events.map((e, idx) => (
                    <li key={idx} className="relative text-sm">
                      <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-primary" />
                      <span className="font-medium capitalize">{e.status.replace("_", " ")}</span>
                      <span className="ml-2 text-xs text-muted-foreground">{fmtDateTime(e.created_at)}</span>
                    </li>
                  ))}
                </ol>
              </div>
              <div className="rounded-md border border-[#E2E8F4] bg-[#F7F9FE] p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs font-semibold text-muted-foreground">
                    RELATED CALL — WHAT WAS TALKED ABOUT
                  </span>
                  {(relatedCall || detail.call_id) && (
                    <Button size="sm" variant="outline" onClick={openRunForOrder}>
                      View in Runs <ExternalLink className="ml-1 h-3 w-3" />
                    </Button>
                  )}
                </div>
                {relatedCall ? (
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span className="font-mono">{relatedCall.call_id.slice(0, 20)}</span>
                      <DispositionBadge disposition={relatedCall.disposition} />
                      <span>{fmtDuration(relatedCall.duration_seconds)}</span>
                    </div>
                    <div className="text-sm">{relatedCall.summary}</div>
                    {relatedCall.transcript.length > 0 && (
                      <ChatTranscript turns={relatedCall.transcript} />
                    )}
                  </div>
                ) : detail.call_id ? (
                  <div className="text-xs text-muted-foreground">Call {detail.call_id.slice(0, 20)} logged, details unavailable.</div>
                ) : (
                  <div className="text-xs text-muted-foreground">
                    No call linked. Wire <code>call_id = {"{{workflow_run_id}}"}</code> as a preset parameter on the create_order tool so future orders link automatically.
                  </div>
                )}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
