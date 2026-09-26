"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/page-header";
import { apiClient } from "@/lib/api";
import {
  Blocks,
  Phone,
  Sparkles,
  Activity,
  Cpu,
  BookOpen,
  ArrowUpRight,
  RefreshCw,
  CheckCircle2,
  CircleDashed,
} from "lucide-react";

interface Entry {
  key: string;
  title: string;
  blurb: string;
  icon: typeof Phone;
  href: string;
  action: string;
  status: "on" | "off" | "unknown";
  statusText: string;
}

export default function IntegrationsPage() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const list: Entry[] = [];
    try {
      const d = await apiClient.dograhSetting();
      list.push({
        key: "dograh", title: "Voice Platform (Dograh)", icon: Phone, href: "/settings",
        action: "Configure",
        blurb: "API key + base URL powering runs, knowledge base, telephony and model configs.",
        status: d.has_key ? "on" : "off",
        statusText: d.has_key ? `Connected (${d.api_key_masked})` : "No API key",
      });
    } catch {
      list.push({
        key: "dograh", title: "Voice Platform (Dograh)", icon: Phone, href: "/settings",
        action: "Configure", blurb: "API key + base URL powering runs, knowledge base and telephony.",
        status: "unknown", statusText: "Unreachable",
      });
    }
    try {
      const g = await apiClient.googleSetting();
      list.push({
        key: "google", title: "Google LLM (Gemini)", icon: Sparkles, href: "/settings",
        action: "Configure",
        blurb: "Transcript order recovery — rebuilds orders from call transcripts when tools miss.",
        status: g.has_key ? "on" : "off",
        statusText: g.has_key ? `${g.model} · recovery ${g.recovery_enabled ? "on" : "off"}` : "No API key",
      });
    } catch { /* ignore */ }
    try {
      const lf = await apiClient.langfuseSetting();
      list.push({
        key: "langfuse", title: "Langfuse Observability", icon: Activity, href: "/settings",
        action: "Configure",
        blurb: "Per-run trace deep links from transactions and run evidence.",
        status: lf.has_keys && lf.enabled ? "on" : "off",
        statusText: lf.has_keys ? (lf.enabled ? "Keys saved · links on" : "Keys saved · links off") : "No keys",
      });
    } catch { /* ignore */ }
    list.push({
      key: "telephony", title: "Telephony Providers", icon: Phone, href: "/telephony",
      action: "Manage",
      blurb: "Twilio, Vonage, Plivo, Telnyx, Vobiz, Exotel and SIP configs with phone numbers.",
      status: "unknown", statusText: "Opens Dograh configs",
    });
    list.push({
      key: "models", title: "AI Models (BYOK)", icon: Cpu, href: "/models",
      action: "Manage",
      blurb: "Org-level LLM / STT / TTS / embedding keys applied to the next call.",
      status: "unknown", statusText: "Opens model config",
    });
    list.push({
      key: "kb", title: "Knowledge Base", icon: BookOpen, href: "/knowledge-base",
      action: "Manage",
      blurb: "Documents the agent retrieves live during calls.",
      status: "unknown", statusText: "Opens documents",
    });
    setEntries(list);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <PageHeader
        icon={Blocks}
        title="Integrations"
        description="Every external service this dashboard talks to — status at a glance, one click to configure."
        actions={
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {entries.map((e) => (
          <Card key={e.key} className="card-clean">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm text-[#000E49]">
                <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#EAF1FF]">
                  <e.icon className="h-4 w-4 text-[#0068FF]" />
                </span>
                {e.title}
                {e.status === "on" ? (
                  <Badge className="ml-auto bg-green-100 text-green-800 hover:bg-green-100">
                    <CheckCircle2 className="mr-1 h-3 w-3" /> {e.statusText}
                  </Badge>
                ) : e.status === "off" ? (
                  <Badge variant="secondary" className="ml-auto bg-gray-100 text-gray-700">
                    <CircleDashed className="mr-1 h-3 w-3" /> {e.statusText}
                  </Badge>
                ) : (
                  <Badge variant="outline" className="ml-auto text-[11px]">{e.statusText}</Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <CardDescription>{e.blurb}</CardDescription>
              <Link href={e.href}>
                <Button size="sm" variant="outline">
                  {e.action} <ArrowUpRight className="ml-1 h-3 w-3" />
                </Button>
              </Link>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
