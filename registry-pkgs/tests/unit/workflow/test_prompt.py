"""Unit tests for render_step_prompt and DependencySpec."""

import re
from typing import Any

import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token

from registry_pkgs.workflows.prompt import DependencySpec, render_step_prompt


def _parse(markdown: str) -> list[Token]:
    return MarkdownIt().parse(markdown)


def _headings(tokens: list[Token]) -> list[tuple[int, str]]:
    return [
        (int(token.tag[1:]), tokens[index + 1].content)
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]


def _section_inline_content(tokens: list[Token], section_title: str) -> list[str]:
    section_start = next(
        index
        for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h1" and tokens[index + 1].content == section_title
    )
    content: list[str] = []
    for index in range(section_start + 3, len(tokens)):
        token = tokens[index]
        if token.type == "heading_open" and token.tag == "h1":
            break
        if token.type == "inline":
            content.append(token.content)
    return content


@pytest.mark.unit
class TestRenderStepPrompt:
    def test_goal_section_always_first(self) -> None:
        result = render_step_prompt(
            step_objective="summarise the report",
            workflow_description=None,
            dependencies=[],
            initial_input=None,
        )

        assert result.startswith("# Step Objective\n\nsummarise the report")

    def test_workflow_description_included_when_present(self) -> None:
        result = render_step_prompt(
            step_objective="do it",
            workflow_description="a multi-step research workflow",
            dependencies=[],
            initial_input=None,
        )

        assert (
            "# Workflow Context\n\n"
            "*This step is part of a larger multi-step workflow.*\n\n"
            "a multi-step research workflow"
        ) in result

    def test_workflow_description_omitted_when_none(self) -> None:
        result = render_step_prompt(
            step_objective="do it",
            workflow_description=None,
            dependencies=[],
            initial_input=None,
        )

        assert "# Workflow Context" not in result

    def test_initial_input_shown_for_entry_node_with_no_deps(self) -> None:
        result = render_step_prompt(
            step_objective="do it",
            workflow_description=None,
            dependencies=[],
            initial_input="please analyse this document",
        )

        assert "# Current Step Inputs\n\n## Workflow Trigger Input\n\nplease analyse this document" in result

    def test_initial_input_omitted_when_deps_present(self) -> None:
        dependencies = [DependencySpec(name="Step A", objective="fetch data", content="some content")]
        result = render_step_prompt(
            step_objective="do it",
            workflow_description=None,
            dependencies=dependencies,
            initial_input="should not appear",
        )

        assert "should not appear" not in result
        assert "Workflow Trigger Input" not in result

    def test_dependencies_listed_with_objectives(self) -> None:
        dependencies = [
            DependencySpec(name="Fetcher", objective="fetch raw data", content="raw"),
            DependencySpec(name="Parser", objective="parse the data", content=None),
        ]
        result = render_step_prompt(
            step_objective="synthesise",
            workflow_description=None,
            dependencies=dependencies,
            initial_input=None,
        )

        assert '## "Fetcher"\n\nfetch raw data' in result
        assert '## "Parser"\n\nparse the data' in result
        assert "fetch raw data." not in result
        assert "parse the data." not in result

    def test_dependency_objective_preserves_markdown(self) -> None:
        objective = "Fetch records.\n\n## Filtering Rules\n\nKeep active records."
        dependencies = [DependencySpec(name="Fetcher", objective=objective)]

        result = render_step_prompt(
            step_objective="consume records",
            workflow_description=None,
            dependencies=dependencies,
            initial_input=None,
        )

        assert f'## "Fetcher"\n\n{objective}' in result

    def test_current_step_inputs_only_for_deps_with_content(self) -> None:
        dependencies = [
            DependencySpec(name="Has Content", objective="obj", content="actual output"),
            DependencySpec(name="No Content", objective="obj2", content=None),
        ]
        result = render_step_prompt(
            step_objective="use them",
            workflow_description=None,
            dependencies=dependencies,
            initial_input=None,
        )

        assert '## "Has Content" — Output\n\nactual output' in result
        assert '"No Content" — Output' not in result

    def test_dependency_content_with_code_fence_is_rendered_unindented(self) -> None:
        content = "before\n```json\n{}\n```\nafter"
        dependencies = [DependencySpec(name="Markdown", objective="emit markdown", content=content)]

        result = render_step_prompt(
            step_objective="consume markdown",
            workflow_description=None,
            dependencies=dependencies,
            initial_input=None,
        )

        assert f'## "Markdown" — Output\n\n{content}' in result
        assert "  ```json" not in result

    def test_current_step_inputs_omitted_when_all_deps_have_no_content(self) -> None:
        dependencies = [DependencySpec(name="Pending", objective="fetch", content=None)]
        result = render_step_prompt(
            step_objective="wait",
            workflow_description=None,
            dependencies=dependencies,
            initial_input=None,
        )

        assert "# Current Step Inputs" not in result
        assert '## "Pending"\n\nfetch' in result

    def test_goal_only_when_no_deps_no_description_no_input(self) -> None:
        result = render_step_prompt(
            step_objective="standalone task",
            workflow_description=None,
            dependencies=[],
            initial_input=None,
        )

        assert result == "# Step Objective\n\nstandalone task"

    def test_sections_separated_by_blank_line(self) -> None:
        result = render_step_prompt(
            step_objective="do it",
            workflow_description="workflow ctx",
            dependencies=[],
            initial_input=None,
        )

        assert "do it\n\n# Workflow Context" in result
        assert "\n\n\n" not in result

    def test_falsy_content_zero_is_rendered(self) -> None:
        dependencies = [DependencySpec(name="Counter", objective="count items", content="0")]
        result = render_step_prompt(
            step_objective="check count",
            workflow_description=None,
            dependencies=dependencies,
            initial_input=None,
        )

        assert '## "Counter" — Output\n\n0' in result

    def test_dependency_spec_is_frozen(self) -> None:
        dependency = DependencySpec(name="A", objective="do A")

        with pytest.raises((AttributeError, TypeError)):
            dependency.name = "B"  # type: ignore[misc]

    def test_trigger_parameters_rendered_right_after_goal(self) -> None:
        trigger_parameters = '{\n  "memberId": "U123"\n}'
        result = render_step_prompt(
            step_objective="do it",
            workflow_description="a multi-step research workflow",
            dependencies=[],
            initial_input=None,
            trigger_parameters=trigger_parameters,
        )

        assert (
            "# Workflow Trigger Parameters\n\n"
            "*Fixed for this run; available to every step.*\n\n"
            f"```json\n{trigger_parameters}\n```"
        ) in result
        assert (
            result.index("# Step Objective")
            < result.index("# Workflow Trigger Parameters")
            < result.index("# Workflow Context")
        )

    def test_trigger_parameters_omitted_when_none(self) -> None:
        result = render_step_prompt(
            step_objective="do it",
            workflow_description=None,
            dependencies=[],
            initial_input=None,
            trigger_parameters=None,
        )

        assert "# Workflow Trigger Parameters" not in result

    def test_trigger_parameters_rendered_even_with_dependencies(self) -> None:
        dependencies = [DependencySpec(name="Step A", objective="fetch data", content="some content")]
        result = render_step_prompt(
            step_objective="do it",
            workflow_description=None,
            dependencies=dependencies,
            initial_input="should not appear",
            trigger_parameters='{\n  "memberId": "U123"\n}',
        )

        assert "# Workflow Trigger Parameters" in result
        assert '"memberId": "U123"' in result
        assert "should not appear" not in result

    def test_multi_paragraph_objective_survives_as_section_content(self) -> None:
        objective = "Do X.\n\nThen do Y."
        result = render_step_prompt(
            step_objective=objective,
            workflow_description=None,
            dependencies=[],
            initial_input=None,
        )
        tokens = _parse(result)

        assert _section_inline_content(tokens, "Step Objective") == ["Do X.", "Then do Y."]
        assert objective in result
        assert "**" not in result

    def test_user_heading_nests_below_step_objective_scaffold(self) -> None:
        result = render_step_prompt(
            step_objective="## User Heading\n\nBody",
            workflow_description="Workflow context",
            dependencies=[],
            initial_input=None,
        )

        assert _headings(_parse(result)) == [
            (1, "Step Objective"),
            (2, "User Heading"),
            (1, "Workflow Context"),
        ]

    @pytest.mark.parametrize(
        "arguments, expected_h1_titles",
        [
            ({}, ["Step Objective"]),
            ({"trigger_parameters": '{"key": "value"}'}, ["Step Objective", "Workflow Trigger Parameters"]),
            ({"workflow_description": "context"}, ["Step Objective", "Workflow Context"]),
            (
                {
                    "trigger_parameters": '{"key": "value"}',
                    "workflow_description": "context",
                },
                ["Step Objective", "Workflow Trigger Parameters", "Workflow Context"],
            ),
            (
                {"dependencies": [DependencySpec(name="Pending", objective="fetch")]},
                ["Step Objective", "Dependencies"],
            ),
            (
                {"dependencies": [DependencySpec(name="Ready", objective="fetch", content="output")]},
                ["Step Objective", "Dependencies", "Current Step Inputs"],
            ),
            (
                {
                    "trigger_parameters": '{"key": "value"}',
                    "dependencies": [DependencySpec(name="Ready", objective="fetch", content="output")],
                },
                ["Step Objective", "Workflow Trigger Parameters", "Dependencies", "Current Step Inputs"],
            ),
            ({"initial_input": "trigger"}, ["Step Objective", "Current Step Inputs"]),
        ],
    )
    def test_section_combinations_have_stable_markdown_structure(
        self,
        arguments: dict[str, Any],
        expected_h1_titles: list[str],
    ) -> None:
        result = render_step_prompt(
            step_objective="do it",
            workflow_description=arguments.get("workflow_description"),
            dependencies=arguments.get("dependencies", []),
            initial_input=arguments.get("initial_input"),
            trigger_parameters=arguments.get("trigger_parameters"),
        )
        parsed_headings = _headings(_parse(result))
        source_heading_count = sum(1 for line in result.splitlines() if re.match(r"^#{1,6} ", line))

        assert re.search(r"\n{3,}", result) is None
        assert [title for level, title in parsed_headings if level == 1] == expected_h1_titles
        assert len(parsed_headings) == source_heading_count
