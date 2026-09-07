"use client";
import { useEffect, useState } from "react";
import { NeedsSession, PageHeader } from "@/components/ui";
import { api, PEOPLE, type Change } from "@/lib/api";
import { useStore } from "@/lib/store";

export default function Changes() {
  const { session } = useStore();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Change[]>([]);
  const [sel, setSel] = useState<Change | null>(null);
  useEffect(() => {
    if (!session) return;
    const t = window.setTimeout(() => {
      api<Change[]>(`/api/changes${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(setItems).catch(() => setItems([]));
    }, 200);
    return () => window.clearTimeout(t);
  }, [q, session]);
  const open = async (c: Change) => {
    setSel(await api<Change>(`/api/changes/${c.id}`));
  };
  return (
    <NeedsSession>
      <PageHeader title="Change Log" subtitle="All approved changes, newest first" />
      <div className="grid grid-cols-5 gap-4 p-6">
        <div className="col-span-3">
          <input className="input mb-3" placeholder="Search by device, engineer, ticket, keyword…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
          <table className="card w-full text-sm">
            <thead>
              <tr className="text-left"><th className="label px-3 py-2">Change</th><th className="label px-3 py-2">Device</th><th className="label px-3 py-2">Applied</th><th className="label px-3 py-2">Engineer</th><th className="label px-3 py-2">Category</th></tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id} onClick={() => open(c)} className={`cursor-pointer border-t border-ink-100 ${sel?.id === c.id ? "bg-brand-100" : "hover:bg-ink-50"}`}>
                  <td className="px-3 py-2"><span className="font-mono text-brand-600">{c.id}</span><div className="text-[12px] text-ink-600">{c.summary}</div></td>
                  <td className="px-3 py-2 font-mono">{c.device}</td>
                  <td className="px-3 py-2 font-mono text-[12px]">{c.applied_at}</td>
                  <td className="px-3 py-2">{PEOPLE[c.engineer]?.name ?? c.engineer}</td>
                  <td className="px-3 py-2"><span className="pill-muted">{c.category}</span></td>
                </tr>
              ))}
              {items.length === 0 && <tr><td colSpan={5} className="px-3 py-6 text-center text-ink-400">No changes match.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="col-span-2">
          {sel ? (
            <div className="card p-4 text-sm">
              <div className="font-mono text-base font-semibold">{sel.id}</div>
              <div className="mt-1 font-medium">{sel.summary}</div>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[13px]">
                <dt className="text-ink-500">Device</dt><dd className="font-mono">{sel.device}</dd>
                <dt className="text-ink-500">Engineer</dt><dd>{PEOPLE[sel.engineer]?.name ?? sel.engineer}</dd>
                <dt className="text-ink-500">Window</dt><dd className="font-mono">{sel.window}</dd>
                <dt className="text-ink-500">Applied</dt><dd className="font-mono">{sel.applied_at}</dd>
                <dt className="text-ink-500">Status</dt><dd>{sel.status}</dd>
                <dt className="text-ink-500">Category</dt><dd>{sel.category}</dd>
              </dl>
              <div className="mt-3 label">Description</div>
              <p className="mt-1 whitespace-pre-wrap">{sel.description}</p>
            </div>
          ) : (
            <div className="card p-4 text-sm text-ink-400">Select a change to see its details.</div>
          )}
        </div>
      </div>
    </NeedsSession>
  );
}
