"use client";
import Link from "next/link";
import { useState } from "react";
import { ErrorBox, PageHeader } from "@/components/ui";
import { api, type Scorecard, type Session } from "@/lib/api";
import { usePoll, useStore } from "@/lib/store";

const DIMS: [string, string][] = [
  ["technical_resolution", "Technical Resolution"],
  ["regression_safety", "Regression Safety"],
  ["troubleshooting_method", "Troubleshooting Method"],
  ["incident_management", "Incident Management"],
  ["communication", "Communication"],
  ["postmortem", "Postmortem"],
];

export default function ScorecardPage() {
  const { session, me, refresh } = useStore();
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const sessions = usePoll(() => api<Session[]>("/api/sessions"), 30000, [me?.user.id]);
  const target = session ?? (sessions.data ?? []).filter((s) => s.state === "COMPLETED").slice(-1)[0] ?? null;
  const card = usePoll(() => (target ? api<Scorecard>(`/api/sessions/${target.id}/scorecard`) : Promise.resolve(null)), 8000, [target?.id]);
  const detail = target ? usePollSession(target.id) : null;
  const complete = async () => {
    if (!target) return;
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/sessions/${target.id}/complete`, { method: "POST" });
      await refresh();
      card.setData(await api<Scorecard>(`/api/sessions/${target.id}/scorecard`));
    } catch (e) {
      const d = (e as { detail?: { missing?: string[] } }).detail;
      setErr(d?.missing ? `Not ready to close: ${d.missing.join(", ").replace(/_/g, " ")}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const c = card.data;
  const ready = detail?.completion && Object.values(detail.completion).every(Boolean);
  return (
    <div>
      <PageHeader
        title={c?.preview ? "Incident review (in progress)" : "Incident Complete"}
        subtitle={target ? `${target.scenario_id} · session ${target.id}` : "No session"}
        right={target && session && <button className="btn-primary" disabled={busy || !ready} onClick={complete} title={ready ? "" : "Finish the completion checklist first"}>{busy ? "Closing…" : "Close out incident"}</button>}
      />
      <ErrorBox msg={err} />
      {!target && <div className="p-6 text-sm text-ink-500">Nothing to review yet.</div>}
      {c && (
        <div className="grid grid-cols-3 gap-4 p-6">
          <div className="card p-4">
            {DIMS.map(([k, label]) => (
              <div key={k} className="flex items-center justify-between border-b border-ink-100 py-2 text-sm last:border-0">
                <span>{label}</span>
                <span className="font-mono font-semibold">{Math.round(c.scores[k] ?? 0)}%</span>
              </div>
            ))}
            <div className="mt-3 flex items-center justify-between text-base font-semibold">
              <span>Overall</span>
              <span className="font-mono">{Math.round(c.overall)}%</span>
            </div>
            {c.preview && <div className="mt-3 text-[11px] text-ink-500">Preview based on the session so far. Final scores are computed when you close out the incident.</div>}
          </div>
          <div className="card col-span-2 p-4">
            <div className="label text-ok">Strong</div>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
              {c.strengths.length === 0 && <li className="list-none text-ink-400">—</li>}
              {c.strengths.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
            <div className="mt-4 label text-warn">Improve</div>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
              {c.improvements.length === 0 && <li className="list-none text-ink-400">—</li>}
              {c.improvements.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
            <div className="mt-4 text-[11px] text-ink-400">Every item above cites the incident record: command telemetry, prober log, ticket updates and chat. Nothing is inferred by a model.</div>
            {!c.preview && <div className="mt-4"><Link href="/" className="btn-ghost">Back to dashboard</Link></div>}
          </div>
        </div>
      )}
    </div>
  );
}

function usePollSession(id: string) {
  const { data } = usePoll(() => api<Session>(`/api/sessions/${id}`), 5000, [id]);
  return data;
}
