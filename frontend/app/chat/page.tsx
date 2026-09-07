"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { NeedsSession } from "@/components/ui";

export default function ChatIndex() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/chat/dm-maya");
  }, [router]);
  return <NeedsSession><div className="p-6 text-sm text-ink-500">Opening inbox…</div></NeedsSession>;
}
