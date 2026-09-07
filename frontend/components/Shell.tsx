"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useStore } from "@/lib/store";
import { PEOPLE } from "@/lib/api";

const NAV = [
  { href: "/", label: "Dashboard", icon: "▦" },
  { href: "/incidents", label: "Incidents", icon: "▲" },
  { href: "/chat", label: "Chat", icon: "◆" },
  { href: "/monitoring", label: "Monitoring", icon: "◉" },
  { href: "/wiki", label: "Wiki", icon: "≡" },
  { href: "/changes", label: "Change Log", icon: "⇄" },
  { href: "/network", label: "Network", icon: "⌘" },
  { href: "/terminal", label: "Terminal", icon: ">_" },
];

const STATE_PILL: Record<string, string> = {
  ACTIVE: "pill-info",
  INVESTIGATING: "pill-warn",
  MITIGATED: "pill-warn",
  RESOLVED: "pill-ok",
  POSTMORTEM: "pill-ok",
  COMPLETED: "pill-muted",
};

export default function Shell({ children }: { children: React.ReactNode }) {
  const { me, loading, session, logout } = useStore();
  const path = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !me && path !== "/login") router.replace("/login");
  }, [loading, me, path, router]);

  if (path === "/login") return <div className="h-screen">{children}</div>;
  if (loading) return <div className="flex h-screen items-center justify-center text-ink-500">Loading workstation…</div>;
  if (!me) return null;

  const person = PEOPLE[me.user.id];
  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-56 shrink-0 flex-col bg-ink-950 text-ink-300">
        <div className="border-b border-ink-800 px-4 py-3">
          <div className="text-[13px] font-semibold tracking-wide text-white">Northstar Technologies</div>
          <div className="mt-0.5 text-[11px] text-ink-500">Engineering Workstation</div>
        </div>
        <nav className="flex-1 overflow-y-auto py-2">
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`mx-2 my-0.5 flex items-center gap-2.5 rounded px-2.5 py-1.5 text-[13px] ${
                  active ? "bg-ink-800 text-white" : "hover:bg-ink-900 hover:text-white"
                }`}
              >
                <span className="w-4 text-center font-mono text-[11px] text-ink-500">{n.icon}</span>
                {n.label}
              </Link>
            );
          })}
          {session && (session.state === "RESOLVED" || session.state === "POSTMORTEM") && (
            <Link href="/postmortem" className={`mx-2 my-0.5 flex items-center gap-2.5 rounded px-2.5 py-1.5 text-[13px] ${path.startsWith("/postmortem") ? "bg-ink-800 text-white" : "hover:bg-ink-900 hover:text-white"}`}>
              <span className="w-4 text-center font-mono text-[11px] text-ink-500">✎</span>Postmortem
            </Link>
          )}
          {session && (
            <Link href="/scorecard" className={`mx-2 my-0.5 flex items-center gap-2.5 rounded px-2.5 py-1.5 text-[13px] ${path.startsWith("/scorecard") ? "bg-ink-800 text-white" : "hover:bg-ink-900 hover:text-white"}`}>
              <span className="w-4 text-center font-mono text-[11px] text-ink-500">★</span>Review
            </Link>
          )}
        </nav>
        <div className="border-t border-ink-800 px-4 py-3 text-[11px]">
          {session ? (
            <div>
              <div className="flex items-center justify-between">
                <span className="text-ink-500">Session</span>
                <span className={STATE_PILL[session.state] ?? "pill-muted"}>{session.state}</span>
              </div>
              <div className="mt-1 font-mono text-ink-400">{session.scenario_id} · {session.lab_id}</div>
            </div>
          ) : (
            <div className="text-ink-500">No active session</div>
          )}
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-11 shrink-0 items-center justify-between border-b border-ink-200 bg-white px-4">
          <div className="text-[13px] text-ink-500">
            {me.team.name} <span className="mx-1.5 text-ink-300">/</span> <span className="text-ink-800">NetOps</span>
          </div>
          <div className="flex items-center gap-3 text-[13px]">
            {me.character_mode === "stub" && <span className="pill-muted" title="No LLM configured; characters use rule-based replies">characters: offline</span>}
            <div className="flex items-center gap-2">
              <span className={`flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-bold text-white ${person?.color ?? "bg-ink-600"}`}>{person?.initials ?? "?"}</span>
              <span className="text-ink-800">{me.user.display_name}</span>
              <span className="text-ink-400">·</span>
              <span className="text-ink-500">{me.employee.title}</span>
            </div>
            <button onClick={logout} className="text-ink-400 hover:text-ink-700">Sign out</button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
