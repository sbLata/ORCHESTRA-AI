"""End-to-end workflow tests against the mock model provider."""
import os
import tempfile
import time

os.environ["ORCHESTRA_LLM"] = "mock"
os.environ["ORCHESTRA_MOCK_DELAY"] = "0"
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.orchestrator import Orchestrator  # noqa: E402

OBJECTIVE = "Research the market for a subscription AI productivity app and write a launch plan."


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def signup(client, email):
    r = client.post("/api/auth/register", json={"email": email, "password": "correct-horse", "name": "T"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def wait_for(client, headers, pid, predicate, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get(f"/api/projects/{pid}", headers=headers).json()
        if predicate(snap):
            return snap
        time.sleep(0.05)
    raise AssertionError(f"timed out; project status = {snap['project']['status']}")


def pending(snap, kind):
    return [a for a in snap["approvals"] if a["status"] == "pending" and a["kind"] == kind]


def decide(client, headers, approval, approve, note=""):
    r = client.post(f"/api/approvals/{approval['id']}/decide", headers=headers,
                    json={"approve": approve, "note": note})
    assert r.status_code == 200, r.text


def test_auth_required_and_validated(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/auth/register", json={"email": "nope", "password": "longenough"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "a@b.co", "password": "short"}).status_code == 422
    signup(client, "dup@example.com")
    assert client.post("/api/auth/register",
                       json={"email": "dup@example.com", "password": "correct-horse"}).status_code == 409
    assert client.post("/api/auth/login",
                       json={"email": "dup@example.com", "password": "wrong-password"}).status_code == 401


def test_full_workflow(client):
    h = signup(client, "owner@example.com")
    pid = client.post("/api/projects", headers=h, json={"objective": OBJECTIVE}).json()["id"]

    # Planning produces a task graph and waits for the user before executing anything.
    snap = wait_for(client, h, pid, lambda s: s["project"]["status"] == "awaiting_plan_approval")
    assert len(snap["tasks"]) == 8 and all(t["status"] == "pending" for t in snap["tasks"])
    decide(client, h, pending(snap, "plan")[0], True)

    # The sensitive task parks for authorization while everything else finishes.
    snap = wait_for(client, h, pid, lambda s: pending(s, "action") and s["project"]["status"] == "awaiting_approval")
    by_key = {t["key"]: t for t in snap["tasks"]}
    assert by_key["T7"]["status"] == "awaiting_approval" and not by_key["T7"]["output"]
    assert by_key["T8"]["status"] == "completed"
    # QA rejected T2 once, and the agent revised it.
    assert by_key["T2"]["attempts"] == 2
    assert [a["outcome"] for a in by_key["T2"]["history"]] == ["qa_rejected", "completed"]
    decide(client, h, pending(snap, "action")[0], True)

    # Final report is consolidated, QA-reviewed, then held for the user's approval.
    snap = wait_for(client, h, pid, lambda s: pending(s, "deliverable"))
    final = [d for d in snap["deliverables"] if d["kind"] == "final_report"]
    assert len(final) == 1 and final[0]["status"] == "draft"
    assert snap["project"]["status"] == "awaiting_approval"

    # Rejecting sends it back for revision with the feedback; approving completes the project.
    decide(client, h, pending(snap, "deliverable")[0], False, "Shorten the summary")
    snap = wait_for(client, h, pid, lambda s: pending(s, "deliverable"))
    decide(client, h, pending(snap, "deliverable")[0], True)
    snap = wait_for(client, h, pid, lambda s: s["project"]["status"] == "completed")
    assert all(d["status"] == "approved" for d in snap["deliverables"])
    assert {d["kind"] for d in snap["deliverables"]} == {"final_report", "supporting"}

    # Traceability: every approval decision and agent hand-off is recorded; usage is tracked.
    assert all(a["status"] != "pending" and a["decided_at"] for a in snap["approvals"])
    assert {m["type"] for m in snap["messages"]} >= {"assignment", "result", "review", "revision_request", "delivery"}
    assert snap["usage"]["cost_usd"] > 0
    assert sum(a["usage"]["calls"] for a in snap["agents"]) >= 18

    final_id = [d for d in snap["deliverables"] if d["kind"] == "final_report"][0]["id"]
    for fmt, marker in (("md", "# "), ("html", "<h1>")):
        r = client.get(f"/api/deliverables/{final_id}/export?format={fmt}", headers=h)
        assert r.status_code == 200 and marker in r.text and "attachment" in r.headers["content-disposition"]

    stats = client.get("/api/dashboard", headers=h).json()["stats"]
    assert stats["completed"] == 1 and stats["workflow_completion_rate"] == 1.0 and stats["qa_rejection_rate"] > 0


def test_projects_are_isolated_between_users(client):
    owner, other = signup(client, "a@example.com"), signup(client, "b@example.com")
    pid = client.post("/api/projects", headers=owner, json={"objective": OBJECTIVE}).json()["id"]
    snap = wait_for(client, owner, pid, lambda s: s["project"]["status"] == "awaiting_plan_approval")
    assert client.get(f"/api/projects/{pid}", headers=other).status_code == 404
    assert client.post(f"/api/projects/{pid}/control", headers=other, json={"action": "cancel"}).status_code == 404
    assert client.patch(f"/api/tasks/{snap['tasks'][0]['id']}", headers=other, json={"title": "x"}).status_code == 404
    assert client.post(f"/api/approvals/{pending(snap, 'plan')[0]['id']}/decide", headers=other,
                       json={"approve": True}).status_code == 404
    assert client.get("/api/dashboard", headers=other).json()["projects"] == []


def test_replan_skip_and_cancel(client):
    h = signup(client, "controls@example.com")
    pid = client.post("/api/projects", headers=h, json={"objective": OBJECTIVE}).json()["id"]
    snap = wait_for(client, h, pid, lambda s: s["project"]["status"] == "awaiting_plan_approval")

    # Rejecting the plan rebuilds it with the feedback kept as project context.
    decide(client, h, pending(snap, "plan")[0], False, "Focus on Europe")
    snap = wait_for(client, h, pid, lambda s: s["project"]["status"] == "awaiting_plan_approval"
                    and len(pending(s, "plan")) == 1 and "Europe" in " ".join(s["project"]["notes"]))
    # Reassign a task before approving.
    t1 = snap["tasks"][0]
    assert client.patch(f"/api/tasks/{t1['id']}", headers=h, json={"agent": "execution"}).status_code == 200
    decide(client, h, pending(snap, "plan")[0], True)

    # Declining the sensitive task skips it; the workflow still reaches final delivery.
    snap = wait_for(client, h, pid, lambda s: pending(s, "action") and s["project"]["status"] == "awaiting_approval")
    decide(client, h, pending(snap, "action")[0], False)
    snap = wait_for(client, h, pid, lambda s: pending(s, "deliverable"))
    by_key = {t["key"]: t for t in snap["tasks"]}
    assert by_key["T7"]["status"] == "skipped" and by_key["T1"]["agent"] == "execution"

    assert client.post(f"/api/projects/{pid}/control", headers=h, json={"action": "cancel"}).status_code == 200
    snap = client.get(f"/api/projects/{pid}", headers=h).json()
    assert snap["project"]["status"] == "cancelled" and not pending(snap, "deliverable")
    assert client.post(f"/api/projects/{pid}/control", headers=h, json={"action": "resume"}).status_code == 409


def test_budget_pauses_workflow(client):
    h = signup(client, "budget@example.com")
    pid = client.post("/api/projects", headers=h, json={"objective": OBJECTIVE, "budget_usd": 0.05}).json()["id"]
    snap = wait_for(client, h, pid, lambda s: s["project"]["status"] == "awaiting_plan_approval")
    decide(client, h, pending(snap, "plan")[0], True)
    snap = wait_for(client, h, pid, lambda s: pending(s, "budget"))
    assert snap["project"]["status"] == "paused"
    decide(client, h, pending(snap, "budget")[0], False)
    assert client.get(f"/api/projects/{pid}", headers=h).json()["project"]["status"] == "cancelled"


def test_plan_validation_breaks_cycles_and_drops_bad_references():
    def raw(key, agent="research", deps=()):
        return {"key": key, "title": key, "agent": agent, "depends_on": list(deps)}

    tasks = Orchestrator._validate_plan([
        raw("T1"), raw("T2", deps=["T1", "T9", "T2"]), raw("T3", deps=["T4"]), raw("T4", deps=["T3"]),
        raw("T5", agent="nonexistent"), raw("T1"),
    ])
    assert [t["key"] for t in tasks] == ["T1", "T2", "T3", "T4"]
    assert {t["key"]: t["depends_on"] for t in tasks} == {"T1": [], "T2": ["T1"], "T3": [], "T4": []}
