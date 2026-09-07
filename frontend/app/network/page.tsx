"use client";
import Link from "next/link";
import { NeedsSession, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { usePoll, useStore } from "@/lib/store";

interface Dev { name: string; node: string; kind: string; site: string; address: string | null }
const POS: Record<string, [number, number]> = {
  "HQ-CLIENT": [120, 70], "APP-SRV": [360, 70], "HQ-RTR1": [240, 170], "AUS-RTR1": [240, 330], "AUS-SW1": [240, 430], "AUS-CLIENT1": [120, 520], "AUS-CLIENT2": [360, 520],
};
const LINKS: [string, string, string?][] = [["HQ-CLIENT", "HQ-RTR1"], ["APP-SRV", "HQ-RTR1"], ["HQ-RTR1", "AUS-RTR1", "WAN transit 10.255.0.0/30"], ["AUS-RTR1", "AUS-SW1"], ["AUS-SW1", "AUS-CLIENT1"], ["AUS-SW1", "AUS-CLIENT2"]];

export default function NetworkPage() {
  const { session } = useStore();
  const { data } = usePoll(() => (session ? api<{ devices: Dev[] }>("/api/network") : Promise.resolve(null)), 60000, [session?.id]);
  const devs = (data?.devices ?? []).filter((d) => POS[d.name]);
  return (
    <NeedsSession>
      <PageHeader title="Network" subtitle="Austin branch and HQ core — as documented" right={<Link href="/wiki/austin-branch-network" className="btn-ghost">Wiki page</Link>} />
      <div className="grid grid-cols-5 gap-4 p-6">
        <div className="card col-span-3 p-4">
          <svg viewBox="0 0 480 580" className="mx-auto w-full max-w-[520px]">
            <rect x="30" y="20" width="420" height="215" rx="8" fill="#f9fafb" stroke="#e5e7eb" />
            <text x="44" y="42" fontSize="11" fill="#6b7280" fontWeight="600">HQ · 10.10.0.0/16</text>
            <rect x="30" y="285" width="420" height="275" rx="8" fill="#f9fafb" stroke="#e5e7eb" />
            <text x="44" y="307" fontSize="11" fill="#6b7280" fontWeight="600">AUSTIN · 10.20.10.0/24</text>
            {LINKS.map(([a, b, label]) => {
              const [x1, y1] = POS[a]; const [x2, y2] = POS[b];
              return (
                <g key={a + b}>
                  <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#9ca3af" strokeWidth="2" />
                  {label && <text x={(x1 + x2) / 2 + 8} y={(y1 + y2) / 2} fontSize="10" fill="#6b7280" fontFamily="monospace">{label}</text>}
                </g>
              );
            })}
            {devs.map((d) => {
              const [x, y] = POS[d.name];
              const router = d.kind === "router";
              return (
                <g key={d.name}>
                  {router ? <circle cx={x} cy={y} r="22" fill="#1d4ed8" /> : d.kind === "switch" ? <rect x={x - 28} y={y - 14} width="56" height="28" rx="3" fill="#4b5563" /> : <rect x={x - 22} y={y - 16} width="44" height="32" rx="4" fill="#ffffff" stroke="#374151" strokeWidth="1.5" />}
                  <text x={x} y={y + 40} textAnchor="middle" fontSize="11" fontFamily="monospace" fontWeight="600" fill="#111827">{d.name}</text>
                  {d.address && <text x={x} y={y + 52} textAnchor="middle" fontSize="10" fontFamily="monospace" fill="#6b7280">{d.address}</text>}
                  {router && <text x={x} y={y + 4} textAnchor="middle" fontSize="10" fill="#fff" fontWeight="700">RTR</text>}
                  {d.kind === "switch" && <text x={x} y={y + 4} textAnchor="middle" fontSize="10" fill="#fff" fontWeight="700">SW</text>}
                </g>
              );
            })}
          </svg>
        </div>
        <div className="col-span-2 space-y-4">
          <div className="card p-4 text-sm">
            <div className="label">Devices</div>
            <table className="mt-2 w-full">
              <tbody>
                {(data?.devices ?? []).map((d) => (
                  <tr key={d.name} className="border-t border-ink-100">
                    <td className="py-1 font-mono font-medium">{d.name}</td><td className="py-1 text-ink-600">{d.kind}</td><td className="py-1 text-ink-600">{d.site}</td><td className="py-1 font-mono text-[12px] text-ink-500">{d.address ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card p-4 text-sm">
            <div className="label">Addressing</div>
            <table className="mt-2 w-full font-mono text-[12px]">
              <tbody>
                {[["10.10.10.0/24", "HQ application subnet"], ["10.10.20.0/24", "HQ user subnet"], ["10.10.30.0/30", "NetOps monitoring link"], ["10.255.0.0/30", "WAN transit"], ["10.20.10.0/24", "Austin users"]].map(([p, l]) => (
                  <tr key={p} className="border-t border-ink-100"><td className="py-1">{p}</td><td className="py-1 font-sans text-ink-600">{l}</td></tr>
                ))}
              </tbody>
            </table>
            <div className="mt-3 text-[11px] text-ink-400">Routing: OSPF area 0 between HQ-RTR1 and AUS-RTR1. Open the <Link href="/terminal" className="text-brand-600 underline">terminal</Link> to inspect live state.</div>
          </div>
        </div>
      </div>
    </NeedsSession>
  );
}
