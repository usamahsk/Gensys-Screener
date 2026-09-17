"use client";

import { useCallback, useEffect, useState, useMemo } from "react";
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
import { apiClient, fmtDateTime, fmtDuration, API_URL, type DograhRun } from "@/lib/api";
import { ChatTranscript, parseRawTranscript } from "@/components/chat-transcript";
import { PageHeader } from "@/components/page-header";
import { toast } from "sonner";
import { RefreshCw, Import, Cloud, Loader2, ExternalLink, Clock, CircleDollarSign } from "lucide-react";
import { DispositionBadge } from "@/app/page";

export default function RunsPage() {
  const [runs, setRuns] = useState<DograhRun[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<DograhRun | null>(null);
  const [transcript, setTranscript] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (p = page) => {
    setLoading(true);
    try {
      const data = await apiClient.dograhRuns(p, PAGE_SIZE);
      const q = search.trim().toLowerCase();
      setRuns(
        q
          ? data.runs.filter(
              (r) =>
                r.phone_number?.includes(q) ||
                r.workflow_name?.toLowerCase().includes(q) ||
                (r.disposition || "").toLowerCase().includes(q)
            )
          : data.runs
      );
      setTotal(data.total_count);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load runs");
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  useEffect(() => {
    load();
  }, [load]);

  // deep link: /runs?run=<id>&workflow=<id> auto-opens the run detail
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const runId = Number(params.get("run"));
    const workflowId = Number(params.get("workflow"));
    if (!runId || !workflowId) return;
    (async () => {
      try {
        const data = await apiClient.dograhRuns(1, 100);
        const match = data.runs.find((r) => r.run_id === runId) || {
          run_id: runId, workflow_id: workflowId, workflow_name: null, name: null,
          created_at: "", duration_seconds: 0, disposition: null, call_type: null,
          phone_number: null, charge_usd: null, transcript_public_url: null,
          recording_public_url: null, gathered_context: null,
        };
        await openRun(match);
      } catch {
        /* ignore */
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openRun = async (r: DograhRun) => {
    setSelected(r);
    setTranscript("");
    try {
      const [t, detail] = await Promise.all([
        apiClient.dograhRunTranscript(r.run_id, r.workflow_id),
        apiClient.dograhRunDetail(r.run_id, r.workflow_id),
      ]);
      setTranscript(t.transcript);
      // refresh public URLs (detail mints them on demand) for playback
      const rec = detail.recording_public_url || detail.recording_url;
      setSelected({
        ...r,
        recording_public_url: typeof rec === "string" ? rec : r.recording_public_url,
        gathered_context: (detail.gathered_context as Record<string, unknown>) || r.gathered_context,
      });
    } catch {
      try {
        const t = await apiClient.dograhRunTranscript(r.run_id, r.workflow_id);
        setTranscript(t.transcript);
      } catch {
        setTranscript("");
      }
    }
  };

  const importRun = async (r: DograhRun) => {
    try {
      const res = await apiClient.importDograhRun(r.run_id, r.workflow_id);
      if (res.status === "duplicate") toast.info("Already imported");
      else toast.success(`Imported ${res.call_id} (${res.disposition})`);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Import failed");
    }
  };

  const openTrace = async (r: DograhRun) => {
    try {
      const { url } = await apiClient.langfuseTraceUrl({ run_id: r.run_id, workflow_id: r.workflow_id });
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not build trace link (check Langfuse settings)");
    }
  };

  const gc = selected?.gathered_context || {};

  const stats = useMemo(() => {
    const talk = runs.reduce((s, r) => s + (r.duration_seconds || 0), 0);
    const cost = runs.reduce((s, r) => s + (r.charge_usd || 0), 0);
    return [
      { label: "Total runs", value: total || runs.length, icon: Cloud },
      { label: "Talk time", value: fmtDuration(talk), icon: Clock },
      { label: "Cost", value: `$${cost.toFixed(2)}`, icon: CircleDollarSign },
    ];
  }, [runs, total]);

  return (
    <div className="space-y-4">
      <PageHeader
        icon={Cloud}
        title="Runs"
        description="Live calls pulled straight from the voice platform via API key. Import a run to attach it to orders, agent view & analytics."
        actions={
          <div className="flex items-center gap-2">
            <Input placeholder="Search phone, agent, disposition…" className="w-64" value={search}
                   onChange={(e) => setSearch(e.target.value)} />
            <Button variant="outline" size="sm" onClick={() => load()}>
              <RefreshCw className="mr-2 h-4 w-4" /> Refresh
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

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <div className="card-clean overflow-x-auto">
        <Table className="min-w-[640px]">
          <TableHeader>
            <TableRow>
              <TableHead>Run</TableHead>
              <TableHead>Agent</TableHead>
              <TableHead>Caller</TableHead>
              <TableHead>Disposition</TableHead>
              <TableHead>Duration</TableHead>
              <TableHead>Cost</TableHead>
              <TableHead>When</TableHead>
              <TableHead className="w-24"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((r) => (
              <TableRow key={r.run_id} className="cursor-pointer" onClick={() => openRun(r)}>
                <TableCell className="font-mono text-xs">{r.run_id}</TableCell>
                <TableCell className="text-sm">{r.workflow_name || "—"}</TableCell>
                <TableCell className="text-sm">{r.phone_number || "—"}</TableCell>
                <TableCell>
                  {r.disposition ? (
                    <DispositionBadge disposition={r.disposition} />
                  ) : (
                    <Badge variant="secondary">{r.call_type || "unknown"}</Badge>
                  )}
                </TableCell>
                <TableCell className="text-sm">{fmtDuration(r.duration_seconds)}</TableCell>
                <TableCell className="text-sm">{r.charge_usd != null ? `$${r.charge_usd.toFixed(4)}` : "—"}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{fmtDateTime(r.created_at)}</TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button size="sm" variant="outline" onClick={(e) => { e.stopPropagation(); importRun(r); }}>
                      <Import className="mr-1 h-3 w-3" /> Import
                    </Button>
                    <Button size="sm" variant="ghost" title="Open Langfuse trace" onClick={(e) => { e.stopPropagation(); openTrace(r); }}>
                      <ExternalLink className="h-3 w-3" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
            {runs.length === 0 && !error && (
              <TableRow>
                <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
                  {loading ? "Loading runs…" : "No runs yet. Configure the API key in Settings first."}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Pagination
        page={page}
        totalPages={Math.max(1, Math.ceil(total / PAGE_SIZE))}
        onChange={setPage}
      />

      <Dialog open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-h-[90vh] w-[calc(100vw-2rem)] max-w-2xl overflow-y-auto overflow-x-hidden sm:max-w-2xl [&>*]:min-w-0">
          {selected && (
            <div className="grid min-w-0 gap-4">
              <DialogHeader className="min-w-0">
                <DialogTitle className="flex min-w-0 flex-wrap items-center gap-3 pr-10">
                  <span className="shrink-0">Run #{selected.run_id}</span>
                  {selected.disposition && <DispositionBadge disposition={selected.disposition} />}
                </DialogTitle>
              </DialogHeader>
              <div className="grid min-w-0 grid-cols-1 gap-2 text-sm sm:grid-cols-2 sm:gap-3">
                <div className="min-w-0 break-words"><span className="text-muted-foreground">Agent:</span> {selected.workflow_name || "—"}</div>
                <div className="min-w-0 break-words"><span className="text-muted-foreground">Caller:</span> {selected.phone_number || "—"}</div>
                <div className="min-w-0"><span className="text-muted-foreground">Started:</span> {fmtDateTime(selected.created_at)}</div>
                <div className="min-w-0"><span className="text-muted-foreground">Duration:</span> {fmtDuration(selected.duration_seconds)}</div>
              </div>

              <div className="flex min-w-0 flex-col gap-2">
                {selected.recording_public_url && (
                  <audio
                    controls
                    className="w-full min-w-0"
                    src={`${API_URL}/api/integration/dograh/file?url=${encodeURIComponent(selected.recording_public_url)}`}
                  />
                )}
                {selected.recording_public_url && (
                  <a className="text-xs text-blue-600 underline"
                     href={`${API_URL}/api/integration/dograh/file?url=${encodeURIComponent(selected.recording_public_url)}`}
                     target="_blank" rel="noreferrer">
                    <ExternalLink className="mr-1 inline h-3 w-3" /> Open recording
                  </a>
                )}
              </div>

              <details className="group min-w-0 rounded-xl border border-[#E2E8F4] bg-[#F7F9FE]">
                <summary className="cursor-pointer list-none px-3 py-2 text-xs font-semibold text-[#000E49] [&::-webkit-details-marker]:hidden">
                  <span className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>
                  Gathered context
                  <span className="ml-2 font-normal text-muted-foreground">
                    {Object.keys(gc).length} fields — click to expand
                  </span>
                </summary>
                <pre className="max-h-48 min-w-0 overflow-auto border-t border-[#E2E8F4] p-3 font-mono text-xs break-words whitespace-pre-wrap">{JSON.stringify(gc, null, 2)}</pre>
              </details>

              <div className="min-w-0">
                <div className="mb-1 text-xs font-semibold text-muted-foreground">Transcript</div>
                <ChatTranscript turns={parseRawTranscript(transcript)} />
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setSelected(null)}>Close</Button>
                <Button variant="outline" onClick={() => selected && openTrace(selected)}>
                  <ExternalLink className="mr-2 h-4 w-4" /> View trace
                </Button>
                <Button onClick={() => importRun(selected)}>
                  {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Import className="mr-2 h-4 w-4" />}
                  Import run
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
