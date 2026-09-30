"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { apiClient, type DograhSetting, type GoogleSetting, type LangfuseSetting } from "@/lib/api";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Settings as SettingsIcon, Plug, Loader2, Sparkles, Activity } from "lucide-react";

export default function SettingsPage() {
  const [setting, setSetting] = useState<DograhSetting | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://voice.larynxai.in");
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [google, setGoogle] = useState<GoogleSetting | null>(null);
  const [googleKey, setGoogleKey] = useState("");
  const [googleModel, setGoogleModel] = useState("gemini-3.5-flash");
  const [recoveryEnabled, setRecoveryEnabled] = useState(true);
  const [savingGoogle, setSavingGoogle] = useState(false);
  const [langfuse, setLangfuse] = useState<LangfuseSetting | null>(null);
  const [lfPublic, setLfPublic] = useState("");
  const [lfSecret, setLfSecret] = useState("");
  const [lfHost, setLfHost] = useState("https://cloud.langfuse.com");
  const [lfProject, setLfProject] = useState("");
  const [lfEnabled, setLfEnabled] = useState(true);
  const [savingLf, setSavingLf] = useState(false);

  const load = useCallback(async () => {
    try {
      const s = await apiClient.dograhSetting();
      setSetting(s);
      setBaseUrl(s.base_url);
      setApiKey("");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to load settings");
    }
    try {
      const g = await apiClient.googleSetting();
      setGoogle(g);
      setGoogleModel(g.model);
      setRecoveryEnabled(g.recovery_enabled);
      setGoogleKey("");
    } catch {
      /* google settings table creates itself on first backend hit */
    }
    try {
      const lf = await apiClient.langfuseSetting();
      setLangfuse(lf);
      setLfHost(lf.host || "https://cloud.langfuse.com");
      setLfProject(lf.project_id || "");
      setLfEnabled(lf.enabled);
      setLfPublic("");
      setLfSecret("");
    } catch {
      /* langfuse settings row creates itself on first backend hit */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const body: { api_key?: string; base_url?: string } = { base_url: baseUrl };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const s = await apiClient.saveDograhSetting(body);
      setSetting(s);
      setApiKey("");
      toast.success("Integration settings saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const saveGoogle = async () => {
    setSavingGoogle(true);
    try {
      const body: { api_key?: string; model?: string; recovery_enabled?: boolean } = {
        model: googleModel,
        recovery_enabled: recoveryEnabled,
      };
      if (googleKey.trim()) body.api_key = googleKey.trim();
      const g = await apiClient.saveGoogleSetting(body);
      setGoogle(g);
      setGoogleKey("");
      toast.success("Google LLM settings saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSavingGoogle(false);
    }
  };
  const test = async () => {
    setTesting(true);
    try {
      const res = await apiClient.dograhHealth();
      if (res.ok) toast.success("Connected ✔");
      else toast.error(`Voice platform unreachable (${res.status}): ${res.detail}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Test failed");
    } finally {
      setTesting(false);
    }
  };

  const saveLangfuse = async () => {
    setSavingLf(true);
    try {
      const body: { public_key?: string; secret_key?: string; host?: string; project_id?: string; enabled?: boolean } = {
        host: lfHost.trim() || "https://cloud.langfuse.com",
        project_id: lfProject.trim(),
        enabled: lfEnabled,
      };
      if (lfPublic.trim()) body.public_key = lfPublic.trim();
      if (lfSecret.trim()) body.secret_key = lfSecret.trim();
      const lf = await apiClient.saveLangfuseSetting(body);
      setLangfuse(lf);
      setLfPublic("");
      setLfSecret("");
      if (lf.dograh_pushed) toast.success("Langfuse keys saved + pushed to Dograh ✔");
      else if (lf.dograh_detail) toast.success(`Saved locally. Dograh push: ${lf.dograh_detail}`);
      else toast.success("Langfuse settings saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSavingLf(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader
        icon={SettingsIcon}
        title="Settings"
        description="Connect this dashboard directly to your voice AI instance."
      />

      <div className="grid items-start gap-6 lg:grid-cols-2">

      <Card className="card-clean">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
              <Plug className="h-4 w-4 text-[#0068FF]" />
            </span>
            Voice Platform Integration
          </CardTitle>
          <CardDescription>
            Pull runs, transcripts and recordings straight from the voice platform using an API key.
            Create one at <code className="rounded bg-muted px-1">/api-keys</code> in the voice platform.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <Label>Voice platform base URL</Label>
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://voice.larynxai.in" />
            <p className="text-xs text-muted-foreground">Defaults to your instance: <code>https://voice.larynxai.in</code></p>
          </div>
          <div className="space-y-1">
            <Label>Voice platform API key</Label>
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={setting?.has_key ? `Saved: ${setting.api_key_masked}` : "Enter API key"}
            />
            <p className="text-xs text-muted-foreground">
              {setting?.has_key ? "A key is already saved — leave blank to keep it, or type a new one to replace." : "Paste your voice platform API key."}
            </p>
          </div>
          <div className="flex gap-2">
            <Button onClick={save} disabled={saving}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null} Save
            </Button>
            <Button variant="outline" onClick={test} disabled={testing}>
              {testing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null} Test connection
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="card-clean">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
              <Sparkles className="h-4 w-4 text-[#0068FF]" />
            </span>
            Google LLM — Transcript Order Recovery
          </CardTitle>
          <CardDescription>
            When a post-call webhook arrives without usable order data, the saved transcript is sent
            to Gemini, which returns order JSON in our payload shape to create/update the same order.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <Label>Google API key</Label>
            <Input
              type="password"
              value={googleKey}
              onChange={(e) => setGoogleKey(e.target.value)}
              placeholder={google?.has_key ? `Saved: ${google.api_key_masked}` : "AIza..."}
            />
            <p className="text-xs text-muted-foreground">
              {google?.has_key ? "A key is already saved — leave blank to keep it, or type a new one to replace." : "Paste a Gemini API key (env KFC_GEMINI_API_KEY works as fallback)."}
            </p>
          </div>
          <div className="space-y-1">
            <Label>Model</Label>
            <Input value={googleModel} onChange={(e) => setGoogleModel(e.target.value)} placeholder="gemini-3.5-flash" />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={recoveryEnabled}
              onChange={(e) => setRecoveryEnabled(e.target.checked)}
            />
            Recover orders from transcripts automatically
          </label>
          <div className="flex gap-2">
            <Button onClick={saveGoogle} disabled={savingGoogle}>
              {savingGoogle ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null} Save
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="card-clean lg:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
              <Activity className="h-4 w-4 text-[#0068FF]" />
            </span>
            Langfuse — Trace Observability
          </CardTitle>
          <CardDescription>
            Save your Langfuse key trio once (org-level). It is stored here and pushed to your
            Dograh instance, so every run links straight to its Langfuse trace — no need to open Dograh.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <Label>Public key</Label>
            <Input
              type="password"
              value={lfPublic}
              onChange={(e) => setLfPublic(e.target.value)}
              placeholder={langfuse?.has_keys ? `Saved: ${langfuse.public_key_masked}` : "pk-lf-..."}
            />
          </div>
          <div className="space-y-1">
            <Label>Secret key</Label>
            <Input
              type="password"
              value={lfSecret}
              onChange={(e) => setLfSecret(e.target.value)}
              placeholder={langfuse?.has_keys ? "Saved (hidden) — type a new one to replace" : "sk-lf-..."}
            />
            <p className="text-xs text-muted-foreground">
              {langfuse?.has_keys ? "Keys already saved — leave blank to keep them." : "Paste the Langfuse public + secret keys."}
            </p>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1">
              <Label>Host</Label>
              <Input value={lfHost} onChange={(e) => setLfHost(e.target.value)} placeholder="https://cloud.langfuse.com" />
            </div>
            <div className="space-y-1">
              <Label>Project ID (optional)</Label>
              <Input value={lfProject} onChange={(e) => setLfProject(e.target.value)} placeholder="cm..." />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={lfEnabled} onChange={(e) => setLfEnabled(e.target.checked)} />
            Enable Langfuse trace links
          </label>
          {langfuse?.dograh_detail && (
            <p className="text-xs text-muted-foreground">Last Dograh push: {langfuse.dograh_detail}</p>
          )}
          <div className="flex gap-2">
            <Button onClick={saveLangfuse} disabled={savingLf}>
              {savingLf ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null} Save + push to Dograh
            </Button>
          </div>
        </CardContent>
      </Card>
      </div>
    </div>
  );
}
