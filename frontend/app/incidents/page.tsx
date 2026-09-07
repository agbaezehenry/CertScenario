"use client";
import Link from "next/link";
import { NeedsSession, PageHeader, SevPill, StatusPill } from "@/components/ui";
import { api, type Incident } from "@/lib/api";
import { usePoll, useStore } from "@/lib/store";

export default function Incidents() {
  const { session } = useStore();
  const { data } = usePoll(() => (session ? api<Incident[]>("/api/incidents") : Promise.resolve([])), 10000, [session?.id]);
  return (
    <NeedsSession>
      <PageHeader title="Incidents" subtitle="Tickets where you are DRI" />
      <div className="p-6">
        <table className="card w-full text-sm">
          <thead>
            <tr className="text-left">
              <th className="label px-3 py-2">ID</th><th className="label px-3 py-2">Title</th><th className="label px-3 py-2">Sev</th>
              <th className="label px-3 py-2">Status</th><th className="label px-3 py-2">Affected</th><th className="label px-3 py-2">Reported</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((i) => (
              <tr key={i.id} className="border-t border-ink-100 hover:bg-ink-50">
                <td className="px-3 py-2 font-mono"><Link className="text-brand-600" href={`/incidents/${i.id}`}>{i.id}</Link></td>
                <td className="px-3 py-2">{i.title}</td>
                <td className="px-3 py-2"><SevPill sev={i.severity} /></td>
                <td className="px-3 py-2"><StatusPill status={i.status} /></td>
                <td className="px-3 py-2">{i.affected}</td>
                <td className="px-3 py-2 font-mono">{i.reported_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </NeedsSession>
  );
}
