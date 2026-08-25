"""Tests for workflow model helpers."""

from registry_pkgs.models.enums import WorkflowNodeType
from registry_pkgs.models.workflow import LoopConfig, RouterChoice, WorkflowNode, collect_executor_keys


def _step(name: str, executor_key: str) -> WorkflowNode:
    return WorkflowNode(name=name, executor_key=executor_key, step_objective=f"Run {name}")


def test_collect_executor_keys_empty_tree() -> None:
    assert collect_executor_keys([]) == set()


def test_collect_executor_keys_walks_every_nested_container_and_deduplicates() -> None:
    nodes = [
        _step("root", "shared"),
        WorkflowNode(
            name="parallel",
            node_type=WorkflowNodeType.PARALLEL,
            children=[_step("parallel-a", "parallel-a"), _step("parallel-b", "shared")],
        ),
        WorkflowNode(
            name="condition",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="input.enabled",
            true_steps=[_step("true", "true-key")],
            false_steps=[_step("false", "false-key")],
        ),
        WorkflowNode(
            name="loop",
            node_type=WorkflowNodeType.LOOP,
            children=[_step("loop-step", "loop-key")],
            loop_config=LoopConfig(max_iterations=2),
        ),
        WorkflowNode(
            name="router",
            node_type=WorkflowNodeType.ROUTER,
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
