"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/page-header";
import { apiClient, type KnowledgeDocument } from "@/lib/api";
import { toast } from "sonner";
import { BookOpen, Download, Loader2, RefreshCw, Trash2, UploadCloud } from "lucide-react";

const ACCEPT = ".pdf,.docx,.doc,.txt,.json";

function statusClass(s: string) {
  const map: Record<string, string> = {
    completed: "bg-green-100 text-green-800",
    processing: "bg-amber-100 text-amber-800",
    pending: "bg-blue-100 text-blue-800",
    failed: "bg-red-100 text-red-800",
  };
  return map[s] || "bg-gray-100 text-gray-700";
}

export default function KnowledgeBasePage() {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState("chunked");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClient.knowledgeDocs();
      setDocs(data.documents);
      setTotal(data.total);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load documents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const upload = async () => {
    if (!file) {
      toast.error("Pick a file first (PDF, DOCX, TXT or JSON, max 5 MB)");
      return;
    }
    setUploading(true);
    try {
      const res = await apiClient.uploadKnowledgeDoc(file, mode);
      const doc = (res.document || {}) as Record<string, unknown>;
      toast.success(`Uploaded ${String(doc.filename || file.name)} — processing ${mode === "chunked" ? "with chunked search" : "as a full document"}`);
      setFile(null);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const remove = async (uuid: string, name: string) => {
    if (!confirm(`Delete "${name}" from the Dograh knowledge base?`)) return;
    try {
      await apiClient.deleteKnowledgeDoc(uuid);
      toast.success("Document deleted");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed");
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <PageHeader
        icon={BookOpen}
        title="Knowledge Base"
        description="Upload menus, price lists, policies and FAQs straight to Dograh — the agent retrieves them live during calls."
        actions={
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
          </Button>
        }
      />

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <Card className="card-clean">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
              <UploadCloud className="h-4 w-4 text-[#0068FF]" />
            </span>
            Upload a document
          </CardTitle>
          <CardDescription>
            PDF, DOCX, TXT or JSON up to 5 MB. After upload, attach the document to a
            Start Call / Agent node in your Dograh workflow so the agent can use it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_220px]">
            <div className="space-y-1">
              <Label>File</Label>
              <Input
                type="file"
                accept={ACCEPT}
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
              <p className="text-xs text-muted-foreground">
                {file ? `${file.name} (${(file.size / 1024).toFixed(0)} KB)` : "No file selected."}
              </p>
            </div>
            <div className="space-y-1">
              <Label>Retrieval mode</Label>
              <select
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
                value={mode}
                onChange={(e) => setMode(e.target.value)}
              >
                <option value="chunked">Chunked search</option>
                <option value="full_document">Full document</option>
              </select>
              <p className="text-xs text-muted-foreground">
                {mode === "chunked"
                  ? "Best for policies & manuals (needs an embedding key in Dograh AI Models)."
                  : "Best for menus & price lists (whole doc in context, no embedding key)."}
              </p>
            </div>
          </div>
          <div className="flex justify-end">
            <Button onClick={upload} disabled={uploading || !file}>
              {uploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UploadCloud className="mr-2 h-4 w-4" />}
              Upload & process
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="card-clean">
        <CardHeader>
          <CardTitle className="text-sm text-[#000E49]">
            Documents ({total || docs.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table className="min-w-[640px]">
              <TableHeader>
                <TableRow>
                  <TableHead>File</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead className="text-right">Chunks</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="w-28"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {docs.map((d) => (
                  <TableRow key={d.document_uuid}>
                    <TableCell>
                      <div className="text-sm font-medium">{d.filename}</div>
                      <div className="text-xs text-muted-foreground">
                        {(d.file_size_bytes / 1024).toFixed(0)} KB · {d.mime_type}
                      </div>
                      {d.processing_error ? (
                        <div className="text-xs text-red-600">{d.processing_error}</div>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className={statusClass(d.processing_status)}>
                        {d.processing_status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm">{d.retrieval_mode?.replace("_", " ")}</TableCell>
                    <TableCell className="text-right text-sm">{d.total_chunks}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(d.updated_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <a
                          href={apiClient.knowledgeDocDownloadUrl(d.document_uuid)}
                          download={d.filename}
                          title={`Download ${d.filename} to this machine`}
                        >
                          <Button size="sm" variant="ghost" title="Download">
                            <Download className="h-4 w-4 text-[#0068FF]" />
                          </Button>
                        </a>
                        <Button size="sm" variant="ghost" title="Delete" onClick={() => remove(d.document_uuid, d.filename)}>
                          <Trash2 className="h-4 w-4 text-red-600" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
                {docs.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="py-8 text-center text-sm text-muted-foreground">
                      {loading ? "Loading documents…" : "No documents yet — upload your menu or FAQ above."}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
