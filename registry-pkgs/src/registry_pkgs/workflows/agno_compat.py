"""Narrow compatibility fixes for the registry's agno 2.7.2 execution path."""

from typing import Any

from agno.run import RunContext
from agno.run.workflow import WorkflowRunOutput
from agno.session.workflow import WorkflowSession
from agno.workflow import Router, Workflow
from agno.workflow.types import WorkflowExecutionInput


class RegistryWorkflow(Workflow):
    """Honor stop signals after manual routing in async, non-streaming runs.

    Agno 2.7.2's special router-selection continuation skips its stop check.
    Bind the user's choice to the selector instead, then reuse the ordinary
    continuation path, including output review, stop handling and persistence.
    Revisit this adapter when upgrading agno; the registry uses neither sync
    nor streaming execution.
    """

    async def _acontinue_execute(
        self,
        session: WorkflowSession,
        execution_input: WorkflowExecutionInput,
        workflow_run_response: WorkflowRunOutput,
        run_context: RunContext,
        start_step_index: int,
        background_tasks: Any = None,
        **kwargs: Any,
    ) -> WorkflowRunOutput:
        router = None
        original_selector = None
        selection = kwargs.get("router_selection")
        if selection and isinstance(self.steps, list) and start_step_index < len(self.steps):
            step = self.steps[start_step_index]
            if isinstance(step, Router):
                step._prepare_steps()
                selected_steps = step._get_steps_from_user_selection(selection)
                router = step
                original_selector = router.selector
                router.selector = lambda _: selected_steps
                kwargs.pop("router_selection")

        try:
            return await super()._acontinue_execute(
                session=session,
                execution_input=execution_input,
                workflow_run_response=workflow_run_response,
                run_context=run_context,
                start_step_index=start_step_index,
                background_tasks=background_tasks,
                **kwargs,
            )
        finally:
            if router is not None:
                router.selector = original_selector
