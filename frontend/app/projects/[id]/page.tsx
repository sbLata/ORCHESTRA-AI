"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, download, subscribe, type Approval, type Snapshot, type Task, type TaskState } from "@/lib/api";
import { AgentTag, Badge, Button, Card, ErrorNote, Markdown, Shell, ago, money, tokens, useSession } from "@/components/ui";
import { GraphLegend, WorkflowGraph } from "@/components/WorkflowGraph";

const TABS = ["plan", "agents", "timeline", "deliverables", "approvals"] as const;
type Tab = (typeof TABS)[number];
const LIVE = ["planning", "running", "paused", "awaiting_plan_approval", "awaiting_approval"];

export default function ProjectPage() {
  const ready = useSession();
  const { id } = useParams<{ id: string }>();
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("plan");
  const [selected, setSelected] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      setSnap(await api<Snapshot>(`/api/projects/${id}`));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  // Run an action, show its error if any, then refresh.
  const act = useCallback(async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
    await load();
  }, [load]);

  useEffect(() => {
    if (!ready) return;
    const wanted = new URLSearchParams(window.location.search).get("tab") as Tab | null;
    if (wanted && TABS.includes(wanted)) setTab(wanted);
    load();
    // Live updates: each server event triggers a (debounced) snapshot refresh.
    const stop = subscribe(id, () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(load, 150);
    });
    const poll = setInterval(load, 8000); // safety net if the stream drops
    return () => { stop(); clearInterval(poll); };
  }, [ready, id, load]);

  if (!ready) return null;
  if (!snap) {
    return <Shell>{error ? <ErrorNote>{error}</ErrorNote> : <p className="text-sm text-muted">Loading project…</p>}</Shell>;
  }

  const { project, tasks, usage } = snap;
  const pending = snap.approvals.filter((a) => a.status === "pending");
  const done = tasks.filter((t) => t.state === "completed").length;
  const task = tasks.find((t) => t.id === selected) ?? null;
  const control = (action: string) => act(() => api(`/api/projects/${id}/control`, { body: { action } }));
  const finalState: TaskState =
    project.status === "completed" ? "completed"
      : snap.deliverables.some((d) => d.kind === "final_report") ? "blocked"
      : tasks.length > 0 && done === tasks.length && project.status === "running" ? "running" : "waiting";
  const spentPct = Math.min(100, (usage.cost_usd / usage.budget_usd) * 100);

  return (
    <Shell>
      <Link href="/" className="text-sm text-muted hover:text-ink">← All projects</Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold">{project.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
            <Badge status={project.status} />
            {tasks.length > 0 && <span>{done}/{tasks.length} tasks done</span>}
            <span className="flex items-center gap-2" title={`${tokens(usage.input_tokens)} input / ${tokens(usage.output_tokens)} output tokens`}>
              {money(usage.cost_usd)} of {money(usage.budget_usd)} budget
              <span className="h-1.5 w-20 overflow-hidden rounded-full bg-idle-soft">
                <span className={`block h-full ${spentPct > 85 ? "bg-bad" : "bg-accent"}`} style={{ width: `${spentPct}%` }} />
              </span>
            </span>
          </div>
        </div>
        <div className="flex gap-2">
          {project.status === "running" && <Button onClick={() => control("pause")}>Pause</Button>}
          {project.status === "paused" && pending.every((a) => a.kind !== "budget") && (
            <Button variant="primary" onClick={() => control("resume")}>Resume</Button>
          )}
          {project.status === "failed" && tasks.length === 0 && (
            <Button variant="primary" onClick={() => control("replan")}>Try planning again</Button>
          )}
          {LIVE.includes(project.status) && (
            <Button variant="danger" onClick={() => { if (window.confirm("Cancel this workflow? Running tasks stop and cannot be resumed.")) control("cancel"); }}>
              Cancel
            </Button>
          )}
        </div>
      </div>

      <div className="mt-4 space-y-3">
        <ErrorNote>{error}</ErrorNote>
        {project.error && <ErrorNote>{project.error}</ErrorNote>}
        {project.status === "planning" && (
          <p className="rounded-lg bg-run-soft px-3 py-2 text-sm text-run">
            The Planning Agent is analyzing your objective and building the task graph…
          </p>
        )}
        {pending.map((a) => (
          <ApprovalCard key={a.id} approval={a}
            onDecide={(approve, note) => act(() => api(`/api/approvals/${a.id}/decide`, { body: { approve, note } }))}
            onView={a.kind === "deliverable" ? () => setTab("deliverables") : a.task_id ? () => { setTab("plan"); setSelected(a.task_id); } : undefined} />
        ))}
      </div>

      <Card className="mt-4 p-4">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">Objective</h2>
        <p className="mt-1 whitespace-pre-wrap text-sm">{project.objective}</p>
        {project.summary && <p className="mt-3 border-t border-line pt-3 text-sm text-muted">{project.summary}</p>}
        <Context snap={snap} onAdd={(note) => act(() => api(`/api/projects/${id}/notes`, { body: { note } }))}
          onReplan={project.status === "awaiting_plan_approval" ? () => control("replan") : undefined} />
      </Card>

      <div role="tablist" className="mt-6 flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((t) => {
          const count = { plan: tasks.length, agents: 0, timeline: 0, deliverables: snap.deliverables.length, approvals: pending.length }[t];
          return (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium capitalize ${tab === t ? "border-accent text-accent" : "border-transparent text-muted hover:text-ink"}`}>
              {t === "plan" ? "Execution plan" : t}{count ? ` (${count})` : ""}
            </button>
          );
        })}
      </div>

      <div className="mt-4">
        {tab === "plan" && (tasks.length === 0 ? <Empty>The execution plan appears here once planning finishes.</Empty> : (
          <div className="space-y-4">
            <Card className="p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-semibold">Workflow</h2>
                <GraphLegend />
              </div>
              <WorkflowGraph tasks={tasks} selected={selected} onSelect={setSelected} finalState={finalState} />
            </Card>
            <Card className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead className="border-b border-line text-left text-xs text-muted">
                  <tr>{["Task", "Agent", "Depends on", "Priority", "Status"].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {tasks.map((t) => (
                    <tr key={t.id} onClick={() => setSelected(t.id)} className={`cursor-pointer hover:bg-idle-soft ${selected === t.id ? "bg-accent-soft" : ""}`}>
                      <td className="px-4 py-2.5">
                        <button className="text-left font-medium" onClick={() => setSelected(t.id)}>
                          <span className="text-muted">{t.key}</span> {t.title}
                        </button>
                        {t.requires_approval && <span className="ml-2 text-xs text-warn">needs authorization</span>}
                      </td>
                      <td className="px-4 py-2.5"><AgentTag agent={t.agent} /></td>
                      <td className="px-4 py-2.5 text-muted">{t.depends_on.join(", ") || "–"}</td>
                      <td className="px-4 py-2.5 capitalize text-muted">{t.priority}</td>
                      <td className="px-4 py-2.5">
                        <Badge status={["pending"].includes(t.status) ? t.state : t.status} />
                        {t.attempts > 1 && <span className="ml-2 text-xs text-muted">attempt {t.attempts}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>
        ))}

        {tab === "agents" && (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {snap.agents.map((a) => (
              <Card key={a.key} className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <AgentTag agent={a.key} name={a.name} />
                  <Badge status={a.status} />
                </div>
                <p className="mt-1 text-sm text-muted">{a.role}</p>
                <div className="mt-3 min-h-10 text-sm">
                  {a.current_tasks.length ? a.current_tasks.map((t) => (
                    <button key={t.id} className="block text-left text-run underline" onClick={() => { setTab("plan"); setSelected(t.id); }}>
                      {a.key === "qa" ? "Reviewing" : "Working on"} {t.key}: {t.title}
                    </button>
                  )) : <span className="text-muted">No current task</span>}
                </div>
                <dl className="mt-3 grid grid-cols-4 gap-2 border-t border-line pt-3 text-center">
                  {[["Tasks", `${a.completed}/${a.assigned}`], ["Retries", a.retries], ["Tokens", tokens(a.usage.input_tokens + a.usage.output_tokens)], ["Cost", money(a.usage.cost_usd)]].map(([k, v]) => (
                    <div key={k}><dt className="text-[11px] text-muted">{k}</dt><dd className="text-sm font-semibold tabular-nums">{v}</dd></div>
                  ))}
                </dl>
                {a.errors.length > 0 && (
                  <details className="mt-3 text-sm">
                    <summary className="cursor-pointer text-warn">{a.errors.length} rejected or failed attempt{a.errors.length > 1 ? "s" : ""}</summary>
                    <ul className="mt-2 space-y-1.5 text-xs text-muted">
                      {a.errors.map((e, i) => <li key={i}><b className="text-ink">{e.task} attempt {e.attempt}</b> ({e.outcome.replace("_", " ")}): {e.detail}</li>)}
                    </ul>
                  </details>
                )}
              </Card>
            ))}
          </div>
        )}

        {tab === "timeline" && (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <h2 className="border-b border-line px-4 py-3 text-sm font-semibold">Activity</h2>
              <ol className="max-h-[36rem] divide-y divide-line overflow-y-auto">
                {snap.events.map((e) => (
                  <li key={e.id} className="flex gap-3 px-4 py-2.5 text-sm">
                    <span className="w-16 shrink-0 text-xs text-muted">{new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                    <span className="min-w-0">{e.agent && <><AgentTag agent={e.agent} /> · </>}{e.message}</span>
                  </li>
                ))}
              </ol>
            </Card>
            <Card>
              <h2 className="border-b border-line px-4 py-3 text-sm font-semibold">Agent messages</h2>
              {snap.messages.length === 0 ? <p className="px-4 py-4 text-sm text-muted">No messages yet.</p> : (
                <ol className="max-h-[36rem] divide-y divide-line overflow-y-auto">
                  {snap.messages.map((m) => (
                    <li key={m.id} className="px-4 py-2.5 text-sm">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                        <AgentTag agent={m.sender} /> <span className="text-muted">→</span> <AgentTag agent={m.recipient} />
                        <span className="rounded bg-idle-soft px-1.5 py-0.5 text-muted">{m.type.replace("_", " ")}</span>
                        <span className="text-muted">{m.status.replace("_", " ")}</span>
                      </div>
                      <p className="mt-1 line-clamp-3 text-muted">{m.content}</p>
                    </li>
                  ))}
                </ol>
              )}
            </Card>
          </div>
        )}

        {tab === "deliverables" && (snap.deliverables.length === 0
          ? <Empty>Deliverables appear here after the team finishes and QA has reviewed the final report.</Empty>
          : (
            <div className="space-y-4">
              {snap.deliverables.map((d) => (
                <Card key={d.id}>
                  <details open={d.kind === "final_report"}>
                    <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-3 px-4 py-3">
                      <span className="min-w-0">
                        <span className="text-xs uppercase tracking-wider text-muted">{d.kind === "final_report" ? "Final report" : "Supporting document"}</span>
                        <span className="block font-medium">{d.title}</span>
                      </span>
                      <span className="flex items-center gap-2">
                        <Badge status={d.status} />
                        <Button onClick={(e) => { e.preventDefault(); act(() => download(d.id, "md")); }}>Export .md</Button>
                        <Button onClick={(e) => { e.preventDefault(); act(() => download(d.id, "html")); }}>Export .html</Button>
                      </span>
                    </summary>
                    <div className="border-t border-line px-4 py-4">
                      {d.qa_notes && <p className="mb-4 rounded-lg bg-idle-soft px-3 py-2 text-xs text-muted">{d.qa_notes}</p>}
                      <Markdown>{d.content ?? ""}</Markdown>
                    </div>
                  </details>
                </Card>
              ))}
            </div>
          ))}

        {tab === "approvals" && (snap.approvals.length === 0 ? <Empty>No approvals have been requested yet.</Empty> : (
          <Card>
            <ul className="divide-y divide-line">
              {snap.approvals.map((a) => (
                <li key={a.id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3 text-sm">
                  <div className="min-w-0">
                    <p className="font-medium">{a.title}</p>
                    <p className="text-xs text-muted">
                      {a.kind} · requested {ago(a.created_at)}{a.decided_at ? ` · decided ${ago(a.decided_at)}` : ""}{a.note ? ` · “${a.note}”` : ""}
                    </p>
                  </div>
                  <Badge status={a.status === "pending" ? "awaiting_approval" : a.status} />
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>

      {task && (
        <TaskPanel key={task.id} task={task} agents={snap.agents.map((a) => [a.key, a.name])}
          canRerun={["running", "paused", "awaiting_approval", "failed", "completed"].includes(project.status)}
          onClose={() => setSelected(null)}
          onSave={(body) => act(() => api(`/api/tasks/${task.id}`, { method: "PATCH", body }))}
          onRetry={() => act(() => api(`/api/tasks/${task.id}/retry`, { body: {} }))} />
      )}
    </Shell>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted">{children}</p>;
}

function ApprovalCard({ approval, onDecide, onView }: {
  approval: Approval; onDecide: (approve: boolean, note: string) => Promise<void>; onView?: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const decide = async (approve: boolean) => { setBusy(true); await onDecide(approve, note); setBusy(false); };
  const wantsNote = ["plan", "deliverable", "escalation"].includes(approval.kind);
  return (
    <div className="rounded-xl border border-warn/40 bg-warn-soft p-4">
      <p className="text-xs font-semibold uppercase tracking-wider text-warn">Needs your decision</p>
      <p className="mt-1 font-medium">{approval.title}</p>
      {approval.details && <p className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap text-sm text-ink/80">{approval.details}</p>}
      {wantsNote && (
        <input value={note} onChange={(e) => setNote(e.target.value)} aria-label="Feedback (optional)"
          placeholder={approval.kind === "escalation" ? "Optional guidance for the retry" : "Optional feedback, used if you reject"}
          className="mt-3 w-full rounded-lg border border-line bg-card px-3 py-1.5 text-sm" />
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="primary" disabled={busy} onClick={() => decide(true)}>{approval.approve_label}</Button>
        <Button disabled={busy} onClick={() => decide(false)}>{approval.reject_label}</Button>
        {onView && <Button variant="ghost" onClick={onView}>{approval.kind === "deliverable" ? "Read it first" : "View task"}</Button>}
      </div>
    </div>
  );
}

function Context({ snap, onAdd, onReplan }: { snap: Snapshot; onAdd: (note: string) => Promise<void>; onReplan?: () => void }) {
  const { questions, assumptions, notes } = snap.project;
  const [note, setNote] = useState("");
  if (!questions.length && !assumptions.length && !notes.length && snap.tasks.length === 0) return null;
  return (
    <details className="mt-3 border-t border-line pt-3 text-sm" open={!!onReplan && questions.length > 0}>
      <summary className="cursor-pointer font-medium">
        Shared project context
        <span className="ml-2 font-normal text-muted">{questions.length} open question{questions.length === 1 ? "" : "s"}, {assumptions.length} assumption{assumptions.length === 1 ? "" : "s"}, {notes.length} note{notes.length === 1 ? "" : "s"} from you</span>
      </summary>
      <div className="mt-3 grid gap-4 md:grid-cols-3">
        {([["Questions from the planner", questions], ["Assumptions made", assumptions], ["Your notes", notes]] as const).map(([title, items]) => (
          <div key={title}>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">{title}</h3>
            {items.length ? <ul className="mt-1 list-disc space-y-1 pl-4">{items.map((q, i) => <li key={i}>{q}</li>)}</ul> : <p className="mt-1 text-muted">None</p>}
          </div>
        ))}
      </div>
      <form className="mt-3 flex flex-wrap gap-2" onSubmit={async (e) => { e.preventDefault(); await onAdd(note); setNote(""); }}>
        <input required value={note} onChange={(e) => setNote(e.target.value)} aria-label="Add context"
          placeholder="Answer a question or add context every agent should know"
          className="min-w-0 flex-1 rounded-lg border border-line bg-paper px-3 py-1.5 text-sm" />
        <Button type="submit">Add note</Button>
        {onReplan && notes.length > 0 && <Button type="button" onClick={onReplan}>Rebuild plan with notes</Button>}
      </form>
    </details>
  );
}

function TaskPanel({ task, agents, canRerun, onClose, onSave, onRetry }: {
  task: Task; agents: string[][]; canRerun: boolean; onClose: () => void;
  onSave: (body: { instructions?: string; agent?: string }) => Promise<void>; onRetry: () => Promise<void>;
}) {
  const [instructions, setInstructions] = useState(task.instructions);
  const [agent, setAgent] = useState(task.agent);
  const busy = ["running", "in_review"].includes(task.status);
  const dirty = instructions !== task.instructions || agent !== task.agent;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside aria-label={`Task ${task.key}`} className="fixed inset-y-0 right-0 z-20 flex w-full max-w-xl flex-col border-l border-line bg-card shadow-2xl">
      <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <p className="text-xs text-muted">{task.key} · priority {task.priority}{task.depends_on.length ? ` · after ${task.depends_on.join(", ")}` : ""}</p>
          <h2 className="font-display text-lg font-semibold">{task.title}</h2>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge status={task.status === "pending" ? task.state : task.status} />
            {task.qa_score != null && <span className="text-xs text-muted">QA score {task.qa_score}</span>}
            {task.attempts > 0 && <span className="text-xs text-muted">{task.attempts} attempt{task.attempts > 1 ? "s" : ""}</span>}
          </div>
        </div>
        <Button variant="ghost" onClick={onClose} aria-label="Close task panel">Close</Button>
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4 text-sm">
        {task.error && <ErrorNote>{task.error}</ErrorNote>}

        <div>
          <label htmlFor="agent" className="text-xs font-semibold uppercase tracking-wider text-muted">Assigned agent</label>
          <select id="agent" value={agent} disabled={busy} onChange={(e) => setAgent(e.target.value)}
            className="mt-1 block w-full rounded-lg border border-line bg-paper px-3 py-1.5">
            {agents.map(([key, name]) => <option key={key} value={key}>{name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="instructions" className="text-xs font-semibold uppercase tracking-wider text-muted">Instructions</label>
          <textarea id="instructions" rows={5} value={instructions} disabled={busy} onChange={(e) => setInstructions(e.target.value)}
            className="mt-1 block w-full rounded-lg border border-line bg-paper px-3 py-2" />
        </div>
        {task.acceptance_criteria && (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Acceptance criteria</h3>
            <p className="mt-1 whitespace-pre-wrap">{task.acceptance_criteria}</p>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={!dirty || busy} onClick={() => onSave({ instructions, agent })}>Save changes</Button>
          {canRerun && task.attempts > 0 && <Button disabled={busy || dirty} onClick={onRetry}>Re-run task</Button>}
          {busy && <span className="self-center text-xs text-muted">Editing is locked while the task runs.</span>}
        </div>

        {task.qa_feedback && (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Latest QA feedback</h3>
            <p className="mt-1 whitespace-pre-wrap rounded-lg bg-warn-soft px-3 py-2">{task.qa_feedback}</p>
          </div>
        )}
        {task.history.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Attempt history</h3>
            <ol className="mt-1 space-y-1">
              {task.history.map((h, i) => (
                <li key={i} className="flex gap-2">
                  <span className={h.outcome === "completed" ? "text-ok" : "text-bad"}>●</span>
                  <span><b>Attempt {h.attempt}</b>: {h.outcome.replace("_", " ")}{h.detail ? `. ${h.detail}` : ""}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Output</h3>
          {task.output ? <div className="mt-2 rounded-lg border border-line p-4"><Markdown>{task.output}</Markdown></div>
            : <p className="mt-1 text-muted">No output yet.</p>}
        </div>
      </div>
    </aside>
  );
}
