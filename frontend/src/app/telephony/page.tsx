"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader } from "@/components/page-header";
import { apiClient, type TelephonyProviderMeta, type TelephonyUIField } from "@/lib/api";
import { toast } from "sonner";
import {
  Phone,
  Plus,
  RefreshCw,
  Star,
  Trash2,
  Loader2,
  TriangleAlert,
  Pencil,
} from "lucide-react";

type AnyRec = Record<string, unknown>;
const str = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : "");
const num = (v: unknown) => (typeof v === "number" ? v : Number(str(v)) || 0);
const bool = (v: unknown) => v === true;

function asList(body: unknown, keys: string[]): AnyRec[] {
  if (Array.isArray(body)) return body as AnyRec[];
  if (body && typeof body === "object") {
    for (const k of keys) {
      const v = (body as AnyRec)[k];
      if (Array.isArray(v)) return v as AnyRec[];
    }
  }
  return [];
}

const FALLBACK_PROVIDERS = ["twilio", "vonage", "plivo", "telnyx", "vobiz", "exotel", "cloudonix", "ari"];

// Required credential fields per provider when Dograh metadata is unreachable.
const FALLBACK_FIELDS: Record<string, { name: string; label: string; sensitive: boolean; required: boolean }[]> = {
  twilio: [
    { name: "account_sid", label: "Account SID", sensitive: false, required: true },
    { name: "auth_token", label: "Auth Token", sensitive: true, required: true },
  ],
  vonage: [
    { name: "api_key", label: "API Key", sensitive: false, required: true },
    { name: "api_secret", label: "API Secret", sensitive: true, required: true },
    { name: "application_id", label: "Application ID", sensitive: false, required: true },
    { name: "private_key", label: "Private Key", sensitive: true, required: true },
    { name: "signature_secret", label: "Signature Secret", sensitive: true, required: false },
  ],
  plivo: [
    { name: "auth_id", label: "Auth ID", sensitive: false, required: true },
    { name: "auth_token", label: "Auth Token", sensitive: true, required: true },
    { name: "application_id", label: "Application ID", sensitive: false, required: false },
  ],
  telnyx: [
    { name: "api_key", label: "API Key", sensitive: true, required: true },
    { name: "connection_id", label: "Connection ID", sensitive: false, required: false },
    { name: "webhook_public_key", label: "Webhook Public Key", sensitive: false, required: false },
  ],
  vobiz: [
    { name: "auth_id", label: "Auth ID", sensitive: false, required: true },
    { name: "auth_token", label: "Auth Token", sensitive: true, required: true },
    { name: "application_id", label: "Application ID", sensitive: false, required: false },
  ],
  exotel: [
    { name: "account_sid", label: "Account SID", sensitive: false, required: true },
    { name: "api_key", label: "API Key", sensitive: false, required: true },
    { name: "api_token", label: "API Token", sensitive: true, required: true },
    { name: "api_base_url", label: "API Base URL", sensitive: false, required: false },
  ],
  cloudonix: [
    { name: "bearer_token", label: "Bearer Token", sensitive: true, required: true },
    { name: "domain_id", label: "Domain", sensitive: false, required: true },
    { name: "application_name", label: "Application Name", sensitive: false, required: false },
  ],
  ari: [
    { name: "ari_endpoint", label: "ARI Endpoint", sensitive: false, required: true },
    { name: "app_name", label: "App Name", sensitive: false, required: true },
    { name: "app_password", label: "App Password", sensitive: true, required: true },
  ],
};

function fieldsFor(provider: string, meta: TelephonyProviderMeta[]): TelephonyUIField[] {
  const m = meta.find((p) => p.provider === provider);
  if (m && m.fields.length > 0) {
    return m.fields.filter((f) => f.name !== "provider");
  }
  return (FALLBACK_FIELDS[provider] || []).map((f) => ({
    name: f.name, label: f.label, type: "string", required: f.required,
    sensitive: f.sensitive, description: null, placeholder: null,
  }));
}

export default function TelephonyPage() {
  const [providers, setProviders] = useState<TelephonyProviderMeta[]>([]);
  const [configs, setConfigs] = useState<AnyRec[]>([]);
  const [warnings, setWarnings] = useState<AnyRec[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [showCreate, setShowCreate] = useState(false);
  const [cName, setCName] = useState("");
  const [cProvider, setCProvider] = useState("twilio");
  const [cCreds, setCCreds] = useState<Record<string, string>>({});
  const [cDefault, setCDefault] = useState(false);
  const [saving, setSaving] = useState(false);

  const [expanded, setExpanded] = useState<number | null>(null);
  const [numbers, setNumbers] = useState<Record<number, AnyRec[]>>({});
  const [nAddress, setNAddress] = useState("");
  const [nLabel, setNLabel] = useState("");
  const [nWorkflow, setNWorkflow] = useState("");
  const [addingNumber, setAddingNumber] = useState(false);

  const [editing, setEditing] = useState<AnyRec | null>(null);
  const [eName, setEName] = useState("");
  const [eCreds, setECreds] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, c, w] = await Promise.all([
        apiClient.telephonyProviders().catch(() => ({ providers: [] })),
        apiClient.telephonyConfigs(),
        apiClient.telephonyWarnings().catch(() => ({})),
      ]);
      setProviders(p.providers || []);
      setConfigs(asList(c, ["configurations", "items", "data"]));
      setWarnings(asList(w, ["warnings", "items", "data"]));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load telephony configs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const providerNames = useMemo(
    () => (providers.length > 0 ? providers.map((p) => p.provider) : FALLBACK_PROVIDERS),
    [providers]
  );
  const displayName = (p: string) =>
    providers.find((m) => m.provider === p)?.display_name || p;

  const create = async () => {
    if (!cName.trim()) {
      toast.error("Give the configuration a name");
      return;
    }
    const creds: Record<string, string> = {};
    for (const [k, v] of Object.entries(cCreds)) {
      if (v.trim()) creds[k] = v.trim();
    }
    setSaving(true);
    try {
      await apiClient.createTelephonyConfig({
        name: cName.trim(), provider: cProvider, credentials: creds,
        is_default_outbound: cDefault,
      });
      toast.success("Telephony configuration created");
      setShowCreate(false);
      setCName("");
      setCCreds({});
      setCDefault(false);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: number, name: string) => {
    if (!confirm(`Delete telephony configuration "${name}"? Its numbers stop working.`)) return;
    try {
      await apiClient.deleteTelephonyConfig(id);
      toast.success("Configuration deleted");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed");
    }
  };

  const makeDefault = async (id: number) => {
    try {
      await apiClient.setDefaultOutbound(id);
      toast.success("Default outbound configuration updated");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
    }
  };

  const reactivate = async (id: number) => {
    try {
      await apiClient.reactivateTelephonyConfig(id);
      toast.success("Configuration reactivated");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Reactivate failed");
    }
  };

  const saveEdit = async () => {
    if (!editing) return;
    const id = num(editing.id);
    const creds: Record<string, string> = {};
    for (const [k, v] of Object.entries(eCreds)) {
      if (v.trim()) creds[k] = v.trim();
    }
    try {
      await apiClient.updateTelephonyConfig(id, {
        ...(eName.trim() ? { name: eName.trim() } : {}),
        ...(Object.keys(creds).length > 0 ? { credentials: creds } : {}),
      });
      toast.success("Configuration updated (blank credential fields were left untouched)");
      setEditing(null);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
    }
  };

  const toggleNumbers = async (id: number) => {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    try {
      const body = await apiClient.telephonyNumbers(id);
      setNumbers((prev) => ({ ...prev, [id]: asList(body, ["phone_numbers", "items", "data"]) }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not load numbers");
    }
  };

  const refreshNumbers = async (id: number) => {
    const body = await apiClient.telephonyNumbers(id);
    setNumbers((prev) => ({ ...prev, [id]: asList(body, ["phone_numbers", "items", "data"]) }));
  };

  const addNumber = async (id: number) => {
    if (!nAddress.trim()) {
      toast.error("Enter the phone number address (E.164, e.g. +911234567890)");
      return;
    }
    setAddingNumber(true);
    try {
      await apiClient.addTelephonyNumber(id, {
        address: nAddress.trim(),
        ...(nLabel.trim() ? { label: nLabel.trim() } : {}),
        ...(nWorkflow.trim() ? { inbound_workflow_id: Number(nWorkflow) } : {}),
      });
      toast.success("Number attached");
      setNAddress("");
      setNLabel("");
      setNWorkflow("");
      refreshNumbers(id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Add failed");
    } finally {
      setAddingNumber(false);
    }
  };

  const deleteNumber = async (configId: number, nid: number, addr: string) => {
    if (!confirm(`Remove number ${addr}?`)) return;
    try {
      await apiClient.deleteTelephonyNumber(configId, nid);
      toast.success("Number removed");
      refreshNumbers(configId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed");
    }
  };

  const updateNumberField = async (configId: number, nid: number, body: Record<string, unknown>) => {
    try {
      await apiClient.updateTelephonyNumber(configId, nid, body);
      refreshNumbers(configId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
    }
  };

  const setCaller = async (configId: number, nid: number) => {
    try {
      await apiClient.setDefaultCaller(configId, nid);
      toast.success("Default caller ID updated");
      refreshNumbers(configId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <PageHeader
        icon={Phone}
        title="Telephony"
        description="Provider configurations and phone numbers, managed here and saved straight to Dograh — no need to open Dograh."
        actions={
          <div className="flex gap-2">
            <Button size="sm" onClick={() => setShowCreate(true)}>
              <Plus className="mr-2 h-4 w-4" /> Add configuration
            </Button>
            <Button variant="outline" size="sm" onClick={load} disabled={loading}>
              <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
            </Button>
          </div>
        }
      />

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      {warnings.length > 0 && (
        <Card className="border-amber-200 bg-amber-50">
          <CardContent className="space-y-1 p-4 text-sm text-amber-900">
            <div className="flex items-center gap-2 font-semibold">
              <TriangleAlert className="h-4 w-4" /> Dograh warnings
            </div>
            {warnings.map((w, i) => (
              <div key={i} className="text-xs">{str(w.message || w.detail || JSON.stringify(w))}</div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card className="card-clean">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table className="min-w-[680px]">
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-64 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {configs.map((c) => {
                  const id = num(c.id);
                  const provider = str((c.config as AnyRec)?.provider || c.provider || "");
                  const isDefault = bool(c.is_default_outbound);
                  const active = c.is_active !== false && str(c.status || "") !== "inactive";
                  return (
                    <Fragment key={id}>
                      <TableRow>
                        <TableCell>
                          <div className="flex items-center gap-2 text-sm font-medium">
                            {str(c.name) || `Config #${id}`}
                            {isDefault && (
                              <Badge className="bg-amber-100 text-amber-900 hover:bg-amber-100">
                                <Star className="mr-1 h-3 w-3" /> default outbound
                              </Badge>
                            )}
                          </div>
                          <div className="font-mono text-xs text-muted-foreground">id {id}</div>
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{displayName(provider) || "—"}</Badge>
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary" className={active ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-700"}>
                            {active ? "active" : "inactive"}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap justify-end gap-1">
                            <Button size="sm" variant="outline" onClick={() => toggleNumbers(id)}>
                              <Phone className="mr-1 h-3 w-3" /> Numbers
                            </Button>
                            {!isDefault && (
                              <Button size="sm" variant="ghost" title="Set as default outbound" onClick={() => makeDefault(id)}>
                                <Star className="h-3 w-3" />
                              </Button>
                            )}
                            <Button
                              size="sm" variant="ghost" title="Rename / rotate credentials"
                              onClick={() => { setEditing(c); setEName(str(c.name)); setECreds({}); }}
                            >
                              <Pencil className="h-3 w-3" />
                            </Button>
                            {!active && (
                              <Button size="sm" variant="ghost" title="Reactivate" onClick={() => reactivate(id)}>
                                <RefreshCw className="h-3 w-3" />
                              </Button>
                            )}
                            <Button size="sm" variant="ghost" title="Delete" onClick={() => remove(id, str(c.name))}>
                              <Trash2 className="h-4 w-4 text-red-600" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                      {expanded === id && (
                        <TableRow key={`${id}-numbers`}>
                          <TableCell colSpan={4} className="bg-[#F7F9FE]">
                            <NumbersBlock
                              numbers={numbers[id] || []}
                              address={nAddress} setAddress={setNAddress}
                              label={nLabel} setLabel={setNLabel}
                              workflow={nWorkflow} setWorkflow={setNWorkflow}
                              adding={addingNumber}
                              onAdd={() => addNumber(id)}
                              onDelete={(nid, addr) => deleteNumber(id, nid, addr)}
                              onToggleActive={(nid, v) => updateNumberField(id, nid, { is_active: v })}
                              onSetCaller={(nid) => setCaller(id, nid)}
                            />
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
                {configs.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={4} className="py-8 text-center text-sm text-muted-foreground">
                      {loading ? "Loading configurations…" : "No telephony configurations yet — add your Twilio / Vonage / Genesys provider above."}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Dialog open={showCreate} onOpenChange={(o) => !o && setShowCreate(false)}>
        <DialogContent className="max-h-[90vh] w-[calc(100vw-2rem)] max-w-xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Add telephony configuration</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="space-y-1">
              <Label>Name</Label>
              <Input value={cName} onChange={(e) => setCName(e.target.value)} placeholder="e.g. Twilio main account" />
            </div>
            <div className="space-y-1">
              <Label>Provider</Label>
              <select
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
                value={cProvider} onChange={(e) => { setCProvider(e.target.value); setCCreds({}); }}
              >
                {providerNames.map((p) => (
                  <option key={p} value={p}>{displayName(p)}</option>
                ))}
              </select>
            </div>
            {fieldsFor(cProvider, providers).map((f) => (
              <div key={f.name} className="space-y-1">
                <Label>{f.label}{f.required ? " *" : ""}</Label>
                <Input
                  type={f.sensitive ? "password" : "text"}
                  value={cCreds[f.name] || ""}
                  onChange={(e) => setCCreds({ ...cCreds, [f.name]: e.target.value })}
                  placeholder={f.placeholder || f.description || ""}
                />
                {f.description ? <p className="text-xs text-muted-foreground">{f.description}</p> : null}
              </div>
            ))}
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={cDefault} onChange={(e) => setCDefault(e.target.checked)} />
              Use as default outbound configuration
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setShowCreate(false)}>Cancel</Button>
              <Button onClick={create} disabled={saving}>
                {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null} Save to Dograh
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent className="max-h-[90vh] w-[calc(100vw-2rem)] max-w-xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit {str(editing?.name)}</DialogTitle>
          </DialogHeader>
          {editing && (
            <div className="grid gap-4">
              <div className="space-y-1">
                <Label>Name</Label>
                <Input value={eName} onChange={(e) => setEName(e.target.value)} />
              </div>
              <p className="text-xs text-muted-foreground">
                Rotate credentials: only filled fields are sent — blank fields keep their saved values on Dograh.
              </p>
              {fieldsFor(str((editing.config as AnyRec)?.provider || editing.provider || ""), providers).map((f) => (
                <div key={f.name} className="space-y-1">
                  <Label>{f.label}</Label>
                  <Input
                    type={f.sensitive ? "password" : "text"}
                    value={eCreds[f.name] || ""}
                    onChange={(e) => setECreds({ ...eCreds, [f.name]: e.target.value })}
                    placeholder="Leave blank to keep saved value"
                  />
                </div>
              ))}
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setEditing(null)}>Cancel</Button>
                <Button onClick={saveEdit}>Save changes</Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function NumbersBlock({
  numbers, address, setAddress, label, setLabel, workflow, setWorkflow,
  adding, onAdd, onDelete, onToggleActive, onSetCaller,
}: {
  numbers: AnyRec[];
  address: string; setAddress: (v: string) => void;
  label: string; setLabel: (v: string) => void;
  workflow: string; setWorkflow: (v: string) => void;
  adding: boolean;
  onAdd: () => void;
  onDelete: (nid: number, addr: string) => void;
  onToggleActive: (nid: number, v: boolean) => void;
  onSetCaller: (nid: number) => void;
}) {
  return (
    <div className="space-y-3 p-1">
      <div className="text-xs font-semibold text-muted-foreground">PHONE NUMBERS ({numbers.length})</div>
      {numbers.map((n) => {
        const nid = num(n.id);
        return (
          <div key={nid} className="flex flex-wrap items-center gap-2 rounded-xl border border-[#E2E8F4] bg-white p-2.5">
            <div className="min-w-0">
              <div className="font-mono text-sm font-semibold">{str(n.address_normalized || n.address)}</div>
              <div className="text-xs text-muted-foreground">
                {str(n.label) || "no label"}
                {n.inbound_workflow_id ? ` · inbound → workflow #${num(n.inbound_workflow_id)}` : " · no inbound workflow"}
              </div>
            </div>
            <div className="ml-auto flex flex-wrap items-center gap-1">
              {bool(n.is_default_caller_id) ? (
                <Badge className="bg-amber-100 text-amber-900 hover:bg-amber-100">
                  <Star className="mr-1 h-3 w-3" /> default caller
                </Badge>
              ) : (
                <Button size="sm" variant="ghost" title="Set as default caller ID" onClick={() => onSetCaller(nid)}>
                  <Star className="h-3 w-3" />
                </Button>
              )}
              <Button
                size="sm" variant="outline"
                onClick={() => onToggleActive(nid, !(n.is_active !== false))}
              >
                {n.is_active !== false ? "Deactivate" : "Activate"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onDelete(nid, str(n.address))}>
                <Trash2 className="h-3 w-3 text-red-600" />
              </Button>
            </div>
          </div>
        );
      })}
      {numbers.length === 0 && (
        <div className="text-xs text-muted-foreground">No numbers on this configuration yet.</div>
      )}
      <div className="grid grid-cols-1 gap-2 rounded-xl border border-dashed border-[#C4D3EE] p-2.5 sm:grid-cols-[1fr_160px_150px_auto]">
        <Input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="+911234567890" />
        <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label (optional)" />
        <Input value={workflow} onChange={(e) => setWorkflow(e.target.value)} placeholder="Inbound workflow #" />
        <Button size="sm" onClick={onAdd} disabled={adding}>
          {adding ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />} Add
        </Button>
      </div>
    </div>
  );
}
