export const API_BASE =
  (typeof window !== "undefined" && (window as unknown as { __API__?: string }).__API__) ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

const TOKEN_KEY = "northstar_token";

export function getToken(): string | null {
  try {
    return typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) : null;
  } catch {
    return null;
  }
}
export function setToken(t: string | null) {
  try {
    if (t) window.localStorage.setItem(TOKEN_KEY, t);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers, body });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data);
  return data as T;
}

export function wsUrl(path: string): string {
  const base = API_BASE.replace(/^http/, "ws");
  const token = getToken() ?? "";
  return `${base}${path}?token=${encodeURIComponent(token)}`;
}

// ---- types (mirror backend Pydantic models) ----
export interface Employee { id: string; name: string; title: string; team_id: string; manager_id: string | null; is_ai_character: boolean }
export interface Me {
  user: { id: string; email: string; display_name: string; employee_id: string };
  employee: Employee;
  team: { id: string; name: string; manager_id: string | null };
  manager: Employee | null;
  company: { id: string; name: string };
  responsibility_level: string;
  active_session: Session | null;
  incidents: Incident[];
  scenarios: { id: string; title: string }[];
  character_mode: "llm" | "stub";
}
export interface Session {
  id: string; scenario_id: string; learner_id: string; state: string; lab_id: string | null;
  started_at: string; last_activity_at: string; ended_at: string | null; container_minutes: number;
  completion?: Record<string, boolean>; prober_running?: boolean;
}
export interface Incident {
  id: string; title: string; severity: string; status: string; reported_at: string; reporter_id: string;
  affected: string; description: string; dri_id: string; notes: { at: string; body: string }[];
  resolution: string | null; impact: string | null; root_cause: string | null;
}
export interface Conversation { id: string; kind: "channel" | "dm"; name: string; participant_ids: string[]; last_message: Message | null }
export interface Message { id: string; conversation_id: string; sender_id: string; body: string; sent_at: string }
export interface WikiPage { id: string; title: string; body?: string; owner: string | null; last_reviewed: string | null }
export interface Change { id: string; device: string; engineer: string; window: string; applied_at: string; status: string; category: string; summary: string; description: string }
export interface Monitoring {
  tiles: { id: string; label: string; state: string; value: string | null }[];
  pairs: Record<string, { label: string; state: string; rtt_ms: number | null }>;
  outages: { pair: string; started_at: string; ended_at: string | null; duration_s: number | null }[];
  prober_running: boolean; captured_at: string;
}
export interface Check { id: string; check: string; category: string; passed: boolean; detail: string }
export interface Verification { passed: number; failed: number; categories: Record<string, { passed: number; failed: number }>; checks: Check[]; scenario_success: boolean; quality_ok: boolean; run_at: string }
export interface Scorecard { preview: boolean; scores: Record<string, number>; overall: number; strengths: string[]; improvements: string[]; detail: Record<string, unknown> }
export interface SimEvent { timestamp: string; event_type: string; source: string; metadata: Record<string, unknown> }

export const PEOPLE: Record<string, { name: string; title: string; initials: string; color: string }> = {
  henry: { name: "Henry", title: "Associate Network Engineer", initials: "HE", color: "bg-brand-600" },
  maya: { name: "Maya Chen", title: "Network Engineering Manager", initials: "MC", color: "bg-purple-600" },
  carlos: { name: "Carlos Ramirez", title: "Help Desk Technician", initials: "CR", color: "bg-amber-600" },
  priya: { name: "Priya Shah", title: "Senior Network Engineer", initials: "PS", color: "bg-emerald-600" },
};

export function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
export function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
export function minutesSince(iso: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
}
