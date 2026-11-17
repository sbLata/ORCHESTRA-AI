export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "orchestra.token";

export const auth = {
  get token(): string | null {
    return typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY);
  },
  set(token: string) {
    window.localStorage.setItem(TOKEN_KEY, token);
  },
  clear() {
    window.localStorage.removeItem(TOKEN_KEY);
  },
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: init.method ?? (init.body === undefined ? "GET" : "POST"),
      headers: {
        ...(init.body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(auth.token ? { Authorization: `Bearer ${auth.token}` } : {}),
      },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch {
    throw new ApiError(0, `Cannot reach the ORCHESTRA API at ${API_URL}. Is the backend running?`);
  }
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    auth.clear();
    window.location.href = "/login";
  }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    const detail = data?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d: { loc?: string[]; msg: string }) => `${d.loc?.at(-1) ?? ""}: ${d.msg}`).join("; ")
      : detail ?? `Request failed (${res.status})`;
    throw new ApiError(res.status, message);
  }
  return res.json();
}

/** Subscribe to a project's live event stream. Returns an unsubscribe function. */
export function subscribe(projectId: string, onEvent: () => void): () => void {
  const controller = new AbortController();
  (async () => {
    while (!controller.signal.aborted) {
      try {
        const res = await fetch(`${API_URL}/api/projects/${projectId}/stream`, {
          headers: { Authorization: `Bearer ${auth.token}` },
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error("stream unavailable");
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          if (decoder.decode(value, { stream: true }).includes("data:")) onEvent();
        }
      } catch {
        /* dropped connection: fall through and reconnect */
      }
      if (!controller.signal.aborted) await new Promise((r) => setTimeout(r, 2000));
    }
  })();
  return () => controller.abort();
}

export async function download(deliverableId: string, format: "md" | "html") {
  const res = await fetch(`${API_URL}/api/deliverables/${deliverableId}/export?format=${format}`, {
    headers: { Authorization: `Bearer ${auth.token}` },
  });
  if (!res.ok) throw new ApiError(res.status, "Export failed");
  const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? `deliverable.${format}`;
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------- types

export type ProjectStatus =
  | "planning" | "awaiting_plan_approval" | "running" | "paused"
  | "awaiting_approval" | "completed" | "failed" | "cancelled";

export interface Project {
  id: string; title: string; objective: string; status: ProjectStatus; summary: string;
  questions: string[]; assumptions: string[]; notes: string[]; budget_usd: number; error: string;
  created_at: string; updated_at: string; completed_at: string | null;
}

export type TaskState = "completed" | "running" | "blocked" | "failed" | "waiting";

export interface Attempt { attempt: number; outcome: string; detail: string; at: string }

export interface Task {
  id: string; key: string; title: string; instructions: string; acceptance_criteria: string;
  agent: string; depends_on: string[]; priority: string; requires_approval: boolean;
  is_deliverable: boolean; status: string; state: TaskState; output: string; qa_feedback: string;
  qa_score: number | null; error: string; attempts: number; history: Attempt[];
  started_at: string | null; finished_at: string | null;
}

export interface Approval {
  id: string; project_id: string; task_id: string | null; kind: string; title: string; details: string;
  approve_label: string; reject_label: string; status: "pending" | "approved" | "rejected"; note: string;
  created_at: string; decided_at: string | null; project_title?: string;
}

export interface Deliverable {
  id: string; project_id: string; kind: "final_report" | "supporting"; title: string;
  status: "draft" | "approved" | "rejected"; qa_notes: string; created_at: string; content?: string;
  project_title?: string;
}

export interface AgentUsage { calls: number; input_tokens: number; output_tokens: number; cost_usd: number }

export interface AgentInfo {
  key: string; name: string; role: string; status: "working" | "idle";
  current_tasks: { id: string; key: string; title: string }[];
  assigned: number; completed: number; retries: number;
  errors: (Attempt & { task: string })[]; usage: AgentUsage;
}

export interface TimelineEvent {
  id: number; kind: string; agent: string | null; message: string; task_key: string | null; created_at: string;
}

export interface AgentMessage {
  id: string; sender: string; recipient: string; type: string; content: string;
  output_ref: string | null; status: string; task_key: string | null; created_at: string;
}

export interface Snapshot {
  project: Project; tasks: Task[]; agents: AgentInfo[]; approvals: Approval[]; deliverables: Deliverable[];
  events: TimelineEvent[]; messages: AgentMessage[];
  usage: { cost_usd: number; budget_usd: number; input_tokens: number; output_tokens: number };
}

export interface Dashboard {
  projects: (Project & { tasks_done: number; tasks_total: number })[];
  pending_approvals: Approval[];
  recent_deliverables: Deliverable[];
  stats: {
    active: number; running: number; completed: number; tasks_completed: number;
    input_tokens: number; output_tokens: number; cost_usd: number;
    workflow_completion_rate: number | null; qa_rejection_rate: number | null;
  };
}

export interface Meta {
  llm: string; model: string; simulated: boolean;
  agents: Record<string, { name: string; role: string }>;
  limits: { max_attempts: number; max_concurrency: number };
}
