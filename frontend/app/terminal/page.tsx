"use client";
import { useEffect, useRef, useState } from "react";
import { NeedsSession, PageHeader } from "@/components/ui";
import { api, wsUrl, type Verification } from "@/lib/api";
import { useStore } from "@/lib/store";

/**
 * Line-oriented terminal over the backend WebSocket. The server owns the
 * prompt and executes complete lines; local editing (backspace, history,
 * Ctrl-C/Ctrl-L) happens here so the wire protocol stays tiny.
 */
export default function TerminalPage() {
  const { session, refresh } = useStore();
  const host = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"connecting" | "open" | "closed">("connecting");
  const [verify, setVerify] = useState<Verification | null>(null);
  const [verifying, setVerifying] = useState(false);

  useEffect(() => {
    if (!session || !host.current) return;
    let disposed = false;
    let ws: WebSocket | null = null;
    let cleanup = () => {};
    (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]);
      await import("@xterm/xterm/css/xterm.css");
      if (disposed || !host.current) return;
      const term = new Terminal({
        cursorBlink: true,
        fontFamily: "JetBrains Mono, Menlo, monospace",
        fontSize: 13,
        theme: { background: "#0b1020", foreground: "#e5e7eb", cursor: "#93c5fd" },
        scrollback: 5000,
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(host.current);
      const refit = () => {
        try {
          fit.fit();
        } catch {
          /* container not laid out yet */
        }
      };
      requestAnimationFrame(refit);
      const ro = new ResizeObserver(refit);
      ro.observe(host.current);
      const onResize = refit;
      window.addEventListener("resize", onResize);

      let line = "";
      let prompt = "";
      const history: string[] = [];
      let hIdx = -1;

      ws = new WebSocket(wsUrl(`/api/sessions/${session.id}/terminal`));
      ws.onopen = () => setStatus("open");
      ws.onclose = () => {
        setStatus("closed");
        term.write("\r\n[connection closed]\r\n");
      };
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data) as { type: string; data: string };
        if (m.type === "output") term.write(m.data.replace(/\r?\n/g, "\r\n"));
        if (m.type === "prompt") {
          prompt = m.data;
          term.write("\x1b[1;36m" + prompt + "\x1b[0m");
        }
      };
      const redraw = () => {
        term.write("\r\x1b[K" + "\x1b[1;36m" + prompt + "\x1b[0m" + line);
      };
      term.onData((d) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        for (const ch of d) {
          const code = ch.charCodeAt(0);
          if (ch === "\r") {
            term.write("\r\n");
            ws.send(JSON.stringify({ type: "input", data: line }));
            if (line.trim()) history.unshift(line);
            hIdx = -1;
            line = "";
          } else if (code === 127 || code === 8) {
            if (line.length) {
              line = line.slice(0, -1);
              term.write("\b \b");
            }
          } else if (code === 3) {
            term.write("^C\r\n");
            line = "";
            ws.send(JSON.stringify({ type: "input", data: "" }));
          } else if (code === 12) {
            term.clear();
            redraw();
          } else if (code === 21) {
            line = "";
            redraw();
          } else if (ch === "\x1b[A" || d === "\x1b[A") {
            if (history.length && hIdx < history.length - 1) {
              hIdx += 1;
              line = history[hIdx];
              redraw();
            }
            return;
          } else if (ch === "\x1b[B" || d === "\x1b[B") {
            hIdx = Math.max(-1, hIdx - 1);
            line = hIdx === -1 ? "" : history[hIdx];
            redraw();
            return;
          } else if (code >= 32) {
            line += ch;
            term.write(ch);
          }
        }
      });
      cleanup = () => {
        window.removeEventListener("resize", onResize);
        ro.disconnect();
        ws?.close();
        term.dispose();
      };
    })();
    return () => {
      disposed = true;
      cleanup();
    };
  }, [session?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const runVerify = async () => {
    if (!session) return;
    setVerifying(true);
    try {
      setVerify(await api<Verification>(`/api/sessions/${session.id}/verify`, { method: "POST" }));
      await refresh();
    } finally {
      setVerifying(false);
    }
  };

  return (
    <NeedsSession>
      <div className="flex h-full flex-col">
        <PageHeader
          title="Terminal"
          subtitle="NetOps jump host · ssh to devices by name (e.g. ssh AUS-RTR1)"
          right={
            <div className="flex items-center gap-2">
              <span className={status === "open" ? "pill-ok" : status === "closed" ? "pill-crit" : "pill-muted"}>{status}</span>
              <button className="btn-primary" onClick={runVerify} disabled={verifying || !session}>{verifying ? "Verifying…" : "Run verification"}</button>
            </div>
          }
        />
        <div className="grid min-h-0 flex-1 grid-cols-4">
          <div className="col-span-3 min-h-0 bg-ink-950 p-2">
            <div ref={host} className="h-full w-full" />
          </div>
          <div className="min-h-0 overflow-y-auto border-l border-ink-200 bg-white p-4 text-sm">
            <div className="label">Devices</div>
            <ul className="mt-1 font-mono text-[12px] text-ink-700">
              {["HQ-RTR1", "AUS-RTR1", "AUS-SW1", "APP-SRV", "HQ-CLIENT", "AUS-CLIENT1", "AUS-CLIENT2"].map((d) => <li key={d}>ssh {d}</li>)}
            </ul>
            <div className="mt-4 label">Verification</div>
            {!verify && <div className="mt-1 text-[12px] text-ink-500">Runs the incident’s acceptance checks against the live lab. Deterministic; run it as often as you like.</div>}
            {verify && (
              <div className="mt-1">
                <div className={`text-sm font-semibold ${verify.scenario_success ? "text-ok" : "text-crit"}`}>{verify.scenario_success ? "Service restored" : "Not resolved"}</div>
                {["resolution", "regression", "quality"].map((cat) => (
                  <div key={cat} className="mt-2">
                    <div className="text-[11px] font-semibold uppercase text-ink-500">{cat} · {verify.categories[cat]?.passed ?? 0} pass / {verify.categories[cat]?.failed ?? 0} fail</div>
                    <ul className="mt-0.5 space-y-0.5">
                      {verify.checks.filter((c) => c.category === cat).map((c) => (
                        <li key={c.id} className="flex gap-1.5 text-[12px]">
                          <span className={`font-mono ${c.passed ? "text-ok" : "text-crit"}`}>{c.passed ? "PASS" : "FAIL"}</span>
                          <span className="text-ink-700">{c.detail}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </NeedsSession>
  );
}
