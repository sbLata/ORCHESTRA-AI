"use client";

import type { Task, TaskState } from "@/lib/api";
import { AGENT_COLORS } from "./ui";

const W = 208, H = 68, GAP_X = 56, GAP_Y = 14, PAD = 12;

const STATE: Record<TaskState | "objective" | "final", { border: string; bg: string; label: string }> = {
  completed: { border: "var(--color-ok)", bg: "var(--color-ok-soft)", label: "Completed" },
  running: { border: "var(--color-run)", bg: "var(--color-run-soft)", label: "Running" },
  blocked: { border: "var(--color-warn)", bg: "var(--color-warn-soft)", label: "Blocked" },
  failed: { border: "var(--color-bad)", bg: "var(--color-bad-soft)", label: "Failed" },
  waiting: { border: "var(--color-line)", bg: "var(--color-card)", label: "Waiting" },
  objective: { border: "var(--color-ink)", bg: "var(--color-card)", label: "" },
  final: { border: "var(--color-ink)", bg: "var(--color-card)", label: "" },
};

interface Node { id: string; col: number; row: number; x: number; y: number }

/** Layered left-to-right DAG: a task's column is one past its deepest dependency. */
export function WorkflowGraph({
  tasks, selected, onSelect, finalState,
}: { tasks: Task[]; selected: string | null; onSelect: (id: string) => void; finalState: TaskState }) {
  const byKey = new Map(tasks.map((t) => [t.key, t]));
  const depth = new Map<string, number>();
  const depthOf = (key: string, seen = new Set<string>()): number => {
    if (depth.has(key)) return depth.get(key)!;
    if (seen.has(key)) return 1;
    seen.add(key);
    const deps = (byKey.get(key)?.depends_on ?? []).filter((d) => byKey.has(d));
    const d = deps.length ? Math.max(...deps.map((k) => depthOf(k, seen))) + 1 : 1;
    depth.set(key, d);
    return d;
  };
  tasks.forEach((t) => depthOf(t.key));

  const columns: Task[][] = [];
  tasks.forEach((t) => (columns[depth.get(t.key)! - 1] ??= []).push(t));
  const maxRows = Math.max(1, ...columns.map((c) => c.length));
  const height = maxRows * H + (maxRows - 1) * GAP_Y + PAD * 2;
  const colX = (col: number) => PAD + col * (W + GAP_X);
  const rowY = (row: number, count: number) =>
    PAD + (height - PAD * 2 - (count * H + (count - 1) * GAP_Y)) / 2 + row * (H + GAP_Y);

  const nodes = new Map<string, Node>();
  columns.forEach((col, c) =>
    col.forEach((t, r) => nodes.set(t.key, { id: t.id, col: c + 1, row: r, x: colX(c + 1), y: rowY(r, col.length) })));
  const objective = { x: colX(0), y: rowY(0, 1) };
  const final = { x: colX(columns.length + 1), y: rowY(0, 1) };
  const width = final.x + W + PAD;

  const hasDependents = new Set(tasks.flatMap((t) => t.depends_on));
  const edges: { from: { x: number; y: number }; to: { x: number; y: number }; done: boolean; key: string }[] = [];
  tasks.forEach((t) => {
    const to = nodes.get(t.key)!;
    const deps = t.depends_on.filter((d) => nodes.has(d));
    if (deps.length === 0) edges.push({ from: objective, to, done: true, key: `o-${t.key}` });
    deps.forEach((d) =>
      edges.push({ from: nodes.get(d)!, to, done: byKey.get(d)!.state === "completed", key: `${d}-${t.key}` }));
    if (!hasDependents.has(t.key)) {
      edges.push({ from: to, to: final, done: t.state === "completed", key: `${t.key}-f` });
    }
  });

  return (
    <div className="overflow-x-auto">
      <div className="relative" style={{ width, height }}>
        <svg width={width} height={height} className="absolute inset-0" aria-hidden="true">
          {edges.map((e) => {
            const x1 = e.from.x + W, y1 = e.from.y + H / 2, x2 = e.to.x, y2 = e.to.y + H / 2;
            const mid = (x1 + x2) / 2;
            return (
              <path key={e.key} d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`} fill="none"
                stroke={e.done ? "var(--color-ok)" : "var(--color-line)"} strokeWidth={e.done ? 1.75 : 1.5} />
            );
          })}
        </svg>

        <Box x={objective.x} y={objective.y} state="objective">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Start</p>
          <p className="text-sm font-medium">User objective</p>
        </Box>

        {tasks.map((t) => {
          const n = nodes.get(t.key)!;
          const s = STATE[t.state];
          return (
            <button
              key={t.id} onClick={() => onSelect(t.id)} aria-pressed={selected === t.id}
              aria-label={`${t.key} ${t.title}, ${s.label}`}
              className="absolute overflow-hidden rounded-lg border-[1.5px] px-2.5 py-1.5 text-left transition-shadow hover:shadow-md"
              style={{
                left: n.x, top: n.y, width: W, height: H, borderColor: s.border, background: s.bg,
                boxShadow: selected === t.id ? "0 0 0 3px var(--color-accent-soft), 0 0 0 4.5px var(--color-accent)" : undefined,
              }}
            >
              <span className="flex items-center justify-between gap-2 text-[11px]">
                <span className="flex items-center gap-1.5 font-medium text-muted">
                  <span className="size-2 rounded-sm" style={{ background: AGENT_COLORS[t.agent] }} />
                  {t.key} · {t.agent}
                </span>
                <span className="flex items-center gap-1 font-semibold" style={{ color: t.state === "waiting" ? "var(--color-idle)" : s.border }}>
                  {t.state === "running" && <span className="pulse size-1.5 rounded-full bg-current" />}
                  {t.status === "in_review" ? "In QA" : t.status === "awaiting_approval" ? "Needs you" : s.label}
                </span>
              </span>
              <span className="mt-0.5 line-clamp-2 block text-[13px] font-medium leading-snug text-ink">{t.title}</span>
            </button>
          );
        })}

        <Box x={final.x} y={final.y} state={finalState === "waiting" ? "final" : finalState}>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">QA-reviewed</p>
          <p className="text-sm font-medium">Final deliverable</p>
        </Box>
      </div>
    </div>
  );
}

function Box({ x, y, state, children }: { x: number; y: number; state: keyof typeof STATE; children: React.ReactNode }) {
  return (
    <div className="absolute flex flex-col justify-center rounded-lg border-[1.5px] border-dashed px-3"
      style={{ left: x, top: y, width: W, height: H, borderColor: STATE[state].border, background: STATE[state].bg }}>
      {children}
    </div>
  );
}

export function GraphLegend() {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {(["completed", "running", "blocked", "failed", "waiting"] as const).map((s) => (
        <li key={s} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm border-[1.5px]" style={{ borderColor: STATE[s].border, background: STATE[s].bg }} />
          {STATE[s].label}
        </li>
      ))}
    </ul>
  );
}
