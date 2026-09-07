"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Avatar, ErrorBox, NeedsSession } from "@/components/ui";
import { api, fmtTime, PEOPLE, type Conversation, type Message } from "@/lib/api";
import { usePoll, useStore } from "@/lib/store";

export default function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const { session, me } = useStore();
  const convs = usePoll(() => (session ? api<Conversation[]>("/api/conversations") : Promise.resolve([])), 6000, [session?.id]);
  const thread = usePoll(() => (session ? api<Conversation & { messages: Message[] }>(`/api/conversations/${id}`) : Promise.resolve(null)), 3000, [session?.id, id]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const count = thread.data?.messages.length ?? 0;
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [count, id]);

  const send = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setErr(null);
    const body = text;
    setText("");
    try {
      const r = await api<{ message: Message; replies: Message[] }>(`/api/conversations/${id}/messages`, { method: "POST", json: { body } });
      thread.setData((t) => (t ? { ...t, messages: [...t.messages, r.message, ...r.replies] } : t));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setText(body);
    } finally {
      setBusy(false);
    }
  };

  const other = thread.data?.kind === "dm" ? thread.data.participant_ids.find((p) => p !== me?.user.id) : null;

  return (
    <NeedsSession>
      <div className="flex h-full">
        <div className="w-60 shrink-0 border-r border-ink-200 bg-white">
          <div className="label px-4 pb-1 pt-4">Channels</div>
          {(convs.data ?? []).filter((c) => c.kind === "channel").map((c) => (
            <ConvLink key={c.id} c={c} active={c.id === id} />
          ))}
          <div className="label px-4 pb-1 pt-4">Direct messages</div>
          {(convs.data ?? []).filter((c) => c.kind === "dm").map((c) => (
            <ConvLink key={c.id} c={c} active={c.id === id} />
          ))}
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-12 items-center gap-3 border-b border-ink-200 bg-white px-4">
            {other && <Avatar id={other} />}
            <div>
              <div className="text-sm font-semibold">{thread.data?.name ?? id}</div>
              {other && <div className="text-[11px] text-ink-500">{PEOPLE[other]?.title}</div>}
              {thread.data?.kind === "channel" && <div className="text-[11px] text-ink-500">Mention @maya, @priya or @carlos to get their attention</div>}
            </div>
          </div>
          <ErrorBox msg={err} />
          <div className="flex-1 overflow-y-auto px-4 py-3">
            {(thread.data?.messages ?? []).map((m) => (
              <div key={m.id} className="group flex gap-3 py-1.5 hover:bg-ink-50">
                <Avatar id={m.sender_id} />
                <div className="min-w-0">
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm font-semibold">{PEOPLE[m.sender_id]?.name ?? m.sender_id}</span>
                    <span className="text-[11px] text-ink-400">{fmtTime(m.sent_at)}</span>
                  </div>
                  <div className="whitespace-pre-wrap text-sm text-ink-800">{m.body}</div>
                </div>
              </div>
            ))}
            {busy && other && <div className="px-10 py-1 text-[12px] italic text-ink-400">{PEOPLE[other]?.name.split(" ")[0]} is typing…</div>}
            <div ref={bottom} />
          </div>
          <div className="border-t border-ink-200 bg-white p-3">
            <textarea
              className="input h-20 resize-none font-sans"
              placeholder={other ? `Message ${PEOPLE[other]?.name}… (Enter to send, Shift+Enter for newline)` : "Message #network-ops…"}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="mt-2 flex items-center justify-between">
              <span className="text-[11px] text-ink-400">SEV-2: keep Maya updated every 30 minutes.</span>
              <button className="btn-primary" onClick={send} disabled={busy || !text.trim()}>Send</button>
            </div>
          </div>
        </div>
      </div>
    </NeedsSession>
  );
}

function ConvLink({ c, active }: { c: Conversation; active: boolean }) {
  const other = c.kind === "dm" ? c.participant_ids.find((p) => p !== "henry") : null;
  return (
    <Link href={`/chat/${c.id}`} className={`flex items-center gap-2 px-4 py-1.5 text-sm ${active ? "bg-brand-100 text-brand-600" : "hover:bg-ink-50"}`}>
      {other ? <Avatar id={other} size={5} /> : <span className="w-5 text-center font-mono text-ink-400">#</span>}
      <span className="truncate">{c.kind === "channel" ? c.name.replace(/^#/, "") : c.name}</span>
    </Link>
  );
}
