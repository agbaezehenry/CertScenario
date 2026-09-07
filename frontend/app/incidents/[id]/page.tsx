"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorBox, NeedsSession, PageHeader, SevPill, StatusPill } from "@/components/ui";
import { api, PEOPLE, type Incident } from "@/lib/api";
import { useStore } from "@/lib/store";

const STATUSES = ["Assigned", "Investigating", "Mitigated", "Resolved"];

export default function IncidentPage() {
  const { id } = useParams<{ id: string }>();
  const { session, refresh } = useStore();
  const [inc, setInc] = useState<Incident | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [form, setForm] = useState({ impact: "", root_cause: "", resolution: "" });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    const i = await api<Incident>(`/api/incidents/${id}`);
    setInc(i);
    setForm({ impact: i.impact ?? "", root_cause: i.root_cause ?? "", resolution: i.resolution ?? "" });
  };
  useEffect(() => {
    if (session) load().catch((e) => setErr(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, session?.id]);

  const patch = async (body: Record<string, string>) => {
    setSaving(true);
    setErr(null);
    try {
      const i = await api<Incident>(`/api/incidents/${id}`, { method: "PATCH", json: body });
      setInc(i);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <NeedsSession>
      {inc && (
        <>
          <PageHeader
            title={`${inc.id} — ${inc.title}`}
            subtitle={`Reported ${inc.reported_at} by ${PEOPLE[inc.reporter_id]?.name ?? inc.reporter_id} · DRI ${PEOPLE[inc.dri_id]?.name ?? inc.dri_id}`}
            right={
              <div className="flex items-center gap-2">
                <SevPill sev={inc.severity} />
                <StatusPill status={inc.status} />
              </div>
            }
          />
          <ErrorBox msg={err} />
          <div className="grid grid-cols-3 gap-4 p-6">
            <div className="col-span-2 space-y-4">
              <div className="card p-4">
                <div className="label">Description</div>
                <p className="mt-1 text-sm">{inc.description}</p>
                <div className="mt-3 grid grid-cols-3 gap-3 text-sm">
                  <div><div className="text-[11px] text-ink-500">Affected</div>{inc.affected}</div>
                  <div><div className="text-[11px] text-ink-500">Severity</div>{inc.severity} · manager update every 30 min</div>
                  <div><div className="text-[11px] text-ink-500">Reporter</div>{PEOPLE[inc.reporter_id]?.name}</div>
                </div>
              </div>
              <div className="card p-4">
                <div className="label">Investigation notes</div>
                <ul className="mt-2 space-y-2 text-sm">
                  {inc.notes.length === 0 && <li className="text-ink-400">No notes yet.</li>}
                  {inc.notes.map((n, i) => (
                    <li key={i} className="flex gap-3">
                      <span className="shrink-0 font-mono text-[11px] text-ink-400">{new Date(n.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                      <span>{n.body}</span>
                    </li>
                  ))}
                </ul>
                <form
                  className="mt-3 flex gap-2"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    if (!note.trim()) return;
                    await patch({ note });
                    setNote("");
                  }}
                >
                  <input className="input" placeholder="Add a note (what you checked, what you found)…" value={note} onChange={(e) => setNote(e.target.value)} />
                  <button className="btn-ghost" disabled={saving}>Add</button>
                </form>
              </div>
              <div className="card p-4">
                <div className="label">Resolution record</div>
                {(["impact", "root_cause", "resolution"] as const).map((k) => (
                  <div key={k} className="mt-3">
                    <div className="text-[11px] font-medium text-ink-600">{k === "root_cause" ? "Root cause" : k[0].toUpperCase() + k.slice(1)}</div>
                    <textarea className="input mt-1 h-16" value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
                  </div>
                ))}
                <div className="mt-3 flex justify-end">
                  <button className="btn-primary" disabled={saving} onClick={() => patch(form)}>Save</button>
                </div>
              </div>
            </div>
            <div className="space-y-4">
              <div className="card p-4">
                <div className="label">Status</div>
                <div className="mt-2 flex flex-col gap-1">
                  {STATUSES.map((s) => (
                    <button
                      key={s}
                      disabled={saving || s === inc.status}
                      onClick={() => patch({ status: s })}
                      className={`rounded border px-2 py-1 text-left text-sm ${s === inc.status ? "border-brand-500 bg-brand-100 text-brand-600" : "border-ink-200 hover:bg-ink-50"}`}
                    >
                      {s}
                    </button>
                  ))}
                </div>
                <div className="mt-3 text-[11px] text-ink-500">Moving off “Assigned” acknowledges the incident. Set “Resolved” once verification passes and users confirm.</div>
              </div>
              <div className="card p-4 text-sm">
                <div className="label">Playbook</div>
                <ol className="mt-2 list-decimal space-y-1 pl-4 text-ink-700">
                  <li>Acknowledge and establish blast radius.</li>
                  <li>Update Maya every 30 min (SEV-2).</li>
                  <li>Correlate with the change log before changing anything.</li>
                  <li>Verify from the user side after any fix.</li>
                  <li>Record resolution, then write the postmortem.</li>
                </ol>
              </div>
            </div>
          </div>
        </>
      )}
    </NeedsSession>
  );
}
