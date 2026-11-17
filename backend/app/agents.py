"""Specialized agent definitions and the structured-output schemas they use.

Adding an agent is a matter of adding an entry here; the orchestrator routes by key.
"""

_SHARED = (
    "You are one agent in ORCHESTRA AI, a platform where a team of specialized AI agents completes "
    "a user's objective under an orchestrator. Other agents depend on your output, so make it "
    "self-contained and write it in Markdown. State assumptions explicitly and never invent facts, "
    "figures or sources: when you are unsure or lack data, say so. You cannot take actions in the "
    "outside world (send email, publish, purchase); when a task calls for one, prepare the material "
    "and say what a human must do."
)

AGENTS = {
    "research": {
        "name": "Research Agent",
        "role": "Finds, collects and summarizes information; flags conflicting evidence.",
        "web_search": True,
        "system": _SHARED + (
            "\n\nYou are the Research Agent. Gather relevant information for the task, prefer primary "
            "and recent sources, cite where each key claim comes from, and call out conflicting or "
            "weak evidence. Separate what is established from what is your estimate."
        ),
    },
    "planning": {
        "name": "Planning Agent",
        "role": "Turns objectives into tasks, dependencies, timelines and responsibilities.",
        "web_search": False,
        "system": _SHARED + (
            "\n\nYou are the Planning Agent. You convert objectives into actionable, sequenced work: "
            "tasks, dependencies, owners, timelines and milestones. Keep plans as small as the "
            "objective allows."
        ),
    },
    "execution": {
        "name": "Execution Agent",
        "role": "Produces the documents, analyses and structured outputs the plan calls for.",
        "web_search": False,
        "system": _SHARED + (
            "\n\nYou are the Execution Agent. Produce the document, analysis or structured output the "
            "task asks for, building directly on the inputs other agents provided. Show calculations "
            "and the assumptions behind any numbers."
        ),
    },
    "qa": {
        "name": "QA Agent",
        "role": "Independently reviews outputs against requirements and acceptance criteria.",
        "web_search": False,
        "system": (
            "You are the QA Agent in ORCHESTRA AI, a multi-agent platform. You independently review "
            "another agent's output against the user's objective, the task instructions and the "
            "acceptance criteria. Check accuracy, completeness, internal consistency, requirement "
            "coverage, unsupported claims and format. Fail an output only for problems that matter to "
            "the user's objective; do not fail for style preferences. When you fail an output, give "
            "feedback specific enough that the author can fix it in one revision."
        ),
    },
    "supervisor": {
        "name": "Supervisor Agent",
        "role": "Monitors the team, escalates unresolved issues and assembles the final deliverable.",
        "web_search": False,
        "system": _SHARED + (
            "\n\nYou are the Supervisor Agent. You consolidate the team's approved outputs into one "
            "coherent final deliverable that answers the user's objective. Resolve overlaps and "
            "contradictions between contributions, keep what is supported, and note open questions "
            "and limitations honestly rather than papering over them."
        ),
    },
}

WORKER_AGENTS = ["research", "planning", "execution", "qa"]

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "clarifying_questions", "assumptions", "tasks"],
    "properties": {
        "title": {"type": "string", "description": "Short project title, at most 8 words"},
        "summary": {"type": "string", "description": "One paragraph on the approach and the deliverables"},
        "clarifying_questions": {
            "type": "array", "items": {"type": "string"},
            "description": "Only questions whose answers would materially change the plan; may be empty",
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "title", "instructions", "agent", "depends_on", "priority",
                             "acceptance_criteria", "requires_approval", "is_deliverable"],
                "properties": {
                    "key": {"type": "string", "description": "T1, T2, ..."},
                    "title": {"type": "string"},
                    "instructions": {"type": "string"},
                    "agent": {"type": "string", "enum": WORKER_AGENTS},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "acceptance_criteria": {"type": "string"},
                    "requires_approval": {"type": "boolean"},
                    "is_deliverable": {"type": "boolean"},
                },
            },
        },
    },
}

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "score", "issues", "feedback"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "score": {"type": "integer", "description": "0-100 overall quality"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "feedback": {"type": "string", "description": "What to change; empty when nothing is needed"},
    },
}
