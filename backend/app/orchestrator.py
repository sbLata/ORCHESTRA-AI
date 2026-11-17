"""Central orchestrator: planning, dependency-aware concurrent execution, QA, recovery, approvals.

One asyncio "run loop" exists per running project. It starts every task whose dependencies are
satisfied, lets tasks run concurrently (bounded by a global semaphore), and parks the project when
it needs a human decision. All state lives in the database, so loops can be resumed after a restart.
"""
import asyncio
import logging

from sqlalchemy import func, select

from . import config
from .agents import AGENTS, PLAN_SCHEMA, REVIEW_SCHEMA
from .llm import LLMError, LLMProvider, LLMResult
from .models import Approval, Deliverable, Event, Message, Project, SessionLocal, Task, Usage, now

log = logging.getLogger("orchestra")

DONE = {"completed", "skipped"}  # statuses that satisfy a dependency


class BudgetExceeded(Exception):
    pass


class EventBus:
    """In-process pub/sub that feeds the per-project SSE streams."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, project_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.setdefault(project_id, set()).add(q)
        return q

    def unsubscribe(self, project_id: str, q: asyncio.Queue) -> None:
        self._subs.get(project_id, set()).discard(q)

    def publish(self, project_id: str, payload: dict) -> None:
        for q in list(self._subs.get(project_id, ())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # slow consumer; it resyncs from the snapshot endpoint


class Orchestrator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.bus = EventBus()
        self._loops: dict[str, asyncio.Task] = {}
        self._wake: dict[str, asyncio.Event] = {}
        self._background: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(config.MAX_CONCURRENCY)

    # ------------------------------------------------------------------ records

    def emit(self, project_id: str, kind: str, message: str, agent: str | None = None,
             task_id: str | None = None) -> None:
        with SessionLocal() as db:
            event = Event(project_id=project_id, kind=kind, message=message, agent=agent, task_id=task_id)
            db.add(event)
            db.commit()
            payload = {"id": event.id, "kind": kind, "message": message, "agent": agent, "task_id": task_id}
        self.bus.publish(project_id, payload)

    def send(self, project_id: str, sender: str, recipient: str, type_: str, content: str,
             task_id: str | None = None, output_ref: str | None = None, status: str = "delivered") -> None:
        with SessionLocal() as db:
            db.add(Message(project_id=project_id, task_id=task_id, sender=sender, recipient=recipient,
                           type=type_, content=content, output_ref=output_ref, status=status))
            db.commit()

    def _set_project(self, project_id: str, **fields) -> None:
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            for k, v in fields.items():
                setattr(project, k, v)
            db.commit()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _call(self, project_id: str, task_id: str | None, agent: str, kind: str, prompt: str,
                    schema: dict | None = None, effort: str | None = None, meta: dict | None = None) -> LLMResult:
        with SessionLocal() as db:
            spent = db.scalar(select(func.coalesce(func.sum(Usage.cost_usd), 0.0))
                              .where(Usage.project_id == project_id))
            budget = db.get(Project, project_id).budget_usd
        if spent >= budget:
            raise BudgetExceeded(f"Spent ${spent:.2f} of the ${budget:.2f} budget")
        result = await self.provider.complete(agent=agent, kind=kind, prompt=prompt, schema=schema,
                                              effort=effort or config.WORKER_EFFORT, meta=meta)
        with SessionLocal() as db:
            db.add(Usage(project_id=project_id, task_id=task_id, agent=agent, model=result.model,
                         input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                         cost_usd=result.cost_usd))
            db.commit()
        return result

    # ------------------------------------------------------------------ planning

    def start_planning(self, project_id: str) -> None:
        self._spawn(self._plan(project_id))

    async def _plan(self, project_id: str) -> None:
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            objective, notes = project.objective, list(project.notes)
            project.status, project.error = "planning", ""
            for t in db.scalars(select(Task).where(Task.project_id == project_id)):
                db.delete(t)
            for a in db.scalars(select(Approval).where(Approval.project_id == project_id,
                                                       Approval.status == "pending")):
                a.status, a.note, a.decided_at = "rejected", "Superseded by a new plan", now()
            db.commit()
        self.emit(project_id, "planning_started", "Analyzing the objective and building an execution plan", "planning")
        self.send(project_id, "orchestrator", "planning", "assignment", "Decompose the objective into a task graph.")
        team = "\n".join(f"- {k}: {AGENTS[k]['role']}" for k in PLAN_SCHEMA["properties"]["tasks"]["items"]
                         ["properties"]["agent"]["enum"])
        prompt = (
            f"# Objective\n{objective}\n\n"
            + (f"# Additional context from the user\n" + "\n".join(f"- {n}" for n in notes) + "\n\n" if notes else "")
            + f"# Available agents\n{team}\n\n"
            "# What to produce\n"
            "Break the objective into the smallest set of tasks that fully delivers it "
            f"(at most {config.MAX_TASKS}). Each task goes to exactly one agent and must be completable "
            "from the objective plus the outputs of the tasks it depends on. Make tasks independent "
            "wherever possible so they can run concurrently, and list a dependency only when a task "
            "truly needs another's output. Give each task instructions a specialist could act on "
            "without seeing this conversation, and acceptance criteria a reviewer can check.\n\n"
            "Every task output is reviewed automatically and a final report is consolidated "
            "automatically, so do not add tasks for those. Set requires_approval for any task that "
            "prepares an external or irreversible action (sending messages, publishing, spending money, "
            "changing production systems, deleting data, sharing confidential information). Set "
            "is_deliverable for outputs the user will want as standalone documents. Ask clarifying "
            "questions only when the answer would change the plan; otherwise record an assumption."
        )
        try:
            result = await self._call(project_id, None, "planning", "plan", prompt, PLAN_SCHEMA,
                                      meta={"objective": objective})
            plan = result.data or {}
            tasks = self._validate_plan(plan.get("tasks", []))
            if not tasks:
                raise LLMError("The plan contained no usable tasks.", retryable=False)
        except (LLMError, BudgetExceeded) as e:
            self._set_project(project_id, status="failed", error=str(e))
            self.emit(project_id, "planning_failed", f"Planning failed: {e}", "planning")
            return
        except Exception as e:  # unexpected: record it rather than leaving the project stuck
            log.exception("planning crashed")
            self._set_project(project_id, status="failed", error=f"Internal error: {e}")
            self.emit(project_id, "planning_failed", "Planning failed with an internal error", "planning")
            return

        with SessionLocal() as db:
            project = db.get(Project, project_id)
            project.title = (plan.get("title") or project.title)[:200]
            project.summary = plan.get("summary", "")
            project.questions = plan.get("clarifying_questions", [])
            project.assumptions = plan.get("assumptions", [])
            project.status = "awaiting_plan_approval"
            for i, t in enumerate(tasks):
                db.add(Task(project_id=project_id, position=i, **t))
            db.add(Approval(project_id=project_id, kind="plan", title="Review the execution plan",
                            details=f"{len(tasks)} tasks planned. Approve to start execution, or reject "
                                    "with feedback to have the plan rebuilt.",
                            approve_label="Approve and start", reject_label="Reject and replan"))
            db.commit()
        self.send(project_id, "planning", "orchestrator", "result", f"Plan with {len(tasks)} tasks.", status="completed")
        self.emit(project_id, "plan_ready", f"Execution plan ready with {len(tasks)} tasks; waiting for approval",
                  "planning")

    @staticmethod
    def _validate_plan(raw: list[dict]) -> list[dict]:
        """Normalize keys, drop unknown dependencies and break any cycles."""
        tasks, keys = [], set()
        for t in raw[: config.MAX_TASKS]:
            key = str(t.get("key", "")).strip() or f"T{len(tasks) + 1}"
            if key in keys or t.get("agent") not in AGENTS:
                continue
            keys.add(key)
            tasks.append({
                "key": key, "title": str(t.get("title", key))[:300],
                "instructions": t.get("instructions", ""), "agent": t["agent"],
                "acceptance_criteria": t.get("acceptance_criteria", ""),
                "priority": t.get("priority", "medium"),
                "requires_approval": bool(t.get("requires_approval")),
                "is_deliverable": bool(t.get("is_deliverable")),
                "depends_on": [str(d) for d in t.get("depends_on", [])],
            })
        for t in tasks:
            t["depends_on"] = [d for d in dict.fromkeys(t["depends_on"]) if d in keys and d != t["key"]]
        # Kahn's algorithm; anything left over is in a cycle and loses its dependencies.
        remaining = {t["key"]: set(t["depends_on"]) for t in tasks}
        while True:
            free = [k for k, deps in remaining.items() if not deps]
            if not free:
                break
            for k in free:
                del remaining[k]
            for deps in remaining.values():
                deps.difference_update(free)
        for t in tasks:
            if t["key"] in remaining:
                t["depends_on"] = []
        return tasks

    # ------------------------------------------------------------------ run loop

    def ensure_running(self, project_id: str) -> None:
        loop = self._loops.get(project_id)
        if loop and not loop.done():
            self._wake.setdefault(project_id, asyncio.Event()).set()
            return
        self._wake[project_id] = asyncio.Event()
        self._loops[project_id] = asyncio.create_task(self._run(project_id))

    async def _run(self, project_id: str) -> None:
        inflight: dict[str, asyncio.Task] = {}
        wake = self._wake[project_id]
        try:
            while True:
                with SessionLocal() as db:
                    status = db.get(Project, project_id).status
                    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id)
                                            .order_by(Task.position)))
                    pending_approvals = db.scalar(select(func.count()).select_from(Approval).where(
                        Approval.project_id == project_id, Approval.status == "pending"))
                if status == "running":
                    done_keys = {t.key for t in tasks if t.status in DONE}
                    order = {"high": 0, "medium": 1, "low": 2}
                    for t in sorted(tasks, key=lambda t: order.get(t.priority, 1)):
                        if t.status == "pending" and t.id not in inflight and set(t.depends_on) <= done_keys:
                            inflight[t.id] = asyncio.create_task(self._execute(project_id, t.id))
                    if not inflight:
                        if all(t.status in DONE for t in tasks):
                            await self._finalize(project_id)
                        elif pending_approvals:
                            self._set_project(project_id, status="awaiting_approval")
                            self.emit(project_id, "awaiting_approval", "Workflow is waiting for your decision")
                        else:
                            self._set_project(project_id, status="failed",
                                              error="No task can make progress. Retry or reassign the failed tasks.")
                            self.emit(project_id, "workflow_blocked", "Workflow is blocked by failed tasks")
                        return
                elif not inflight:
                    return
                wake.clear()
                waiter = asyncio.create_task(wake.wait())
                await asyncio.wait([*inflight.values(), waiter], return_when=asyncio.FIRST_COMPLETED)
                waiter.cancel()
                for tid in [tid for tid, job in inflight.items() if job.done()]:
                    job = inflight.pop(tid)
                    if not job.cancelled() and job.exception():
                        log.error("task %s crashed", tid, exc_info=job.exception())
                        self._task_crashed(project_id, tid, job.exception())
        except asyncio.CancelledError:
            for job in inflight.values():
                job.cancel()
            raise
        except Exception as e:
            log.exception("run loop crashed")
            self._set_project(project_id, status="failed", error=f"Internal error: {e}")
            self.emit(project_id, "workflow_failed", "Workflow stopped on an internal error")
        finally:
            if self._loops.get(project_id) is asyncio.current_task():
                self._loops.pop(project_id, None)

    def _task_crashed(self, project_id: str, task_id: str, exc: BaseException) -> None:
        self._escalate(project_id, task_id, f"Internal error: {exc}", on_approve="retry")

    # ------------------------------------------------------------------ task execution

    async def _execute(self, project_id: str, task_id: str) -> None:
        async with self._sem:
            while True:
                with SessionLocal() as db:
                    project = db.get(Project, project_id)
                    task = db.get(Task, task_id)
                    if project.status != "running" or task.status not in ("pending", "needs_revision"):
                        if task.status == "needs_revision":
                            task.status = "pending"  # paused mid-revision; resumes later
                            db.commit()
                        return
                    if task.requires_approval and not task.approved:
                        task.status = "awaiting_approval"
                        db.add(Approval(
                            project_id=project_id, task_id=task_id, kind="action",
                            title=f"Authorize: {task.title}",
                            details=f"The plan marks this task as a sensitive action.\n\n{task.instructions}",
                            approve_label="Authorize task", reject_label="Skip task"))
                        db.commit()
                        key, title = task.key, task.title
                        self.send(project_id, "orchestrator", "user", "escalation",
                                  f"[{key}] needs authorization before it runs.", task_id, status="pending")
                        self.emit(project_id, "approval_requested", f"[{key}] {title} needs your authorization",
                                  task.agent, task_id)
                        return
                    task.status, task.error = "running", ""
                    task.attempts += 1
                    task.started_at = task.started_at or now()
                    db.commit()
                    attempt, agent, key, title = task.attempts, task.agent, task.key, task.title
                    deps = list(db.scalars(select(Task).where(Task.project_id == project_id,
                                                              Task.key.in_(task.depends_on or [""]))))
                    prompt = self._task_prompt(project, task, deps)
                    criteria = task.acceptance_criteria
                    instructions = task.instructions
                    objective = project.objective

                verb = "started" if attempt == 1 else f"started attempt {attempt}"
                self.emit(project_id, "task_started", f"[{key}] {title}: {verb}", agent, task_id)
                self.send(project_id, "orchestrator", agent, "assignment", f"[{key}] {title}", task_id,
                          status="in_progress")
                meta = {"task_key": key, "task_title": title, "attempt": attempt,
                        "dependencies": [d.key for d in deps]}
                try:
                    result = await self._call(project_id, task_id, agent, "work", prompt, meta=meta)
                    self._save_task(task_id, output=result.text, status="in_review")
                    self.send(project_id, agent, "qa", "result", f"[{key}] output ready for review.",
                              task_id, output_ref=f"task:{task_id}", status="completed")

                    if agent in ("qa", "supervisor"):  # reviewers are not reviewed by themselves
                        self._complete(project_id, task_id, key, title, agent, attempt, "No independent review")
                        return

                    self.emit(project_id, "review_started", f"[{key}] under QA review", "qa", task_id)
                    review = (await self._call(
                        project_id, task_id, "qa", "review",
                        f"# User objective\n{objective}\n\n# Task [{key}] {title}\n{instructions}\n\n"
                        f"# Acceptance criteria\n{criteria or 'None given; judge against the task.'}\n\n"
                        f"# Output to review\n{result.text}\n\n"
                        "Decide whether this output is good enough for other agents to build on.",
                        REVIEW_SCHEMA, config.REVIEW_EFFORT, meta)).data or {}
                except BudgetExceeded as e:
                    self._budget_stop(project_id, task_id, str(e))
                    return
                except LLMError as e:
                    self._log_attempt(task_id, attempt, "error", str(e))
                    self.emit(project_id, "task_error", f"[{key}] attempt {attempt} failed: {e}", agent, task_id)
                    if e.retryable and attempt < config.MAX_ATTEMPTS:
                        self._save_task(task_id, status="needs_revision", error=str(e))
                        await asyncio.sleep(min(2 ** attempt, 20) if config.MOCK_DELAY else 0)
                        continue
                    self._escalate(project_id, task_id, str(e), on_approve="retry")
                    return

                score = review.get("score")
                issues = review.get("issues") or []
                feedback = review.get("feedback", "") or "\n".join(issues)
                if review.get("verdict") == "pass":
                    self._save_task(task_id, qa_feedback=feedback, qa_score=score)
                    self.send(project_id, "qa", agent, "review", f"[{key}] approved (score {score}).",
                              task_id, status="approved")
                    self._complete(project_id, task_id, key, title, agent, attempt, f"QA passed ({score})")
                    return

                self._log_attempt(task_id, attempt, "qa_rejected", feedback)
                self.send(project_id, "qa", agent, "revision_request", feedback, task_id, status="rejected")
                if attempt < config.MAX_ATTEMPTS:
                    self._save_task(task_id, status="needs_revision", qa_feedback=feedback, qa_score=score)
                    self.emit(project_id, "task_rejected", f"[{key}] QA requested a revision: {feedback[:200]}",
                              "qa", task_id)
                    continue
                self._save_task(task_id, qa_feedback=feedback, qa_score=score)
                self._escalate(project_id, task_id,
                               f"QA rejected the output {attempt} times. Latest feedback:\n\n{feedback}",
                               on_approve="accept")
                return

    @staticmethod
    def _task_prompt(project: Project, task: Task, deps: list[Task]) -> str:
        parts = [f"# Project objective\n{project.objective}"]
        if project.notes:
            parts.append("# Project context from the user\n" + "\n".join(f"- {n}" for n in project.notes))
        if project.assumptions:
            parts.append("# Planning assumptions\n" + "\n".join(f"- {a}" for a in project.assumptions))
        parts.append(f"# Your task: [{task.key}] {task.title}\n{task.instructions}")
        if task.acceptance_criteria:
            parts.append(f"# Acceptance criteria\n{task.acceptance_criteria}")
        if deps:
            blocks = []
            for d in sorted(deps, key=lambda d: d.position):
                body = d.output if d.status == "completed" else "(This step was skipped by the user; work without it.)"
                blocks.append(f"## [{d.key}] {d.title} (from {AGENTS[d.agent]['name']})\n{body}")
            parts.append("# Inputs from completed tasks\n" + "\n\n".join(blocks))
        if task.attempts > 1 and task.output:
            parts.append(f"# Revision required\nYour previous output was not accepted.\n\n"
                         f"## Reviewer feedback\n{task.qa_feedback or task.error}\n\n"
                         f"## Your previous output\n{task.output}\n\n"
                         "Produce a complete corrected version, not a list of changes.")
        return "\n\n".join(parts)

    def _save_task(self, task_id: str, **fields) -> None:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            for k, v in fields.items():
                setattr(task, k, v)
            db.commit()

    def _log_attempt(self, task_id: str, attempt: int, outcome: str, detail: str) -> None:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.history = [*task.history, {"attempt": attempt, "outcome": outcome, "detail": detail,
                                            "at": now().isoformat() + "Z"}]
            db.commit()

    def _complete(self, project_id, task_id, key, title, agent, attempt, detail) -> None:
        self._log_attempt(task_id, attempt, "completed", detail)
        self._save_task(task_id, status="completed", finished_at=now(), error="")
        self.emit(project_id, "task_completed", f"[{key}] {title}: completed", agent, task_id)

    def _escalate(self, project_id: str, task_id: str, reason: str, on_approve: str) -> None:
        """Recovery is exhausted: the Supervisor hands the decision to the user."""
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.status, task.error, task.finished_at = "failed", reason, now()
            key, title = task.key, task.title
            db.add(Approval(
                project_id=project_id, task_id=task_id, kind="escalation",
                title=f"[{key}] {title} needs a decision", details=reason, on_approve=on_approve,
                approve_label="Accept output as is" if on_approve == "accept" else "Retry task",
                reject_label="Skip task"))
            db.commit()
        self.send(project_id, "supervisor", "user", "escalation", f"[{key}] {reason}", task_id, status="pending")
        self.emit(project_id, "task_escalated", f"[{key}] {title}: escalated to you after retries", "supervisor",
                  task_id)

    def _budget_stop(self, project_id: str, task_id: str | None, reason: str) -> None:
        with SessionLocal() as db:
            if task_id:
                task = db.get(Task, task_id)
                if task.status != "completed":
                    task.status = "pending"
                    task.attempts = max(0, task.attempts - 1)
            project = db.get(Project, project_id)
            already = db.scalar(select(func.count()).select_from(Approval).where(
                Approval.project_id == project_id, Approval.kind == "budget", Approval.status == "pending"))
            if not already:
                project.status = "paused"
                db.add(Approval(project_id=project_id, kind="budget", title="Execution budget reached",
                                details=f"{reason}. Approve to raise the budget by 50% and continue.",
                                approve_label="Raise budget and continue", reject_label="Cancel workflow"))
            db.commit()
        if not already:
            self.emit(project_id, "budget_reached", f"Paused: {reason}", "supervisor")

    # ------------------------------------------------------------------ final delivery

    async def _finalize(self, project_id: str) -> None:
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            objective, title, user_feedback = project.objective, project.title, project.review_feedback
            tasks = list(db.scalars(select(Task).where(Task.project_id == project_id, Task.status == "completed")
                                    .order_by(Task.position)))
            previous = db.scalar(select(Deliverable).where(Deliverable.project_id == project_id,
                                                           Deliverable.kind == "final_report")
                                 .order_by(Deliverable.created_at.desc()))
            previous_content = previous.content if previous else ""
        if not tasks:
            self._set_project(project_id, status="failed", error="Every task was skipped; nothing to deliver.")
            self.emit(project_id, "workflow_failed", "Nothing to deliver: every task was skipped")
            return

        self.emit(project_id, "consolidation_started", "Consolidating approved outputs into the final report",
                  "supervisor")
        self.send(project_id, "orchestrator", "supervisor", "assignment", "Consolidate approved outputs.")
        contributions = "\n\n".join(f"## [{t.key}] {t.title} (from {AGENTS[t.agent]['name']})\n{t.output}"
                                    for t in tasks)
        base = (f"# User objective\n{objective}\n\n# Approved outputs from the team\n{contributions}\n\n"
                "# What to produce\nWrite the final deliverable for the user as one Markdown document "
                f"titled “{title}”. Lead with an executive summary, then organize the content around "
                "what the objective asked for rather than around which agent produced it. Keep the "
                "team's supporting detail, and end with open questions and limitations.")
        meta = {"title": title, "objective": objective, "sections": [(t.title, t.output) for t in tasks]}
        feedback, qa_notes, report = "", "", ""
        if user_feedback and previous_content:
            feedback = f"The user rejected the previous version with this feedback:\n{user_feedback}"
            report = previous_content
        try:
            for round_no in range(1, config.FINAL_REVIEW_ROUNDS + 1):
                prompt = base
                if feedback:
                    prompt += (f"\n\n# Revision required\n{feedback}\n\n## Previous version\n{report}\n\n"
                               "Produce the complete corrected document.")
                report = (await self._call(project_id, None, "supervisor", "final", prompt, meta=meta)).text
                self.emit(project_id, "final_review", "Final report under QA review", "qa")
                review = (await self._call(
                    project_id, None, "qa", "final_review",
                    f"# User objective\n{objective}\n\n# Final deliverable to review\n{report}\n\n"
                    "Decide whether this deliverable fully and accurately answers the objective.",
                    REVIEW_SCHEMA, config.REVIEW_EFFORT, meta)).data or {}
                qa_notes = review.get("feedback", "") or "\n".join(review.get("issues") or [])
                if review.get("verdict") == "pass":
                    qa_notes = f"QA passed (score {review.get('score')})." + (f" {qa_notes}" if qa_notes else "")
                    break
                self.send(project_id, "qa", "supervisor", "revision_request", qa_notes, status="rejected")
                feedback = f"The QA Agent did not accept the previous version:\n{qa_notes}"
                qa_notes = f"QA still has concerns after {round_no} review round(s): {qa_notes}"
        except BudgetExceeded as e:
            self._budget_stop(project_id, None, str(e))
            return
        except LLMError as e:
            self._set_project(project_id, status="failed", error=f"Final consolidation failed: {e}")
            self.emit(project_id, "workflow_failed", f"Final consolidation failed: {e}", "supervisor")
            return

        with SessionLocal() as db:
            for d in db.scalars(select(Deliverable).where(Deliverable.project_id == project_id,
                                                          Deliverable.status == "draft")):
                db.delete(d)
            db.add(Deliverable(project_id=project_id, kind="final_report", title=title, content=report,
                               qa_notes=qa_notes))
            for t in tasks:
                if t.is_deliverable:
                    db.add(Deliverable(project_id=project_id, task_id=t.id, kind="supporting", title=t.title,
                                       content=t.output, qa_notes=f"QA score {t.qa_score}" if t.qa_score else ""))
            db.add(Approval(project_id=project_id, kind="deliverable", title="Review the final deliverable",
                            details=qa_notes, approve_label="Approve deliverable", reject_label="Request changes"))
            project = db.get(Project, project_id)
            project.status, project.review_feedback = "awaiting_approval", ""
            db.commit()
        self.send(project_id, "supervisor", "user", "delivery", "Final report ready for your review.",
                  output_ref="deliverable:final_report", status="pending")
        self.emit(project_id, "deliverable_ready", "Final report is ready for your review", "supervisor")

    # ------------------------------------------------------------------ human controls

    def decide(self, approval_id: str, approve: bool, note: str, user_id: str) -> None:
        with SessionLocal() as db:
            approval = db.get(Approval, approval_id)
            project = db.get(Project, approval.project_id) if approval else None
            if approval is None or project.user_id != user_id:
                raise LookupError("Approval not found")
            if approval.status != "pending":
                raise ValueError("This approval was already decided")
            approval.status = "approved" if approve else "rejected"
            approval.note, approval.decided_at = note, now()
            task = db.get(Task, approval.task_id) if approval.task_id else None
            pid, kind = project.id, approval.kind
            next_step = "run"

            if kind == "plan":
                if approve:
                    project.status = "running"
                else:
                    if note:
                        project.notes = [*project.notes, f"Feedback on the rejected plan: {note}"]
                    next_step = "replan"
            elif kind == "action":
                if approve:
                    task.approved, task.status = True, "pending"
                else:
                    task.status, task.finished_at = "skipped", now()
            elif kind == "escalation":
                if not approve:
                    task.status = "skipped"
                elif approval.on_approve == "accept" and task.output:
                    task.status, task.error = "completed", ""
                else:
                    task.status, task.attempts, task.error = "pending", 0, ""
                    if note:
                        task.instructions += f"\n\nAdditional guidance from the user: {note}"
            elif kind == "budget":
                if approve:
                    project.budget_usd = round(project.budget_usd * 1.5, 2)
                else:
                    next_step = "cancel"
            elif kind == "deliverable":
                drafts = db.scalars(select(Deliverable).where(Deliverable.project_id == pid,
                                                              Deliverable.status == "draft"))
                if approve:
                    for d in drafts:
                        d.status = "approved"
                    project.status, project.completed_at = "completed", now()
                    next_step = "done"
                else:
                    project.review_feedback = note or "The user rejected the deliverable without comment."
            if next_step == "run" and project.status in ("awaiting_approval", "paused", "failed"):
                project.status, project.error = "running", ""
            db.commit()

        verdict = "approved" if approve else "rejected"
        self.emit(pid, f"approval_{verdict}", f"You {verdict}: {approval.title}" + (f" ({note})" if note else ""),
                  task_id=approval.task_id)
        if next_step == "replan":
            self.start_planning(pid)
        elif next_step == "cancel":
            self.cancel(pid)
        elif next_step == "done":
            self.emit(pid, "workflow_completed", "Workflow completed; deliverables approved", "supervisor")
        else:
            self.ensure_running(pid)

    def pause(self, project_id: str) -> None:
        self._set_project(project_id, status="paused")
        self.emit(project_id, "paused", "Paused by you. Tasks already running will finish their current step.")
        self.ensure_running(project_id)

    def resume(self, project_id: str) -> None:
        self._set_project(project_id, status="running", error="")
        self.emit(project_id, "resumed", "Resumed by you")
        self.ensure_running(project_id)

    def cancel(self, project_id: str) -> None:
        loop = self._loops.pop(project_id, None)
        if loop:
            loop.cancel()
        with SessionLocal() as db:
            project = db.get(Project, project_id)
            project.status = "cancelled"
            for t in db.scalars(select(Task).where(Task.project_id == project_id)):
                if t.status not in ("completed", "skipped", "failed"):
                    t.status = "cancelled"
            for a in db.scalars(select(Approval).where(Approval.project_id == project_id,
                                                       Approval.status == "pending")):
                a.status, a.note, a.decided_at = "rejected", "Workflow cancelled", now()
            db.commit()
        self.emit(project_id, "cancelled", "Workflow cancelled by you")

    def retry_task(self, project_id: str, task_id: str) -> None:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.status, task.attempts, task.error, task.output = "pending", 0, "", ""
            task.qa_feedback, task.qa_score, task.finished_at = "", None, None
            for a in db.scalars(select(Approval).where(Approval.task_id == task_id, Approval.status == "pending")):
                a.status, a.note, a.decided_at = "rejected", "Superseded: task re-run", now()
            for a in db.scalars(select(Approval).where(Approval.project_id == project_id,
                                                       Approval.kind == "deliverable",
                                                       Approval.status == "pending")):
                a.status, a.note, a.decided_at = "rejected", "Superseded: task re-run", now()
            project = db.get(Project, project_id)
            project.status, project.error = "running", ""
            key, title = task.key, task.title
            db.commit()
        self.emit(project_id, "task_requeued", f"[{key}] {title}: re-queued by you", task_id=task_id)
        self.ensure_running(project_id)

    def recover(self) -> None:
        """After a restart, put interrupted work back in the queue and restart the loops."""
        with SessionLocal() as db:
            for t in db.scalars(select(Task).where(Task.status.in_(["running", "in_review", "needs_revision"]))):
                t.status = "pending"
            running = [p.id for p in db.scalars(select(Project).where(Project.status == "running"))]
            planning = [p.id for p in db.scalars(select(Project).where(Project.status == "planning"))]
            db.commit()
        for pid in running:
            self.ensure_running(pid)
        for pid in planning:
            self.start_planning(pid)
