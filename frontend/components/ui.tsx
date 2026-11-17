"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, auth, type Meta } from "@/lib/api";

type Tone = "ok" | "run" | "warn" | "bad" | "idle" | "accent";

const TONES: Record<Tone, string> = {
  ok: "bg-ok-soft text-ok",
  run: "bg-run-soft text-run",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
  idle: "bg-idle-soft text-idle",
  accent: "bg-accent-soft text-accent",
};

const STATUS: Record<string, [string, Tone]> = {
  // project
  planning: ["Planning", "run"],
  awaiting_plan_approval: ["Plan needs approval", "warn"],
  running: ["Running", "run"],
  paused: ["Paused", "idle"],
  awaiting_approval: ["Needs your decision", "warn"],
  completed: ["Completed", "ok"],
  failed: ["Failed", "bad"],
  cancelled: ["Cancelled", "idle"],
  // task
  pending: ["Queued", "idle"],
  waiting: ["Waiting", "idle"],
  blocked: ["Blocked", "warn"],
  in_review: ["In QA review", "run"],
  needs_revision: ["Revising", "run"],
  skipped: ["Skipped", "idle"],
  // approvals / deliverables / agents
  approved: ["Approved", "ok"],
  rejected: ["Rejected", "bad"],
  draft: ["Awaiting approval", "warn"],
  working: ["Working", "run"],
  idle: ["Idle", "idle"],
};

export function Badge({ status, label }: { status: string; label?: string }) {
  const [text, tone] = STATUS[status] ?? [status.replaceAll("_", " "), "idle"];
  const live = tone === "run";
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ${TONES[tone]}`}>
      <span className={`size-1.5 rounded-full bg-current ${live ? "pulse" : ""}`} />
      {label ?? text}
    </span>
  );
}

export const AGENT_COLORS: Record<string, string> = {
  research: "#2563a8",
  planning: "#7c4a9e",
  execution: "#1f5f5b",
  qa: "#a8742a",
  supervisor: "#8a3b4a",
  orchestrator: "#1d1b16",
  user: "#6b665c",
};

export function AgentTag({ agent, name }: { agent: string; name?: string }) {
  const label = name ?? agent[0].toUpperCase() + agent.slice(1);
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium text-ink">
      <span className="size-2 rounded-sm" style={{ background: AGENT_COLORS[agent] ?? "#6b665c" }} />
      {label}
    </span>
  );
}

export function Button({
  variant = "secondary", className = "", ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const styles = {
    primary: "bg-accent text-white hover:bg-accent/90",
    secondary: "border border-line bg-card text-ink hover:bg-idle-soft",
    danger: "border border-bad/30 bg-card text-bad hover:bg-bad-soft",
    ghost: "text-muted hover:bg-idle-soft hover:text-ink",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    />
  );
}

export function Card({ className = "", children }: { className?: string; children: React.ReactNode }) {
  return <section className={`rounded-xl border border-line bg-card ${className}`}>{children}</section>;
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-doc">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  if (!children) return null;
  return <p role="alert" className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">{children}</p>;
}

export function ago(iso: string | null): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export const money = (n: number) => `$${n < 1 ? n.toFixed(3) : n.toFixed(2)}`;
export const tokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : `${n}`);

/** Redirects to /login when there is no session; returns true once it is safe to render. */
export function useSession(): boolean {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (auth.token) setReady(true);
    else router.replace("/login");
  }, [router]);
  return ready;
}

export function useMeta(): Meta | null {
  const [meta, setMeta] = useState<Meta | null>(null);
  useEffect(() => {
    api<Meta>("/api/meta").then(setMeta).catch(() => {});
  }, []);
  return meta;
}

export function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const meta = useMeta();
  return (
    <>
      <header className="border-b border-line bg-card">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link href="/" className="font-display text-lg font-semibold tracking-wide">
            ORCHESTRA <span className="text-brass">AI</span>
          </Link>
          <div className="flex items-center gap-3">
            {meta && <span className="hidden text-xs text-muted sm:inline">Engine: {meta.model}</span>}
            <Button variant="ghost" onClick={() => { auth.clear(); router.replace("/login"); }}>
              Sign out
            </Button>
          </div>
        </div>
      </header>
      {meta?.simulated && (
        <p className="border-b border-warn/20 bg-warn-soft px-4 py-2 text-center text-xs text-warn">
          Simulated agents: no model API credentials were found, so outputs are placeholders. Set
          ANTHROPIC_API_KEY in backend/.env and restart the backend for real work.
        </p>
      )}
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>
    </>
  );
}
