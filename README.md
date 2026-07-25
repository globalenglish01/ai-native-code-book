# AI Native Framework

Production-grade, independently pluggable Python packages extracted and
generalized from verified patterns in real production AI agent systems
(plus a few modules designed from scratch where no real-project precedent
existed — see Status below). This is the **Framework** layer of a three-layer
AI Native system:

1. **AI Native Methodology** — standards, maturity model, best practices (external).
2. **AI Native Framework** — this repository: reusable components.
3. **AI Native CLI** — `ainative new <project> --type <type>` one-command scaffolding — `ainative-cli` in this repository.

## Packages

| Package | Purpose |
|---|---|
| `ainative-core` | Foundation: `Protocol` interfaces, vendor-agnostic model factory with cross-vendor fallback, in-memory default backends. |
| `ainative-guardrail` | Agent runtime guardrails: model routing, token/call budgets, consecutive-failure/stall detection, composite health monitoring. |
| `ainative-prompt` | Prompt version management, deterministic sticky A/B routing, LLM-as-judge evaluation. |
| `ainative-security` | PII redaction, output safety scanning (secrets/injection/malicious code), homoglyph folding, secret drift monitoring. |
| `ainative-eval` | FCARS-style governance gate (GREEN/YELLOW/RED/UNKNOWN/SKIPPED/NEEDS_REVIEW state machine with boundary-recheck noise reduction), judge-score aggregation. |
| `ainative-memory` | Checkpoint-saver factory (permanent vs. transient failure classification), long-term `MemoryStore` with owner-scoped deletion, PII-redacting persistence proxy, history token-budget trimming. |
| `ainative-mcp` | MCP server config assembly, stdio environment-variable safe-listing, tool-call audit log schema. |
| `ainative-workflow` | Lightweight DAG orchestration engine (topological execution, conditional skip, pause/resume), HITL interrupt detection, timeout-safe-default policy. |
| `ainative-a2a` | Agent-to-agent capability registry/discovery, task dispatch with delegation-depth and cycle protection, pluggable transport (in-process by default). |
| `ainative-cli` | `ainative new <project> --type <type>` scaffolding — generates a runnable starter project wired to a sensible subset of the above packages. |

## Design principles

- **No hard dependency on any specific database/message-queue product.** Every
  package that needs persistence defines a `Protocol` (in `ainative_core.protocols`)
  and ships an in-memory default implementation. Real projects implement the
  protocol themselves to connect to Postgres/Redis/MongoDB/etc.
- **Each package is independently installable and testable.** Every module besides
  `ainative-core` depends only on `ainative-core` — never on each other (the CLI
  generates code that imports several packages together, but the packages
  themselves stay decoupled).
- **Zero real API keys or infrastructure required to run the tests or the demos.**
- **Safety-by-construction where it matters.** For example, `ainative-workflow`'s
  HITL timeout policy makes it structurally impossible to default to "approve" on
  timeout, and `ainative-a2a`'s dispatcher rejects cyclic or too-deep delegation
  chains before they can run away.

## Getting started

```bash
uv sync
uv run ruff check packages/ examples/
uv run pytest packages/ examples/tests
uv run python examples/quickstart.py       # core + guardrail + prompt + security + eval
uv run python examples/quickstart_v2.py    # memory + mcp + workflow + a2a + cli
uv run python -m ainative_cli.main new my-app --type customer-service
```

Available `ainative new --type` values: `customer-service`, `browser-agent`,
`multi-agent`, `minimal` (run `ainative-cli`'s `list-types` command to see
descriptions and package sets).

## Example products

`examples/products/` contains fuller, more realistic integrations than the
CLI's starter templates — each one is a runnable script with real pytest
coverage in `examples/tests/`:

| Example | Packages combined | What it demonstrates |
|---|---|---|
| `customer_support_bot.py` | guardrail, prompt, security, memory, eval | Multi-turn chat, PII redacted before storage, secret-leak in a reply auto-redacted, deployment gate. |
| `rag_qa_assistant.py` | memory, security | Retrieval context capped by token budget (not left unbounded — a documented real anti-pattern), a poisoned retrieved document's injection phrase stripped from the final answer. |
| `code_review_assistant.py` | workflow, security, eval | DAG pipeline (static analysis → LLM review → safety scan); a failed static-analysis stage skips the (costly) LLM stage entirely; a manipulated "fix suggestion" containing a destructive shell command gets sanitized before reaching the human reviewer. |
| `research_team.py` | a2a, workflow | Orchestrator delegates `research` and `fact_check` to separate agents via capability discovery; pipeline pauses for human sign-off (HITL) when fact-checking flags an unverified claim. |
| `browser_task_agent.py` | guardrail, mcp | A stuck browser-automation loop (repeated `browser_snapshot` with no progress) gets short-circuited by the stall guard; per-tool call caps enforced; every call (including blocked ones) recorded in the audit log. |
| `content_moderation_pipeline.py` | security, eval | UGC pipeline: PII redacted before storage, a leaked secret in submitted content routes to human review instead of auto-publishing, a deployment gate blocks release while the moderation service's own API key/webhook secret are still at their insecure defaults. |
| `personal_assistant_with_memory.py` | memory, prompt | Facts learned in one session are recalled by thread ID in a later session; conversation history is trimmed to a token budget instead of growing unbounded; a user's "forget me" request deletes all of their long-term memory without touching other users'. |
| `agent_plugin_marketplace.py` | a2a, mcp, eval | A coordinator delegates tasks to plugin agents discovered by capability; every plugin call is audited; a deployment gate blocks a plugin whose historical error rate is too high; cyclic delegation is rejected instead of looping forever. |
| `document_publishing_pipeline.py` | workflow, guardrail | A draft → review → publish DAG pauses instead of auto-publishing when content is too long or contains high-risk phrasing; a human approval resumes the run without re-executing the completed draft stage; each pipeline stage gets its own consecutive-error budget. |
| `eval_harness.py` | prompt, eval | Each eval case is scored by an ensemble of independent LLM-judge calls (median score, disagreement flagged as `high_uncertainty`); a deployment gate blocks the whole suite if any single case's judges disagreed too much, even when the average score looks fine. |

```bash
uv run python examples/products/customer_support_bot.py
uv run python examples/products/rag_qa_assistant.py
uv run python examples/products/code_review_assistant.py
uv run python examples/products/browser_task_agent.py
uv run python examples/products/research_team.py
uv run python examples/products/content_moderation_pipeline.py
uv run python examples/products/personal_assistant_with_memory.py
uv run python examples/products/agent_plugin_marketplace.py
uv run python examples/products/document_publishing_pipeline.py
uv run python examples/products/eval_harness.py
```

## Status

**Delivered:**
- Batch 1: `ainative-core` + guardrail/prompt/security/eval — extracted and
  generalized from two audited real production codebases, with several
  genuine bugs found during extraction fixed rather than copied verbatim.
- Batch 2: `ainative-memory`/`ainative-mcp` (extracted and generalized from
  the same real codebases) + `ainative-workflow`/`ainative-a2a` (designed
  from scratch — neither real codebase had implemented DAG orchestration or
  agent-to-agent delegation; both had deliberately chosen a lighter
  single-agent + staged-prompt + HITL-interrupt architecture instead) +
  `ainative-cli` (the scaffolding layer).

**Deferred:** `ainative-rag` (LightRAG-derived — requires careful separation
of the user's own additions from LightRAG's upstream MIT-licensed code before
extraction).
