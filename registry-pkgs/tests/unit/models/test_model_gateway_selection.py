from beanie import PydanticObjectId

from registry_pkgs.models.model_gateway_selection import ModelGatewaySelection


def test_selection_defaults_to_empty_slots() -> None:
    selection = ModelGatewaySelection.model_construct()
    assert selection.defaultWorkflowModelSourceId is None
    assert selection.embeddingModelSourceId is None
    assert selection.updatedBy is None


def test_selection_accepts_object_ids() -> None:
    workflow_id = PydanticObjectId()
    embedding_id = PydanticObjectId()
    selection = ModelGatewaySelection.model_construct(
        defaultWorkflowModelSourceId=workflow_id,
        embeddingModelSourceId=embedding_id,
    )
    assert selection.defaultWorkflowModelSourceId == workflow_id
    assert selection.embeddingModelSourceId == embedding_id
