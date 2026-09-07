"use client";
import Link from "next/link";
import { PEOPLE } from "@/lib/api";
import { useStore } from "@/lib/store";

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between border-b border-ink-200 bg-white px-6 py-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-900">{title}</h1>
        {subtitle && <div className="mt-0.5 text-sm text-ink-500">{subtitle}</div>}
      </div>
      {right}
    </div>
  );
}

export function Avatar({ id, size = 7 }: { id: string; size?: number }) {
  const p = PEOPLE[id];
  return (
    <span className={`flex h-${size} w-${size} shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-white ${p?.color ?? "bg-ink-500"}`} title={p?.name ?? id}>
      {p?.initials ?? id.slice(0, 2).toUpperCase()}
    </span>
  );
}

export function SevPill({ sev }: { sev: string }) {
  const cls = sev === "SEV-1" ? "pill-crit" : sev === "SEV-2" ? "pill-warn" : "pill-muted";
  return <span className={cls}>{sev}</span>;
}

export function StatusPill({ status }: { status: string }) {
  const s = status.toLowerCase();
  const cls = s === "resolved" ? "pill-ok" : s === "assigned" ? "pill-crit" : "pill-warn";
  return <span className={cls}>{status}</span>;
}

/** Shown on pages that need a live session when there is none. */
export function NeedsSession({ children }: { children: React.ReactNode }) {
  const { session } = useStore();
  if (!session)
    return (
      <div className="p-10 text-center text-sm text-ink-500">
        No active incident session. <Link href="/" className="text-brand-600 underline">Go to the dashboard</Link> to start one.
      </div>
    );
  return <>{children}</>;
}

export function ErrorBox({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return <div className="mx-6 my-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{msg}</div>;
}
