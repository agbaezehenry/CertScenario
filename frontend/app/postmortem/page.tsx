"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorBox, NeedsSession, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

const FIELDS: [keyof Form, string, string][] = [
  ["impact", "Impact", "Who was affected, what could they not do, for how long."],
  ["timeline", "Timeline", "Key times: assignment, first evidence, change, verification. Use HH:MM."],
  ["root_cause", "Root Cause", "The specific configuration state that caused the symptom, on which device."],
  ["resolution", "Resolution", "What you changed and how you verified it."],
  ["contributing_factors", "Contributing Factors", "Why the cause was introduced and why it wasn't caught."],
  ["preventative_actions", "Preventative Actions", "Concrete checks, alerts or process changes."],
];
interface Form { impact: string; timeline: string; root_cause: string; resolution: string; contributing_factors: string; preventative_actions: string }

export default function PostmortemPage() {
  const { session, refresh } = useStore();
  const router = useRouter();
  const [form, setForm] = useState<Form>({ impact: "", timeline: "", root_cause: "", resolution: "", contributing_factors: "", preventative_actions: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (session) api<Form | null>(`/api/sessions/${session.id}/postmortem`).then((pm) => pm && setForm(pm)).catch(() => {});
  }, [session]);
  return (
    <NeedsSession>
      <PageHeader title="Postmortem — INC-1042" subtitle="Blameless. Technical claims are checked against the incident record." />
      <ErrorBox msg={err} />
      <div className="p-6">
        <form
          className="card max-w-3xl p-6"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!session) return;
            setBusy(true);
            setErr(null);
            try {
              await api(`/api/sessions/${session.id}/postmortem`, { method: "POST", json: form });
              setSaved(true);
              await refresh();
              router.push("/scorecard");
            } catch (e2) {
              setErr(e2 instanceof Error ? e2.message : String(e2));
            } finally {
              setBusy(false);
            }
          }}
        >
          {FIELDS.map(([k, label, hint]) => (
            <div key={k} className="mb-4">
              <label className="text-sm font-semibold">{label}</label>
              <div className="text-[11px] text-ink-500">{hint}</div>
              <textarea className="input mt-1 h-24" value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} required={k !== "contributing_factors" && k !== "preventative_actions"} />
            </div>
          ))}
          <div className="flex items-center justify-end gap-3">
            {saved && <span className="text-sm text-ok">Saved</span>}
            <button className="btn-primary" disabled={busy}>{busy ? "Submitting…" : "Submit postmortem"}</button>
          </div>
        </form>
      </div>
    </NeedsSession>
  );
}
