import pytest

from registry_pkgs.models.enums import OnRejectPolicy, WorkflowNodeType
from registry_pkgs.models.workflow import HumanReviewSpec, _validate_human_review_for_node


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
