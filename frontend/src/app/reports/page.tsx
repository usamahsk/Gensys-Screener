"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { PageHeader } from "@/components/page-header";
import { apiClient, type Analytics } from "@/lib/api";
import { FileSpreadsheet, Download, PhoneCall, ShoppingCart, Layers } from "lucide-react";

const REPORTS = [
  {
    id: "full",
    title: "Full business report",
    icon: Layers,
    blurb: "Summary KPIs + daily series + disposition & provider breakdowns, all orders with items, and all calls with telephony IDs. The informative default.",
  },
  {
    id: "orders",
    title: "Sessions report",
    icon: ShoppingCart,
    blurb: "Every session: customer, items, totals, status, provider + telephony / provider call ID and linked call ID.",
  },
  {
    id: "calls",
    title: "Calls report",
    icon: PhoneCall,
    blurb: "Every call: provider, telephony / provider call ID, Genesys conversation ID, run, disposition, duration, cost and summary.",
  },
];

export default function ReportsPage() {
  const [report, setReport] = useState("full");
  const [format, setFormat] = useState("xlsx");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [stats, setStats] = useState<Analytics | null>(null);

  useEffect(() => {
    apiClient.analytics().then(setStats).catch(() => setStats(null));
  }, []);

  const url = apiClient.reportDownloadUrl({
    report,
    format,
    ...(start ? { start_date: start } : {}),
    ...(end ? { end_date: end } : {}),
  });

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <PageHeader
        icon={FileSpreadsheet}
        title="Reports"
        description="Pick a report, optionally bound it by dates, and download it as Excel (multi-sheet) or CSV."
      />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {REPORTS.map(({ id, title, icon: Icon, blurb }) => (
          <button key={id} onClick={() => setReport(id)} className="text-left">
            <Card className={report === id ? "border-[#0068FF] ring-2 ring-[#0068FF]/30" : "card-clean"}>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
                  <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
                    <Icon className="h-4 w-4 text-[#0068FF]" />
                  </span>
                  {title}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-xs text-muted-foreground">{blurb}</p>
              </CardContent>
            </Card>
          </button>
        ))}
      </div>

      <Card className="card-clean">
        <CardHeader>
          <CardTitle className="text-sm text-[#000E49]">Options</CardTitle>
          <CardDescription>
            {stats
              ? `Currently ${stats.total_calls} calls and ${stats.orders_created} orders on record.`
              : "Date range is optional — leave blank for everything."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1">
              <Label>From (optional)</Label>
              <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>To (optional)</Label>
              <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
          </div>
          <div className="flex items-center gap-4">
            <span className="text-sm font-medium">Format:</span>
            {(["xlsx", "csv"] as const).map((f) => (
              <label key={f} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="format"
                  className="accent-[#0068FF]"
                  checked={format === f}
                  onChange={() => setFormat(f)}
                  disabled={f === "csv" && report === "full"}
                />
                {f === "xlsx" ? "Excel (.xlsx)" : "CSV (.csv)"}
              </label>
            ))}
          </div>
          {format === "csv" && report === "full" ? (
            <p className="text-xs text-amber-700">CSV fits one sheet — pick Orders or Calls, or switch back to Excel for the full report.</p>
          ) : null}
          <div className="flex justify-end">
            <a href={format === "csv" && report === "full" ? undefined : url} onClick={(e) => { if (format === "csv" && report === "full") e.preventDefault(); }}>
              <Button disabled={format === "csv" && report === "full"}>
                <Download className="mr-2 h-4 w-4" />
                Download {report} .{format}
              </Button>
            </a>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
