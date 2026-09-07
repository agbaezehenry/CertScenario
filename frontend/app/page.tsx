"use client";
import Link from "next/link";
import { useState } from "react";
import { PageHeader, SevPill, StatusPill, ErrorBox } from "@/components/ui";
import { api, fmtDateTime, minutesSince, type Session } from "@/lib/api";
import { useStore, usePoll } from "@/lib/store";

const CHECKS: [string, string][] = [
  ["incident_acknowledged", "Incident acknowledged"],
  ["network_repaired", "Network repaired (resolution checks pass)"],
  ["regression_passed", "Regression checks passed"],
  ["resolution_submitted", "Resolution update submitted"],
  ["postmortem_completed", "Postmortem completed"],
];

export default function Dashboard() {
  const { me, session, startScenario, refresh } = useStore();
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const live = usePoll(() => (session ? api<Session>(`/api/sessions/${session.id}`) : Promise.resolve(null)), 5000, [session?.id]);
  if (!me) return null;
  const s = live.data ?? session;

  return (
    <div>
      <PageHeader title={`Good morning, ${me.user.display_name}`} subtitle={`${me.employee.title} · ${me.team.name}`} />
      <ErrorBox msg={err} />
      <div className="grid grid-cols-3 gap-4 p-6">
        <div className="card p-4">
          <div className="label">You</div>
          <div className="mt-1 text-base font-semibold">{me.user.display_name}</div>
          <div className="text-sm text-ink-600">{me.employee.title}</div>
          <div className="mt-3 label">Team</div>
          <div className="text-sm">{me.team.name}</div>
          <div className="mt-3 label">Manager</div>
          <div className="text-sm">{me.manager?.name ?? "—"}</div>
          <div className="mt-3 label">Responsibility level</div>
          <div className="text-sm">{me.responsibility_level}</div>
        </div>

        <div className="card col-span-2 p-4">
          <div className="flex items-center justify-between">
            <div className="label">Assigned</div>
            {s && <span className="text-[11px] text-ink-500">session started {fmtDateTime(s.started_at)} · {minutesSince(s.started_at)} min ago</span>}
          </div>
          {!s && me.last_session && me.last_session.end_reason && me.last_session.end_reason !== "completed" && (
            <div className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              {me.last_session.end_reason === "idle" && "Your previous session was closed after 20 minutes without activity; its lab was destroyed."}
              {me.last_session.end_reason === "absolute" && "Your previous session hit the 4-hour limit and was closed."}
              {me.last_session.end_reason === "abandoned" && "Your previous session was abandoned."}
              {me.last_session.end_reason === "failed" && "Your previous session failed to provision. Check the backend logs."}
              {!["idle", "absolute", "abandoned", "failed"].includes(me.last_session.end_reason) && `Your previous session ended (${me.last_session.end_reason}).`}
              {" "}Progress is not carried over. <Link href="/scorecard" className="underline">Review it</Link> or clock in again.
            </div>
          )}
          {!s && (
            <div className="mt-3">
              <div className="text-sm text-ink-600">Nothing assigned right now.</div>
              {me.scenarios.map((sc) => (
                <div key={sc.id} className="mt-3 flex items-center justify-between rounded border border-dashed border-ink-300 p-3">
                  <div>
                    <div className="text-sm font-medium">Start your shift</div>
                    <div className="text-[12px] text-ink-500">Provisions your lab environment and opens your inbox.</div>
                  </div>
                  <button
                    className="btn-primary"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      setErr(null);
                      try {
                        await startScenario(sc.id);
                        await refresh();
                      } catch (e) {
                        setErr(e instanceof Error ? e.message : String(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    {busy ? "Provisioning lab…" : "Clock in"}
                  </button>
                </div>
              ))}
            </div>
          )}
          {s &&
            me.incidents.map((inc) => (
              <Link key={inc.id} href={`/incidents/${inc.id}`} className="mt-3 block rounded border border-ink-200 p-3 hover:bg-ink-50">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm font-semibold">{inc.id}</span>
                  <SevPill sev={inc.severity} />
                  <StatusPill status={inc.status} />
                  <span className="ml-auto text-[11px] text-ink-500">DRI: you</span>
                </div>
                <div className="mt-1 text-sm">{inc.title}</div>
              </Link>
            ))}
          {s && (
            <div className="mt-4">
              <div className="label">Completion</div>
              <ul className="mt-1 space-y-1 text-sm">
                {CHECKS.map(([k, label]) => (
                  <li key={k} className="flex items-center gap-2">
                    <span className={`font-mono ${s.completion?.[k] ? "text-ok" : "text-ink-300"}`}>{s.completion?.[k] ? "☑" : "☐"}</span>
                    <span className={s.completion?.[k] ? "text-ink-800" : "text-ink-500"}>{label}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-3 flex gap-2">
                <Link href="/chat/dm-maya" className="btn-ghost">Open inbox</Link>
                <Link href="/terminal" className="btn-ghost">Terminal</Link>
                {s.completion && Object.values(s.completion).every(Boolean) && <Link href="/scorecard" className="btn-primary">Close out incident</Link>}
              </div>
            </div>
          )}
        </div>

        {s && (
          <div className="card col-span-3 p-4">
            <div className="flex items-center justify-between">
              <div className="label">Lab environment</div>
              <button
                className="btn-danger"
                onClick={async () => {
                  if (!confirm("Abandon this session and destroy the lab?")) return;
                  await api(`/api/sessions/${s.id}`, { method: "DELETE" });
                  await refresh();
                }}
              >
                Abandon session
              </button>
            </div>
            <div className="mt-2 grid grid-cols-4 gap-4 text-sm">
              <div><div className="text-[11px] text-ink-500">Lab</div><div className="font-mono">{s.lab_id}</div></div>
              <div><div className="text-[11px] text-ink-500">State</div><div>{s.state}</div></div>
              <div><div className="text-[11px] text-ink-500">Prober</div><div>{s.prober_running ? "running" : "stopped"}</div></div>
              <div><div className="text-[11px] text-ink-500">Idle timeout</div><div>20 min · absolute 4 h</div></div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
