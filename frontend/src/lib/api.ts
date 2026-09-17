export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body.slice(0, 200)}`);
  }
  return res.json();
}

// ---------- types ----------

export interface OrderItem {
  sku: string;
  name: string;
  quantity: number;
  unit_price: number | string;
  size: string;
  notes: string;
}

export interface OrderEvent {
  status: string;
  note: string;
  created_at: string;
}

export interface OrderRow {
  order_number: string;
  customer_name: string;
  customer_phone: string;
  order_type: string;
  table_number: string;
  pickup_time: string;
  delivery_address: string;
  payment_method: string;
  status: string;
  total: number;
  source: string;
  item_count: number;
  created_at: string;
}

export interface OrderDetail extends OrderRow {
  email: string;
  call_id: string;
  items: OrderItem[];
  events: OrderEvent[];
}

export interface CallRow {
  call_id: string;
  call_type: string;
  disposition: string;
  started_at: string;
  duration_seconds: number;
  customer_name: string;
  customer_phone: string;
  first_name: string;
  rsvp: string;
  summary: string;
  escalation_reason: string;
  order_numbers: string[];
  sentiment: string;
  recording_url: string;
  transcript_url: string;
}

export interface TranscriptTurn {
  role: string;
  content: string;
}

export interface CallDetail extends CallRow {
  ended_at: string | null;
  transcript: TranscriptTurn[];
  tool_calls: { name: string; args_summary: string; result?: unknown }[];
  cost: number | null;
  recording_url: string;
  transcript_url: string;
  user_recording_url: string;
  bot_recording_url: string;
  genesys_conversation_id: string;
  workflow_id: number | null;
  run_id: number | null;
  workflow_name: string;
  call_type: string;
}

export interface DograhSetting {
  base_url: string;
  api_key_masked: string;
  has_key: boolean;
}

export interface GoogleSetting {
  model: string;
  api_key_masked: string;
  has_key: boolean;
  recovery_enabled: boolean;
  use_audio: boolean;
}

export interface LangfuseSetting {
  host: string;
  project_id: string;
  public_key_masked: string;
  has_keys: boolean;
  enabled: boolean;
  dograh_pushed?: boolean | null;
  dograh_detail?: string | null;
}

export interface DograhRun {
  run_id: number;
  workflow_id: number;
  workflow_name: string | null;
  name: string | null;
  created_at: string;
  duration_seconds: number;
  disposition: string | null;
  call_type: string | null;
  phone_number: string | null;
  charge_usd: number | null;
  transcript_public_url: string | null;
  recording_public_url: string | null;
  gathered_context: Record<string, unknown> | null;
}

export interface Analytics {
  total_calls: number;
  ai_resolved: number;
  transferred: number;
  failed: number;
  orders_created: number;
  revenue: number;
  avg_duration_seconds: number;
  daily: {
    date: string;
    calls: number;
    transferred: number;
    orders: number;
    revenue: number;
  }[];
}

// ---------- endpoints ----------

export const apiClient = {
  analytics: () => api<Analytics>("/api/analytics/summary"),
  orders: (params?: { status?: string; search?: string; order_type?: string }) => {
    const qs = new URLSearchParams(
      Object.entries(params || {}).filter(([, v]) => v) as [string, string][]
    );
    return api<OrderRow[]>(`/api/orders?${qs}`);
  },
  orderDetail: (orderNumber: string) =>
    api<OrderDetail>(`/api/orders/${orderNumber}`),
  updateOrderStatus: (orderNumber: string, status: string, note = "") =>
    api<{ status: string }>(`/api/orders/${orderNumber}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status, note }),
    }),
  dograhSetting: () => api<DograhSetting>("/api/integration/dograh"),
  saveDograhSetting: (s: { api_key?: string; base_url?: string }) =>
    api<DograhSetting>("/api/integration/dograh", {
      method: "PUT",
      body: JSON.stringify(s),
    }),
  googleSetting: () => api<GoogleSetting>("/api/integration/google"),
  saveGoogleSetting: (s: { api_key?: string; model?: string; recovery_enabled?: boolean; use_audio?: boolean }) =>
    api<GoogleSetting>("/api/integration/google", {
      method: "PUT",
      body: JSON.stringify(s),
    }),
  langfuseSetting: () => api<LangfuseSetting>("/api/integration/langfuse"),
  saveLangfuseSetting: (s: { public_key?: string; secret_key?: string; host?: string; project_id?: string; enabled?: boolean }) =>
    api<LangfuseSetting>("/api/integration/langfuse", {
      method: "PUT",
      body: JSON.stringify(s),
    }),
  langfuseTraceUrl: (params: { run_id?: number; workflow_id?: number; call_id?: string; trace_id?: string }) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][]
    );
    return api<{ url: string; trace_id: string; source: string }>(
      `/api/integration/langfuse/trace-url?${qs}`
    );
  },
  recoverCallOrder: (callId: string) =>
    api<{ status: string; order_number?: string; reason?: string }>(`/api/integration/google/recover/${encodeURIComponent(callId)}`, {
      method: "POST",
    }),
  dograhHealth: () => api<{ ok: boolean; status: number | null; detail: string }>("/api/integration/dograh/health"),
  dograhRuns: (page = 1, limit = 50) =>
    api<{ runs: DograhRun[]; total_count: number; total_pages: number }>(
      `/api/integration/dograh/runs?page=${page}&limit=${limit}`
    ),
  dograhRunTranscript: (runId: number, workflowId?: number) =>
    api<{ run_id: number; transcript: string }>(
      `/api/integration/dograh/runs/${runId}/transcript${workflowId ? `?workflow_id=${workflowId}` : ""}`
    ),
  dograhRunDetail: (runId: number, workflowId: number) =>
    api<Record<string, unknown>>(
      `/api/integration/dograh/runs/${runId}/detail?workflow_id=${workflowId}`
    ),
  findRunByCallId: (callUuid: string) =>
    api<{ found: boolean; run_id: number | null; workflow_id: number | null }>(
      `/api/integration/dograh/runs/by-call-id/${encodeURIComponent(callUuid)}`
    ),
  importDograhRun: (runId: number, workflowId: number) =>
    api<{ status: string; call_id: string; disposition: string }>(
      `/api/integration/dograh/runs/${runId}/import?workflow_id=${workflowId}`,
      { method: "POST" }
    ),
  dograhModels: () =>
    api<{ configuration: Record<string, unknown> | null; effective_configuration: Record<string, unknown>; source: string }>(
      "/api/integration/dograh/models"
    ),
  saveDograhModels: (payload: Record<string, unknown>) =>
    api<Record<string, unknown>>("/api/integration/dograh/models", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  calls: (params?: { disposition?: string; call_type?: string; search?: string }) => {
    const qs = new URLSearchParams(
      Object.entries(params || {}).filter(([, v]) => v) as [string, string][]
    );
    const suffix = qs.toString() ? `?${qs}` : "";
    return api<CallRow[]>(`/api/calls${suffix}`);
  },
  callDetail: (callId: string) => api<CallDetail>(`/api/calls/${callId}`),
};

export function fmtDuration(s: number) {
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

export function fmtDateTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtMoney(n: number) {
  return `$${Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
