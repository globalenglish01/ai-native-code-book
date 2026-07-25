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
uv run pytest packages/
uv run python examples/quickstart.py       # core + guardrail + prompt + security + eval
uv run python examples/quickstart_v2.py    # memory + mcp + workflow + a2a + cli
uv run python -m ainative_cli.main new my-app --type customer-service
```

Available `ainative new --type` values: `customer-service`, `browser-agent`,
`multi-agent`, `minimal` (run `ainative-cli`'s `list-types` command to see
descriptions and package sets).

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
