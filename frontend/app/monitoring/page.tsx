"use client";
import { NeedsSession, PageHeader } from "@/components/ui";
import { api, fmtTime, type Monitoring } from "@/lib/api";
import { usePoll, useStore } from "@/lib/store";

const STATE_CLS: Record<string, string> = { UP: "text-ok", OK: "text-ok", CRITICAL: "text-crit", "NO DATA": "text-ink-400", DOWN: "text-crit" };

export default function MonitoringPage() {
  const { session } = useStore();
  const { data, err } = usePoll(() => (session ? api<Monitoring>("/api/monitoring") : Promise.resolve(null)), 2000, [session?.id]);
  return (
    <NeedsSession>
      <PageHeader title="Monitoring" subtitle="NetOps board · 1 s synthetic probes from the monitoring node" right={data && <span className="text-[11px] text-ink-400">updated {fmtTime(data.captured_at)}</span>} />
      {err && <div className="mx-6 mt-3 text-sm text-red-700">{err}</div>}
      <div className="grid grid-cols-3 gap-4 p-6">
        {(data?.tiles ?? []).map((t) => (
          <div key={t.id} className="card p-4">
            <div className="text-[12px] text-ink-500">{t.label}</div>
            <div className={`mt-1 font-mono text-2xl font-semibold ${STATE_CLS[t.state] ?? "text-ink-800"}`}>{t.value ?? t.state}</div>
            {t.value && <div className="text-[11px] text-ink-400">{t.state}</div>}
          </div>
        ))}
      </div>
      <div className="px-6 pb-6">
        <div className="card p-4">
          <div className="label">Recent state changes</div>
          {data && data.outages.length === 0 && <div className="mt-2 text-sm text-ink-400">No transitions recorded this session.</div>}
          <table className="mt-2 w-full text-sm">
            <tbody>
              {(data?.outages ?? []).slice().reverse().map((o, i) => (
                <tr key={i} className="border-t border-ink-100">
                  <td className="py-1 font-mono text-[12px]">{o.pair}</td>
                  <td className="py-1 text-crit">DOWN {fmtTime(o.started_at)}</td>
                  <td className="py-1">{o.ended_at ? <span className="text-ok">UP {fmtTime(o.ended_at)} ({Math.round(o.duration_s ?? 0)} s)</span> : <span className="text-ink-400">ongoing</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3 text-[11px] text-ink-400">Probes originate from the NetOps monitoring node (10.20.10.250 in Austin, 10.10.30.2 at HQ), not from user workstations.</div>
        </div>
      </div>
    </NeedsSession>
  );
}
