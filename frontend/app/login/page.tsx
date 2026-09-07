"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useStore } from "@/lib/store";

export default function Login() {
  const { login, me } = useStore();
  const router = useRouter();
  const [email, setEmail] = useState("henry@northstar.example");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (me) router.replace("/");
  }, [me, router]);

  return (
    <div className="flex h-full items-center justify-center bg-ink-100">
      <form
        className="card w-[380px] p-6"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(null);
          try {
            await login(email);
            router.replace("/");
          } catch (e2) {
            setErr("Sign-in failed. Is the backend running on port 8000?");
            console.error(e2);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="text-[13px] font-semibold tracking-wide text-ink-900">Northstar Technologies</div>
        <div className="mb-5 text-[12px] text-ink-500">Single sign-on · demo environment</div>
        <label className="label">Work email</label>
        <input className="input mt-1" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        {err && <div className="mt-3 text-sm text-red-700">{err}</div>}
        <button className="btn-primary mt-4 w-full justify-center" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="mt-4 text-[11px] text-ink-400">Demo account: henry@northstar.example</div>
      </form>
    </div>
  );
}
