from __future__ import annotations

import pytest
from ainative_workflow.graph import NodeStatus, Workflow, WorkflowNode, WorkflowPaused


@pytest.mark.asyncio
async def test_linear_workflow_runs_all_nodes_in_order():
    calls = []

    def step1(ctx):
        calls.append("step1")
        return "a"

    def step2(ctx):
        calls.append("step2")
        return ctx["step1_out"] + "b"

    wf = Workflow([
        WorkflowNode(name="step1", fn=step1, output_key="step1_out"),
        WorkflowNode(name="step2", fn=step2, depends_on=("step1",), output_key="step2_out"),
    ])
    run = await wf.run({})
    assert calls == ["step1", "step2"]
    assert run.context["step2_out"] == "ab"
    assert run.is_completed


@pytest.mark.asyncio
async def test_async_node_functions_are_awaited():
    async def async_step(ctx):
        return "async result"

    wf = Workflow([WorkflowNode(name="step", fn=async_step, output_key="out")])
    run = await wf.run({})
    assert run.context["out"] == "async result"


@pytest.mark.asyncio
async def test_condition_false_skips_node():
    def gate(ctx):
        return False

    def should_not_run(ctx):
        raise AssertionError("should never be called")

    wf = Workflow([
        WorkflowNode(name="gate", fn=lambda ctx: None, output_key=None),
        WorkflowNode(name="conditional", fn=should_not_run, depends_on=("gate",), condition=gate),
    ])
    run = await wf.run({})
    assert run.node_status["conditional"] == NodeStatus.SKIPPED


@pytest.mark.asyncio
async def test_downstream_of_skipped_node_is_also_skipped_by_default():
    wf = Workflow([
        WorkflowNode(name="a", fn=lambda ctx: None, condition=lambda ctx: False),
        WorkflowNode(name="b", fn=lambda ctx: None, depends_on=("a",)),
    ])
    run = await wf.run({})
    assert run.node_status["a"] == NodeStatus.SKIPPED
    assert run.node_status["b"] == NodeStatus.SKIPPED


@pytest.mark.asyncio
async def test_downstream_can_opt_out_of_skip_propagation():
    calls = []

    def b_fn(ctx):
        calls.append("b ran")

    wf = Workflow([
        WorkflowNode(name="a", fn=lambda ctx: None, condition=lambda ctx: False),
        WorkflowNode(name="b", fn=b_fn, depends_on=("a",), skip_if_dependency_skipped=False),
    ])
    run = await wf.run({})
    assert run.node_status["a"] == NodeStatus.SKIPPED
    assert run.node_status["b"] == NodeStatus.COMPLETED
    assert calls == ["b ran"]


@pytest.mark.asyncio
async def test_node_failure_short_circuits_and_records_error():
    def failing(ctx):
        raise RuntimeError("boom")

    def never_runs(ctx):
        raise AssertionError("should not execute after failure")

    wf = Workflow([
        WorkflowNode(name="fail", fn=failing),
        WorkflowNode(name="after", fn=never_runs, depends_on=("fail",)),
    ])
    run = await wf.run({})
    assert run.is_failed
    assert run.failed_at == "fail"
    assert "boom" in run.error
    assert run.node_status["after"] == NodeStatus.PENDING


@pytest.mark.asyncio
async def test_pause_and_resume_continues_from_pause_point():
    calls = []

    def before(ctx):
        calls.append("before")
        return "ready"

    def needs_approval(ctx):
        calls.append("needs_approval-attempt")
        if not ctx.get("approved"):
            raise WorkflowPaused(payload={"reason": "manual approval required"})
        return "approved!"

    def after(ctx):
        calls.append("after")
        return ctx["approval_out"]

    wf = Workflow([
        WorkflowNode(name="before", fn=before, output_key="before_out"),
        WorkflowNode(name="approval", fn=needs_approval, depends_on=("before",), output_key="approval_out"),
        WorkflowNode(name="after", fn=after, depends_on=("approval",), output_key="after_out"),
    ])

    run = await wf.run({})
    assert run.is_paused
    assert run.paused_at == "approval"
    assert run.pause_payload == {"reason": "manual approval required"}
    assert calls == ["before", "needs_approval-attempt"]

    run = await wf.resume(run, resume_context={"approved": True})
    assert run.is_completed
    assert run.context["after_out"] == "approved!"
    assert calls == ["before", "needs_approval-attempt", "needs_approval-attempt", "after"]


@pytest.mark.asyncio
async def test_resume_does_not_rerun_already_completed_nodes():
    calls = []

    def step1(ctx):
        calls.append("step1")
        if not ctx.get("resumed"):
            raise WorkflowPaused()
        return "done"

    def step2(ctx):
        calls.append("step2")

    wf = Workflow([
        WorkflowNode(name="step1", fn=step1, output_key="out"),
        WorkflowNode(name="step2", fn=step2, depends_on=("step1",)),
    ])
    run = await wf.run({})
    assert run.is_paused

    run = await wf.resume(run, resume_context={"resumed": True})
    assert calls == ["step1", "step1", "step2"]
    assert run.is_completed


def test_duplicate_node_names_raise_value_error():
    with pytest.raises(ValueError, match="duplicate"):
        Workflow([
            WorkflowNode(name="dup", fn=lambda ctx: None),
            WorkflowNode(name="dup", fn=lambda ctx: None),
        ])


def test_unknown_dependency_raises_value_error():
    with pytest.raises(ValueError, match="unknown node"):
        Workflow([WorkflowNode(name="a", fn=lambda ctx: None, depends_on=("nonexistent",))])


def test_cycle_detection_raises_value_error():
    with pytest.raises(ValueError, match="cycle"):
        Workflow([
            WorkflowNode(name="a", fn=lambda ctx: None, depends_on=("b",)),
            WorkflowNode(name="b", fn=lambda ctx: None, depends_on=("a",)),
        ])


def test_node_names_returns_topological_order():
    wf = Workflow([
        WorkflowNode(name="c", fn=lambda ctx: None, depends_on=("a", "b")),
        WorkflowNode(name="a", fn=lambda ctx: None),
        WorkflowNode(name="b", fn=lambda ctx: None, depends_on=("a",)),
    ])
    order = wf.node_names
    assert order.index("a") < order.index("b") < order.index("c")


@pytest.mark.asyncio
async def test_diamond_shaped_dag_runs_correctly():
    """a -> b, a -> c, (b,c) -> d"""
    calls = []

    def make(name):
        def fn(ctx):
            calls.append(name)
            return name
        return fn

    wf = Workflow([
        WorkflowNode(name="a", fn=make("a"), output_key="a"),
        WorkflowNode(name="b", fn=make("b"), depends_on=("a",), output_key="b"),
        WorkflowNode(name="c", fn=make("c"), depends_on=("a",), output_key="c"),
        WorkflowNode(name="d", fn=make("d"), depends_on=("b", "c"), output_key="d"),
    ])
    run = await wf.run({})
    assert run.is_completed
    assert calls.index("a") < calls.index("b")
    assert calls.index("a") < calls.index("c")
    assert calls.index("b") < calls.index("d")
    assert calls.index("c") < calls.index("d")
