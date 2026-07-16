"""Unit tests for render_step_prompt and DependencySpec."""

import pytest

from registry_pkgs.workflows.prompt import DependencySpec, render_step_prompt


@pytest.mark.unit
class TestRenderStepPrompt:
    def test_goal_line_always_first(self):
        result = render_step_prompt(
            step_objective="summarise the report",
            workflow_description=None,
            dependencies=[],
            initial_input=None,
        )
        assert result.startswith("**IMPORTANT: The goal of this step is to summarise the report.**")

    def test_workflow_description_included_when_present(self):
        result = render_step_prompt(
            step_objective="do it",
            workflow_description="a multi-step research workflow",
            dependencies=[],
            initial_input=None,
        )
        assert "This step is part of a larger workflow: a multi-step research workflow" in result

    def test_workflow_description_omitted_when_none(self):
        result = render_step_prompt(
            step_objective="do it",
            workflow_description=None,
            dependencies=[],
            initial_input=None,
        )
        assert "larger workflow" not in result

    def test_initial_input_shown_for_entry_node_with_no_deps(self):
        result = render_step_prompt(
            step_objective="do it",
            workflow_description=None,
            dependencies=[],
            initial_input="please analyse this document",
        )
        assert "Workflow trigger input" in result
        assert "please analyse this document" in result

    def test_initial_input_omitted_when_deps_present(self):
        deps = [DependencySpec(name="Step A", objective="fetch data", content="some content")]
        result = render_step_prompt(
            step_objective="do it",
            workflow_description=None,
            dependencies=deps,
            initial_input="should not appear",
        )
        assert "should not appear" not in result
        assert "Workflow trigger input" not in result

    def test_dependencies_listed_with_objectives(self):
        deps = [
            DependencySpec(name="Fetcher", objective="fetch raw data", content="raw"),
            DependencySpec(name="Parser", objective="parse the data", content=None),
        ]
        result = render_step_prompt(
            step_objective="synthesise",
            workflow_description=None,
            dependencies=deps,
            initial_input=None,
        )
        assert '"Fetcher": fetch raw data.' in result
        assert '"Parser": parse the data.' in result

    def test_current_step_inputs_only_for_deps_with_content(self):
        deps = [
            DependencySpec(name="Has Content", objective="obj", content="actual output"),
            DependencySpec(name="No Content", objective="obj2", content=None),
        ]
        result = render_step_prompt(
            step_objective="use them",
            workflow_description=None,
            dependencies=deps,
            initial_input=None,
        )
        assert '"Has Content" outputs:' in result
        assert "actual output" in result
        assert '"No Content" outputs:' not in result

    def test_dependency_content_with_code_fence_is_rendered_as_indented_block(self):
        deps = [DependencySpec(name="Markdown", objective="emit markdown", content="before\n```json\n{}\n```\nafter")]
        result = render_step_prompt(
            step_objective="consume markdown",
            workflow_description=None,
            dependencies=deps,
            initial_input=None,
        )

        assert "  ```json" in result
        assert "\n  {}\n" in result
        assert "\n  ```\n" in result

    def test_current_step_inputs_omitted_when_all_deps_have_no_content(self):
        deps = [DependencySpec(name="Pending", objective="fetch", content=None)]
        result = render_step_prompt(
            step_objective="wait",
            workflow_description=None,
            dependencies=deps,
            initial_input=None,
        )
        assert "Current Step Inputs" not in result
        # Dependencies section still present
        assert '"Pending": fetch.' in result

    def test_goal_only_when_no_deps_no_description_no_input(self):
        result = render_step_prompt(
            step_objective="standalone task",
            workflow_description=None,
            dependencies=[],
            initial_input=None,
        )
        assert result == "**IMPORTANT: The goal of this step is to standalone task.**"

    def test_sections_separated_by_double_newline(self):
        result = render_step_prompt(
            step_objective="do it",
            workflow_description="workflow ctx",
            dependencies=[],
            initial_input=None,
        )
        # Goal + workflow description = 2 sections joined with \n\n
        parts = result.split("\n\n")
        assert len(parts) == 2
        assert "do it" in parts[0]
        assert "workflow ctx" in parts[1]

    def test_falsy_content_zero_is_rendered(self):
        deps = [DependencySpec(name="Counter", objective="count items", content="0")]
        result = render_step_prompt(
            step_objective="check count",
            workflow_description=None,
            dependencies=deps,
            initial_input=None,
        )
        assert '"Counter" outputs:' in result
        assert "0" in result

    def test_dependency_spec_is_frozen(self):
        dep = DependencySpec(name="A", objective="do A")
        with pytest.raises((AttributeError, TypeError)):
            dep.name = "B"  # type: ignore[misc]
