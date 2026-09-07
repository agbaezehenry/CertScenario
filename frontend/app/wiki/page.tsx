"use client";
import Link from "next/link";
import { NeedsSession, PageHeader } from "@/components/ui";
import { api, type WikiPage } from "@/lib/api";
import { usePoll, useStore } from "@/lib/store";

export default function WikiIndex() {
  const { session } = useStore();
  const { data } = usePoll(() => (session ? api<WikiPage[]>("/api/wiki") : Promise.resolve([])), 60000, [session?.id]);
  return (
    <NeedsSession>
      <PageHeader title="Wiki" subtitle="Infrastructure Engineering knowledge base" />
      <div className="grid grid-cols-2 gap-4 p-6">
        {(data ?? []).map((p) => (
          <Link key={p.id} href={`/wiki/${p.id}`} className="card p-4 hover:bg-ink-50">
            <div className="text-sm font-semibold">{p.title}</div>
            <div className="mt-1 text-[11px] text-ink-500">Owner {p.owner ?? "—"} · reviewed {p.last_reviewed ?? "—"}</div>
          </Link>
        ))}
        <Link href="/changes" className="card p-4 hover:bg-ink-50">
          <div className="text-sm font-semibold">Change Log</div>
          <div className="mt-1 text-[11px] text-ink-500">Searchable record of maintenance-window changes</div>
        </Link>
      </div>
    </NeedsSession>
  );
}
