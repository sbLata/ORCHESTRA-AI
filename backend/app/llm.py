"""Model abstraction layer.

Agents talk to an `LLMProvider`; the orchestrator never imports a vendor SDK. `AnthropicProvider`
is the primary engine (Claude Fable by default). `MockProvider` produces clearly labelled simulated
output so the platform can be run and tested without credentials.
"""
import asyncio
import json
import os
import random
from dataclasses import dataclass
from typing import Protocol

from . import config
from .agents import AGENTS


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class LLMResult:
    text: str
    data: dict | None
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = config.PRICING.get(model, config.PRICING["claude-fable-5-2"])
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, *, agent: str, kind: str, prompt: str, schema: dict | None = None,
                       effort: str = "high", meta: dict | None = None) -> LLMResult: ...


# Models that accept the server-side refusal fallback parameter.
_FALLBACK_MODELS = ("claude-fable-5", "claude-opus-5", "claude-sonnet-5-5")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic()
        self.model = config.MODEL

    async def complete(self, *, agent, kind, prompt, schema=None, effort="high", meta=None) -> LLMResult:
        anthropic = self._anthropic
        spec = AGENTS[agent]
        output_config: dict = {"effort": effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 32000,
            "system": spec["system"],
            "output_config": output_config,
        }
        if self.model.startswith(_FALLBACK_MODELS):
            # On a safety decline the API re-runs the request on a fallback model in the same call.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        if spec["web_search"] and config.WEB_SEARCH and schema is None:
            kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]

        messages: list = [{"role": "user", "content": prompt}]
        input_tokens = output_tokens = 0
        text_parts: list[str] = []
        try:
            for _ in range(5):  # server tools may pause a long turn; resume it
                async with self.client.beta.messages.stream(messages=messages, **kwargs) as stream:
                    message = await stream.get_final_message()
                usage = message.usage
                input_tokens += (usage.input_tokens or 0) + (usage.cache_read_input_tokens or 0) \
                    + (usage.cache_creation_input_tokens or 0)
                output_tokens += usage.output_tokens or 0
                text_parts.extend(b.text for b in message.content if b.type == "text")
                if message.stop_reason != "pause_turn":
                    break
                messages.append({"role": "assistant", "content": message.content})
        except anthropic.RateLimitError as e:
            raise LLMError(f"Rate limited by the model API: {e}", retryable=True)
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Could not reach the model API: {e}", retryable=True)
        except anthropic.APIStatusError as e:
            raise LLMError(f"Model API error {e.status_code}: {e.message}", retryable=e.status_code >= 500)

        if message.stop_reason == "refusal":
            raise LLMError("The model declined this request.", retryable=False)
        if message.stop_reason == "max_tokens":
            raise LLMError("The model's output was cut off at the token limit.", retryable=True)

        text = "".join(text_parts).strip()
        data = None
        if schema is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                raise LLMError("The model returned malformed structured output.", retryable=True)
        return LLMResult(text, data, input_tokens, output_tokens, message.model,
                         cost_of(self.model, input_tokens, output_tokens))


class MockProvider:
    """Deterministic simulated agents. Output is labelled as simulated; costs are estimates."""

    name = "mock"
    model = config.MODEL

    async def complete(self, *, agent, kind, prompt, schema=None, effort="high", meta=None) -> LLMResult:
        meta = meta or {}
        if config.MOCK_DELAY:
            await asyncio.sleep(config.MOCK_DELAY * random.uniform(0.6, 1.6))
        data = None
        if kind == "plan":
            data = self._plan(meta.get("objective", ""))
            text = json.dumps(data)
        elif kind in ("review", "final_review"):
            # Fail one task's first attempt so the QA -> revision loop is visible in a demo run.
            fail = kind == "review" and meta.get("task_key") == "T2" and meta.get("attempt") == 1
            data = {
                "verdict": "fail" if fail else "pass",
                "score": 58 if fail else 88,
                "issues": ["Competitor pricing is asserted without a source."] if fail else [],
                "feedback": "Add a source or mark each pricing figure as an estimate." if fail else "",
            }
            text = json.dumps(data)
        elif kind == "final":
            text = self._final(meta)
        else:
            text = self._work(agent, meta)
        input_tokens = max(1, len(prompt) // 4) + 400
        output_tokens = max(1, len(text) // 4) + 600
        return LLMResult(text, data, input_tokens, output_tokens, f"mock:{self.model}",
                         cost_of(self.model, input_tokens, output_tokens))

    @staticmethod
    def _plan(objective: str) -> dict:
        def task(key, title, agent, deps, instructions, criteria, **extra):
            return {"key": key, "title": title, "agent": agent, "depends_on": deps,
                    "instructions": instructions, "acceptance_criteria": criteria,
                    "priority": extra.get("priority", "medium"),
                    "requires_approval": extra.get("requires_approval", False),
                    "is_deliverable": extra.get("is_deliverable", False)}

        words = objective.strip().split()
        return {
            "title": " ".join(words[:7]) + ("…" if len(words) > 7 else ""),
            "summary": "Research the landscape in parallel, synthesize the opportunity, then produce "
                       "a plan, a financial view and the main document. (Simulated plan.)",
            "clarifying_questions": ["What budget range should the plan assume?",
                                     "Is there a hard deadline for the first milestone?"],
            "assumptions": ["No budget was given, so a lean budget is assumed.",
                            "The audience for the deliverable is the project owner."],
            "tasks": [
                task("T1", "Research the market landscape", "research", [],
                     "Size the market and describe its main trends.", "Covers size, growth and trends.",
                     priority="high"),
                task("T2", "Identify and profile competitors", "research", [],
                     "Profile the main competitors and their pricing.", "At least five competitors with pricing.",
                     priority="high"),
                task("T3", "Define target customer segments", "research", [],
                     "Describe the most promising customer segments and their needs.",
                     "Two or more segments with needs and willingness to pay."),
                task("T4", "Synthesize the opportunity", "execution", ["T1", "T2", "T3"],
                     "Combine the research into a clear opportunity assessment.",
                     "States the opportunity, differentiation and main risks.", priority="high"),
                task("T5", "Build the roadmap and timeline", "planning", ["T4"],
                     "Create a phased roadmap with milestones and owners.", "Phases, milestones, owners."),
                task("T6", "Create the financial projection", "execution", ["T4"],
                     "Project costs and revenue with stated assumptions.", "Assumptions listed; totals reconcile.",
                     is_deliverable=True),
                task("T7", "Prepare stakeholder announcement", "execution", ["T5"],
                     "Draft the announcement email to stakeholders. Do not send it.",
                     "Draft ready for a human to send.", priority="low", requires_approval=True),
                task("T8", "Write the main plan document", "execution", ["T4", "T5", "T6"],
                     "Write the complete plan document.", "Covers every part of the objective.",
                     priority="high", is_deliverable=True),
            ],
        }

    @staticmethod
    def _work(agent: str, meta: dict) -> str:
        title = meta.get("task_title", "Task")
        revised = meta.get("attempt", 1) > 1
        return (
            f"## {title}\n\n"
            f"> Simulated output from the {AGENTS[agent]['name']} (mock model, no real analysis).\n\n"
            f"**Summary.** This section would contain the {AGENTS[agent]['name']}'s findings for "
            f"“{title}”, built on {len(meta.get('dependencies', []))} upstream input(s).\n\n"
            "### Key points\n"
            "- Finding one, with the evidence behind it.\n"
            "- Finding two, with the evidence behind it.\n"
            "- An open question the team should resolve.\n\n"
            "### Assumptions\n- Figures are placeholders, not estimates.\n"
            + ("\n### Revision notes\n- Addressed QA feedback from the previous attempt.\n" if revised else "")
        )

    @staticmethod
    def _final(meta: dict) -> str:
        parts = [f"# {meta.get('title', 'Final report')}\n",
                 "> Simulated consolidated report (mock model). Connect a model API key for real output.\n",
                 f"## Objective\n\n{meta.get('objective', '')}\n",
                 "## Executive summary\n\nThe team completed every planned task. The sections below "
                 "consolidate their approved outputs.\n"]
        for title, output in meta.get("sections", []):
            body = output.split("\n", 1)[1] if output.startswith("## ") and "\n" in output else output
            parts.append(f"## {title}\n{body}")
        return "\n".join(parts)


def has_anthropic_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE")
                or os.path.isdir(os.path.expanduser("~/.config/anthropic")))


def get_provider() -> LLMProvider:
    mode = config.LLM_MODE
    if mode == "mock" or (mode == "auto" and not has_anthropic_credentials()):
        return MockProvider()
    return AnthropicProvider()
