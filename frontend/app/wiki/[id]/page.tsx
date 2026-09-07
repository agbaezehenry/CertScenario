"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { NeedsSession, PageHeader } from "@/components/ui";
import { api, type WikiPage } from "@/lib/api";
import { useStore } from "@/lib/store";

export default function WikiPageView() {
  const { id } = useParams<{ id: string }>();
  const { session } = useStore();
  const [page, setPage] = useState<WikiPage | null>(null);
  useEffect(() => {
    if (session) api<WikiPage>(`/api/wiki/${id}`).then(setPage).catch(() => setPage(null));
  }, [id, session]);
  return (
    <NeedsSession>
      <PageHeader title={page?.title ?? id} subtitle={page ? `Owner ${page.owner ?? "—"} · last reviewed ${page.last_reviewed ?? "—"}` : undefined} right={<Link href="/wiki" className="btn-ghost">All pages</Link>} />
      <div className="p-6">
        <div className="card max-w-4xl p-6">
          {page?.body && (
            <div className="prose-wiki text-sm">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children }) => <Link href={href?.startsWith("http") ? href : `/wiki/${href}`}>{children}</Link>,
                }}
              >
                {page.body}
              </ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </NeedsSession>
  );
}
