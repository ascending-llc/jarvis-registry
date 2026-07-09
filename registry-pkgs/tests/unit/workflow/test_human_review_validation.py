import pytest

from registry_pkgs.models.enums import OnRejectPolicy, WorkflowNodeType
from registry_pkgs.models.workflow import HumanReviewSpec, WorkflowNode, _validate_human_review_for_node


def _condition_node(*, on_reject: OnRejectPolicy, with_false_steps: bool) -> dict:
    """Kwargs for a CONDITION WorkflowNode with a confirmation gate."""
    return {
        "name": "branch",
        "node_type": WorkflowNodeType.CONDITION,
        "condition_cel": "session_state.x != ''",
        "true_steps": [WorkflowNode(name="t", executor_key="tool", step_objective="run true branch")],
        "false_steps": [WorkflowNode(name="f", executor_key="tool", step_objective="run false branch")]
        if with_false_steps
        else [],
        "human_review": HumanReviewSpec(requires_confirmation=True, on_reject=on_reject),
    }


@pytest.mark.unit
class TestElseBranchValidation:
    @pytest.mark.parametrize(
        "node_type",
        [
            WorkflowNodeType.STEP,
            WorkflowNodeType.ROUTER,
            WorkflowNodeType.LOOP,
            WorkflowNodeType.PARALLEL,
        ],
    )
    def test_else_branch_rejected_on_non_condition_node(self, node_type: WorkflowNodeType):
        spec = HumanReviewSpec(on_reject=OnRejectPolicy.ELSE_BRANCH)
        with pytest.raises(ValueError, match="else_branch is only supported on condition"):
            _validate_human_review_for_node(node_type, spec)

    def test_else_branch_allowed_on_condition_node(self):
        spec = HumanReviewSpec(requires_confirmation=True, on_reject=OnRejectPolicy.ELSE_BRANCH)
        # Must not raise — else_branch is the one node type where it is valid.
        _validate_human_review_for_node(WorkflowNodeType.CONDITION, spec)

    def test_non_else_branch_policy_unaffected_on_step_node(self):
        spec = HumanReviewSpec(requires_confirmation=True, on_reject=OnRejectPolicy.SKIP)
        # Regression guard: the new check must not reject the default policy.
        _validate_human_review_for_node(WorkflowNodeType.STEP, spec)


@pytest.mark.unit
class TestElseBranchRequiresFalseSteps:
    """on_reject=else_branch routes a rejected gate into the false branch, so the
    definition must declare one (caught at save time, not at runtime)."""

    def test_else_branch_without_false_steps_rejected(self):
        with pytest.raises(ValueError, match="requires at least one false_steps entry"):
            WorkflowNode(**_condition_node(on_reject=OnRejectPolicy.ELSE_BRANCH, with_false_steps=False))

    def test_else_branch_with_false_steps_ok(self):
        # Must not raise — the false branch the rejection routes into exists.
        WorkflowNode(**_condition_node(on_reject=OnRejectPolicy.ELSE_BRANCH, with_false_steps=True))

    def test_non_else_branch_condition_allows_empty_false_steps(self):
        # Regression guard: other policies don't require a false branch.
        WorkflowNode(**_condition_node(on_reject=OnRejectPolicy.SKIP, with_false_steps=False))
