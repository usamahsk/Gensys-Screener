"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { apiClient } from "@/lib/api";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import type { LucideIcon } from "lucide-react";
import { Brain, Cpu, KeyRound, Layers, Loader2, Mic, RefreshCw, Volume2, Zap } from "lucide-react";

const LLM_PROVIDERS = ["openai", "google", "groq", "openrouter", "azure", "google_vertex", "aws_bedrock"];
const STT_PROVIDERS = ["deepgram", "openai", "assemblyai", "gladia", "speechmatics", "google", "azure_speech", "sarvam"];
const TTS_PROVIDERS = ["elevenlabs", "cartesia", "inworld", "openai", "google", "azure", "sarvam", "camb", "rime", "minimax", "deepgram", "smallest"];
const EMB_PROVIDERS = ["openai", "google", "azure", "aws_bedrock", "google_vertex", "huggingface"];
const RT_PROVIDERS = ["openai_realtime", "grok_realtime", "ultravox_realtime", "google_realtime", "google_vertex_realtime", "azure_realtime"];

type Svc = { provider: string; model: string; api_key: string; base_url: string; voice: string; speed: string };
const blank = (provider = ""): Svc => ({ provider, model: "", api_key: "", base_url: "", voice: "", speed: "1.0" });

function get(obj: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((o, k) => (o as Record<string, unknown>)?.[k], obj);
}
function str(v: unknown): string {
  return typeof v === "string" || typeof v === "number" ? String(v) : "";
}

function ServiceFields({
  title, icon: Icon, value, onChange, providers, showVoice,
}: {
  title: string;
  icon: LucideIcon;
  value: Svc;
  onChange: (v: Svc) => void;
  providers: string[];
  showVoice?: boolean;
}) {
  const set = (k: keyof Svc) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    onChange({ ...value, [k]: e.target.value });
  return (
    <Card className="card-clean">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
            <Icon className="h-4 w-4 text-[#0068FF]" />
          </span>
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label>Provider</Label>
          <select className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
                  value={value.provider} onChange={set("provider")}>
            <option value="">Select…</option>
            {providers.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label>Model</Label>
          <Input value={value.model} onChange={set("model")} placeholder="e.g. gpt-4o, gemini-3.5-flash, nova-2" />
        </div>
        <div className="col-span-2 space-y-1">
          <Label>API key</Label>
          <Input type="password" value={value.api_key} onChange={set("api_key")} placeholder="Leave masked value to keep existing" />
        </div>
        <div className="col-span-2 space-y-1">
          <Label>Base URL (optional)</Label>
          <Input value={value.base_url} onChange={set("base_url")} placeholder="Only for self-hosted/compatible endpoints" />
        </div>
        {showVoice && (
          <>
            <div className="space-y-1">
              <Label>Voice</Label>
              <Input value={value.voice} onChange={set("voice")} placeholder="Voice id / name" />
            </div>
            <div className="space-y-1">
              <Label>Speed</Label>
              <Input type="number" step="0.1" min="0.5" max="2" value={value.speed} onChange={set("speed")} />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default function ModelsPage() {
  const [mode, setMode] = useState<"dograh" | "byok">("byok");
  const [byokMode, setByokMode] = useState<"pipeline" | "realtime">("pipeline");
  const [managed, setManaged] = useState({ api_key: "", voice: "", speed: "1.0" });
  const [llm, setLlm] = useState<Svc>(blank("openai"));
  const [stt, setStt] = useState<Svc>(blank("deepgram"));
  const [tts, setTts] = useState<Svc>(blank("elevenlabs"));
  const [emb, setEmb] = useState<Svc>(blank("openai"));
  const [rt, setRt] = useState<Svc>(blank("openai_realtime"));
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClient.dograhModels();
      const cfg = (data.configuration || {}) as Record<string, unknown>;
      const m = str(get(cfg, "mode"));
      if (m === "dograh" || m === "byok") setMode(m);
      setManaged({
        api_key: str(get(cfg, "dograh.api_key")),
        voice: str(get(cfg, "dograh.voice")),
        speed: str(get(cfg, "dograh.speed") ?? "1.0"),
      });
      const bm = str(get(cfg, "byok.mode"));
      if (bm === "pipeline" || bm === "realtime") setByokMode(bm);
      const pick = (path: string, fallback: string) => ({
        provider: str(get(cfg, `${path}.provider`)) || fallback,
        model: str(get(cfg, `${path}.model`)),
        api_key: str(get(cfg, `${path}.api_key`)),
        base_url: str(get(cfg, `${path}.base_url`)),
        voice: str(get(cfg, `${path}.voice`)),
        speed: str(get(cfg, `${path}.speed`) ?? "1.0"),
      });
      setLlm(pick("byok.pipeline.llm", "openai"));
      setStt(pick("byok.pipeline.stt", "deepgram"));
      setTts(pick("byok.pipeline.tts", "elevenlabs"));
      setEmb(pick("byok.pipeline.embeddings", "openai"));
      setRt(pick("byok.realtime.realtime", "openai_realtime"));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load model configuration");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const svcBody = (s: Svc, withVoice = false) => {
    const body: Record<string, string> = { provider: s.provider, model: s.model, api_key: s.api_key };
    if (s.base_url.trim()) body.base_url = s.base_url.trim();
    if (withVoice) {
      if (s.voice.trim()) body.voice = s.voice.trim();
      if (s.speed) body.speed = s.speed;
    }
    return body;
  };

  const save = async () => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> =
        mode === "dograh"
          ? { version: 2, mode: "dograh", dograh: { api_key: managed.api_key, voice: managed.voice, speed: Number(managed.speed) || 1.0 } }
          : byokMode === "pipeline"
          ? {
              version: 2,
              mode: "byok",
              byok: {
                mode: "pipeline",
                pipeline: {
                  llm: svcBody(llm),
                  stt: svcBody(stt),
                  tts: svcBody(tts, true),
                  embeddings: svcBody(emb),
                },
              },
            }
          : {
              version: 2,
              mode: "byok",
              byok: {
                mode: "realtime",
                realtime: { ...svcBody(rt), is_realtime: true },
                embeddings: svcBody(emb),
              },
            };
      await apiClient.saveDograhModels(payload);
      toast.success("Model configuration saved — applies to the next call");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <PageHeader
        icon={Cpu}
        title="Models (BYOK)"
        description="Bring your own LLM / STT / TTS keys — applies to the next call, no other dashboard needed."
        actions={
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className="mr-2 h-4 w-4" /> Reload
          </Button>
        }
      />

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <Card className="card-clean">
        <CardContent className="flex gap-4 pt-4">
          {(["dograh", "byok"] as const).map((m) => (
            <label key={m} className="flex items-center gap-2 text-sm font-medium">
              <input type="radio" name="mode" className="accent-[#0068FF]" checked={mode === m} onChange={() => setMode(m)} />
              {m === "dograh" ? "Managed" : "BYOK (own keys)"}
            </label>
          ))}
        </CardContent>
      </Card>

      {mode === "dograh" && (
        <Card className="card-clean">
          <CardHeader>          <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
              <KeyRound className="h-4 w-4 text-[#0068FF]" />
            </span>
            Managed inference
          </CardTitle>
            <CardDescription>Single key billed on the voice platform account that owns it.</CardDescription></CardHeader>
          <CardContent className="grid grid-cols-2 gap-3">
            <div className="col-span-2 space-y-1">
              <Label>Service key</Label>
              <Input type="password" value={managed.api_key} onChange={(e) => setManaged({ ...managed, api_key: e.target.value })}
                     placeholder="Leave masked value to keep existing" />
            </div>
            <div className="space-y-1">
              <Label>Voice</Label>
              <Input value={managed.voice} onChange={(e) => setManaged({ ...managed, voice: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label>Speed</Label>
              <Input type="number" step="0.1" min="0.5" max="2" value={managed.speed}
                     onChange={(e) => setManaged({ ...managed, speed: e.target.value })} />
            </div>
          </CardContent>
        </Card>
      )}

      {mode === "byok" && (
        <>
          <Card className="card-clean">
            <CardContent className="flex gap-4 pt-4">
              {(["pipeline", "realtime"] as const).map((m) => (
              <label key={m} className="flex items-center gap-2 text-sm font-medium">
                <input type="radio" name="byokmode" className="accent-[#0068FF]" checked={byokMode === m} onChange={() => setByokMode(m)} />
                  {m === "pipeline" ? "Pipeline (STT → LLM → TTS)" : "Realtime (speech-to-speech)"}
                </label>
              ))}
            </CardContent>
          </Card>

          {byokMode === "pipeline" ? (
            <div className="grid items-start gap-5 md:grid-cols-2">
              <ServiceFields title="LLM — reasoning" icon={Brain} value={llm} onChange={setLlm} providers={LLM_PROVIDERS} />
              <ServiceFields title="Speech-to-Text" icon={Mic} value={stt} onChange={setStt} providers={STT_PROVIDERS} />
              <ServiceFields title="Text-to-Speech" icon={Volume2} value={tts} onChange={setTts} providers={TTS_PROVIDERS} showVoice />
              <ServiceFields title="Embeddings" icon={Layers} value={emb} onChange={setEmb} providers={EMB_PROVIDERS} />
            </div>
          ) : (
            <div className="grid items-start gap-5 md:grid-cols-2">
              <ServiceFields title="Realtime model" icon={Zap} value={rt} onChange={setRt} providers={RT_PROVIDERS} />
              <ServiceFields title="Embeddings" icon={Layers} value={emb} onChange={setEmb} providers={EMB_PROVIDERS} />
            </div>
          )}
        </>
      )}

      <div className="flex justify-end">
        <Button onClick={save} disabled={saving}>
          {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null} Save changes
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        Masked keys (e.g. sk-…ab12) are preserved when saved. Typing a new key replaces it.
        Agent-level model overrides are rare for a single-agent org — the org config above covers your Pizza Bay agent.
      </p>
    </div>
  );
}
