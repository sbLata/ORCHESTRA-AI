"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type Dashboard, type Project } from "@/lib/api";
import { Badge, Button, Card, ErrorNote, Shell, ago, money, tokens, useSession } from "@/components/ui";

const EXAMPLE =
  "Research the market for launching a subscription-based AI productivity application in the United States. " +
  "Develop a business plan and a six-month launch strategy.";

export default function DashboardPage() {
  const ready = useSession();
  const router = useRouter();
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [objective, setObjective] = useState("");
  const [budget, setBudget] = useState("20");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Dashboard>("/api/dashboard").then(setData).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!ready) return;
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [ready, load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const project = await api<Project>("/api/projects", {
        body: { objective, budget_usd: Number(budget) || undefined },
      });
      router.push(`/projects/${project.id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  if (!ready) return null;
  const stats = data?.stats;
  const active = data?.projects.filter((p) => !["completed", "failed", "cancelled"].includes(p.status)) ?? [];
  const finished = data?.projects.filter((p) => ["completed", "failed", "cancelled"].includes(p.status)) ?? [];
  const pct = (n: number | null | undefined) => (n == null ? "–" : `${Math.round(n * 100)}%`);

  return (
    <Shell>
      <Card className="p-5">
        <form onSubmit={create}>
          <label htmlFor="objective" className="font-display text-xl font-semibold">
            What do you want done?
          </label>
          <p className="mt-1 text-sm text-muted">
            Describe the outcome. The orchestrator plans the work, you approve the plan, and the agent team executes it.
          </p>
          <textarea
            id="objective" required minLength={10} rows={3} value={objective}
            onChange={(e) => setObjective(e.target.value)} placeholder={EXAMPLE}
            className="mt-3 w-full rounded-lg border border-line bg-paper px-3 py-2 text-sm"
          />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-4 text-sm text-muted">
              <label className="flex items-center gap-2">
                Budget
                <span className="flex items-center rounded-lg border border-line bg-paper pl-2">
                  $
                  <input type="number" min={1} max={1000} step={1} value={budget}
                    onChange={(e) => setBudget(e.target.value)}
                    className="w-16 bg-transparent px-1 py-1 text-ink" />
                </span>
              </label>
              <button type="button" className="text-accent underline" onClick={() => setObjective(EXAMPLE)}>
                Use the example
              </button>
            </div>
            <Button variant="primary" type="submit" disabled={busy} className="px-4 py-2">
              {busy ? "Creating…" : "Plan this objective"}
            </Button>
          </div>
        </form>
      </Card>

      <div className="mt-4"><ErrorNote>{error}</ErrorNote></div>

      <dl className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {[
          ["Active projects", stats?.active ?? "–"],
          ["Running now", stats?.running ?? "–"],
          ["Completed", stats?.completed ?? "–"],
          ["Workflow completion", pct(stats?.workflow_completion_rate)],
          ["QA rejection rate", pct(stats?.qa_rejection_rate)],
          ["Model spend", stats ? money(stats.cost_usd) : "–"],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border border-line bg-card px-4 py-3">
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="mt-1 text-2xl font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {stats && (
        <p className="mt-2 text-xs text-muted">
          {tokens(stats.input_tokens)} input and {tokens(stats.output_tokens)} output tokens across{" "}
          {stats.tasks_completed} completed tasks.
        </p>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <ProjectList title="Active projects" projects={active}
            empty="No active projects. Describe an objective above to start one." />
          {finished.length > 0 && <ProjectList title="Finished projects" projects={finished} empty="" />}
        </div>

        <div className="space-y-6">
          <Card>
            <h2 className="border-b border-line px-4 py-3 text-sm font-semibold">
              Pending approvals{data && data.pending_approvals.length > 0 ? ` (${data.pending_approvals.length})` : ""}
            </h2>
            {data?.pending_approvals.length ? (
              <ul className="divide-y divide-line">
                {data.pending_approvals.map((a) => (
                  <li key={a.id}>
                    <Link href={`/projects/${a.project_id}`} className="block px-4 py-3 hover:bg-idle-soft">
                      <p className="text-sm font-medium">{a.title}</p>
                      <p className="mt-0.5 truncate text-xs text-muted">{a.project_title}</p>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-4 py-4 text-sm text-muted">Nothing is waiting on you.</p>
            )}
          </Card>

          <Card>
            <h2 className="border-b border-line px-4 py-3 text-sm font-semibold">Recent deliverables</h2>
            {data?.recent_deliverables.length ? (
              <ul className="divide-y divide-line">
                {data.recent_deliverables.map((d) => (
                  <li key={d.id}>
                    <Link href={`/projects/${d.project_id}?tab=deliverables`} className="block px-4 py-3 hover:bg-idle-soft">
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-sm font-medium">{d.title}</p>
                        <Badge status={d.status} />
                      </div>
                      <p className="mt-0.5 text-xs text-muted">{ago(d.created_at)}</p>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-4 py-4 text-sm text-muted">Deliverables appear here when a workflow finishes.</p>
            )}
          </Card>
        </div>
      </div>
    </Shell>
  );
}

function ProjectList({ title, projects, empty }: { title: string; projects: Dashboard["projects"]; empty: string }) {
  return (
    <Card>
      <h2 className="border-b border-line px-4 py-3 text-sm font-semibold">{title}</h2>
      {projects.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted">{empty}</p>
      ) : (
        <ul className="divide-y divide-line">
          {projects.map((p) => (
            <li key={p.id}>
              <Link href={`/projects/${p.id}`} className="block px-4 py-3 hover:bg-idle-soft">
                <div className="flex items-start justify-between gap-3">
                  <p className="min-w-0 font-medium">{p.title}</p>
                  <Badge status={p.status} />
                </div>
                <div className="mt-2 flex items-center gap-3">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-idle-soft">
                    <div className="h-full rounded-full bg-accent"
                      style={{ width: `${p.tasks_total ? (p.tasks_done / p.tasks_total) * 100 : 0}%` }} />
                  </div>
                  <span className="whitespace-nowrap text-xs tabular-nums text-muted">
                    {p.tasks_total ? `${p.tasks_done}/${p.tasks_total} tasks` : "planning"} · {ago(p.updated_at)}
                  </span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
