"""Tests for workflow model helpers."""

import pytest

from registry_pkgs.models.enums import WorkflowNodeType
from registry_pkgs.models.workflow import LoopConfig, RouterChoice, WorkflowNode, collect_executor_keys


def _step(name: str, executor_key: str) -> WorkflowNode:
    return WorkflowNode(name=name, executor_key=executor_key, step_objective=f"Run {name}")


def test_step_objective_preserves_multi_paragraph_content() -> None:
    objective = "Do X.\n\nThen do Y."

    node = WorkflowNode(name="multi-paragraph", executor_key="tool", step_objective=objective)

    assert node.step_objective == objective


def test_step_objective_normalizes_line_endings() -> None:
    node = WorkflowNode(name="line-endings", executor_key="tool", step_objective="First\r\nSecond\rThird")

    assert node.step_objective == "First\nSecond\nThird"


def test_step_objective_strips_only_outer_whitespace() -> None:
    node = WorkflowNode(name="outer-whitespace", executor_key="tool", step_objective=" \tFirst\n  Second\t ")

    assert node.step_objective == "First\n  Second"


def test_step_objective_normalizes_all_whitespace_to_none() -> None:
    assert WorkflowNode._normalize_step_objective(" \t\r\n ") is None
    with pytest.raises(ValueError, match="step node requires step_objective"):
        WorkflowNode(name="empty-objective", executor_key="tool", step_objective=" \t\r\n ")


def test_collect_executor_keys_empty_tree() -> None:
    assert collect_executor_keys([]) == set()


def test_collect_executor_keys_walks_every_nested_container_and_deduplicates() -> None:
    nodes = [
        _step("root", "shared"),
        WorkflowNode(
            name="parallel",
            node_type=WorkflowNodeType.PARALLEL,
            executor_key="ignored-parallel-key",
            children=[_step("parallel-a", "parallel-a"), _step("parallel-b", "shared")],
        ),
        WorkflowNode(
            name="condition",
            node_type=WorkflowNodeType.CONDITION,
            executor_key="ignored-condition-key",
            condition_cel="input.enabled",
            true_steps=[_step("true", "true-key")],
            false_steps=[_step("false", "false-key")],
        ),
        WorkflowNode(
            name="loop",
            node_type=WorkflowNodeType.LOOP,
            executor_key="ignored-loop-key",
            children=[_step("loop-step", "loop-key")],
            loop_config=LoopConfig(max_iterations=2),
        ),
        WorkflowNode(
            name="router",
            node_type=WorkflowNodeType.ROUTER,
            executor_key="ignored-router-key",
            condition_cel="input.route",
            choices=[
                RouterChoice(name="first", steps=[_step("choice-a", "choice-a")]),
                RouterChoice(name="second", steps=[_step("choice-b", "choice-b")]),
            ],
        ),
    ]

    assert collect_executor_keys(nodes) == {
        "shared",
        "parallel-a",
        "true-key",
        "false-key",
        "loop-key",
        "choice-a",
        "choice-b",
    }


def test_collect_executor_keys_excludes_a2a_pool_steps() -> None:
    node = WorkflowNode(
        name="a2a-pool",
        a2a_pool=["agent-a", "agent-b"],
        step_objective="Delegate to the pool",
    )

    assert collect_executor_keys([node]) == set()
