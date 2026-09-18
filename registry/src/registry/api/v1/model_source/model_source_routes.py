import logging
import math

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status

from registry_pkgs.models.enums import ModelSourceMode, ModelSourceProviderType
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig, ModelSource

from ....auth.dependencies import CurrentUser
from ....core.telemetry_decorators import track_registry_operation
from ....deps import get_model_gateway_selection_service, get_model_source_crud_service
from ....schemas.errors import ErrorCode, create_error_detail
from ....schemas.model_source_api_schemas import (
    AwsBedrockModelConfigResponse,
    AzureOpenAIModelConfigResponse,
    ModelGatewaySelectionResponse,
    ModelSourceCreateRequest,
    ModelSourceDeleteResponse,
    ModelSourceDetailResponse,
    ModelSourceListItemResponse,
    ModelSourcePagedResponse,
    ModelSourceProviderConfigResponse,
    ModelSourceUpdateRequest,
    SetDefaultWorkflowModelRequest,
)
from ....schemas.server_api_schemas import PaginationMetadata
from ....services.model_gateway_selection_service import (
    ModelGatewaySelectionService,
    ModelSourceModeMismatchError,
    ModelSourceNotFoundError,
)
from ....services.model_metadata_service import get_model_metadata
from ....services.model_source_crud_service import ModelSourceCrudService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Model Source Management"])


def _to_response_config(config: AwsBedrockModelConfig | AzureOpenAIModelConfig) -> ModelSourceProviderConfigResponse:
    if isinstance(config, AwsBedrockModelConfig):
        return AwsBedrockModelConfigResponse(
            awsRegion=config.awsRegion,
            modelIdOrArn=config.modelIdOrArn,
            baseModelId=config.baseModelId,
        )
    return AzureOpenAIModelConfigResponse(
        endpoint=config.endpoint,
        deploymentName=config.deploymentName,
        baseModelId=config.baseModelId,
        apiVersion=config.apiVersion,
        hasApiKey=bool(config.apiKeyEncrypted),
    )


def _to_list_item(source: ModelSource) -> ModelSourceListItemResponse:
    return ModelSourceListItemResponse(
        id=str(source.id),
        displayName=source.displayName,
        description=source.description,
        tags=source.tags,
        mode=source.mode,
        providerType=source.providerConfig.providerType,
        createdAt=source.createdAt,
        updatedAt=source.updatedAt,
    )


def _to_detail(source: ModelSource, *, include_metadata: bool) -> ModelSourceDetailResponse:
    base = _to_list_item(source)
    return ModelSourceDetailResponse(
        **base.model_dump(),
        providerConfig=_to_response_config(source.providerConfig),
        metadata=get_model_metadata(source.providerConfig) if include_metadata else None,
        createdBy=source.createdBy,
        updatedBy=source.updatedBy,
    )


async def _required_source(model_source_id: str, service: ModelSourceCrudService) -> ModelSource:
    source = await service.get_source(model_source_id)
    if source is None:
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            detail=create_error_detail(ErrorCode.NOT_FOUND, "Model source not found"),
        )
    return source


@router.post("/model-sources", response_model=ModelSourceDetailResponse, status_code=http_status.HTTP_201_CREATED)
@track_registry_operation("create", resource_type="model_source")
async def create_model_source(
    data: ModelSourceCreateRequest,
    user_context: CurrentUser,
    service: ModelSourceCrudService = Depends(get_model_source_crud_service),
):
    try:
        source = await service.create_source(
            display_name=data.displayName,
            description=data.description,
            tags=data.tags,
            mode=data.mode,
            provider_config=data.providerConfig,
            created_by=str(user_context["user_id"]),
        )
        return _to_detail(source, include_metadata=False)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to create model source")
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
        ) from exc


@router.get("/model-sources", response_model=ModelSourcePagedResponse)
@track_registry_operation("list", resource_type="model_source")
async def list_model_sources(
    user_context: CurrentUser,
    mode: ModelSourceMode | None = Query(default=None),
    providerType: ModelSourceProviderType | None = Query(default=None),
    tag: str | None = Query(default=None),
    query: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    service: ModelSourceCrudService = Depends(get_model_source_crud_service),
):
    try:
        items, total = await service.list_sources(
            mode=mode,
            provider_type=providerType,
            tag=tag,
            keyword=query,
            page=page,
            page_size=per_page,
        )
        return ModelSourcePagedResponse(
            modelSources=[_to_list_item(source) for source in items],
            pagination=PaginationMetadata(
                total=total,
                page=page,
                perPage=per_page,
                totalPages=math.ceil(total / per_page) if total else 0,
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list model sources")
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
        ) from exc


@router.get("/model-sources/{model_source_id}", response_model=ModelSourceDetailResponse)
@track_registry_operation("read", resource_type="model_source")
async def get_model_source(
    model_source_id: str,
    user_context: CurrentUser,
    service: ModelSourceCrudService = Depends(get_model_source_crud_service),
):
    try:
        source = await _required_source(model_source_id, service)
        return _to_detail(source, include_metadata=True)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get model source %s", model_source_id)
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
        ) from exc


@router.patch("/model-sources/{model_source_id}", response_model=ModelSourceDetailResponse)
@track_registry_operation("update", resource_type="model_source")
async def update_model_source(
    model_source_id: str,
    data: ModelSourceUpdateRequest,
    user_context: CurrentUser,
    service: ModelSourceCrudService = Depends(get_model_source_crud_service),
):
    try:
        source = await _required_source(model_source_id, service)
        changes = {field: getattr(data, field) for field in data.model_fields_set}
        source = await service.update_source(source, changes, updated_by=str(user_context["user_id"]))
        return _to_detail(source, include_metadata=False)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            detail=create_error_detail(ErrorCode.CONFLICT, str(exc)),
        ) from exc
    except Exception as exc:
        logger.exception("Failed to update model source %s", model_source_id)
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
        ) from exc


@router.delete("/model-sources/{model_source_id}", response_model=ModelSourceDeleteResponse)
@track_registry_operation("delete", resource_type="model_source")
async def delete_model_source(
    model_source_id: str,
    user_context: CurrentUser,
    service: ModelSourceCrudService = Depends(get_model_source_crud_service),
):
    try:
        source = await _required_source(model_source_id, service)
        if await service.is_in_use(model_source_id):
            raise HTTPException(
                http_status.HTTP_409_CONFLICT,
                detail=create_error_detail(
                    ErrorCode.CONFLICT,
                    "Model source is in use and cannot be deleted",
                ),
            )
        source = await service.soft_delete(source)
        return ModelSourceDeleteResponse(id=str(source.id), deletedAt=source.deletedAt)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to delete model source %s", model_source_id)
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
        ) from exc


@router.get("/model-gateway/selection", response_model=ModelGatewaySelectionResponse)
@track_registry_operation("read", resource_type="model_gateway_selection")
async def get_model_gateway_selection(
    user_context: CurrentUser,
    selection_service: ModelGatewaySelectionService = Depends(get_model_gateway_selection_service),
):
    try:
        selection = await selection_service.get_selection()
        return ModelGatewaySelectionResponse(
            defaultWorkflowModelSourceId=(
                str(selection.defaultWorkflowModelSourceId) if selection.defaultWorkflowModelSourceId else None
            ),
            embeddingModelSourceId=(
                str(selection.embeddingModelSourceId) if selection.embeddingModelSourceId else None
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get model gateway selection")
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
        ) from exc


@router.put(
    "/model-gateway/selection/default-workflow-model",
    response_model=ModelGatewaySelectionResponse,
)
@track_registry_operation("set_default_workflow_model", resource_type="model_gateway_selection")
async def set_default_workflow_model(
    data: SetDefaultWorkflowModelRequest,
    user_context: CurrentUser,
    selection_service: ModelGatewaySelectionService = Depends(get_model_gateway_selection_service),
):
    try:
        selection = await selection_service.set_default_workflow_model(
            data.modelSourceId,
            updated_by=str(user_context["user_id"]),
        )
        return ModelGatewaySelectionResponse(
            defaultWorkflowModelSourceId=str(selection.defaultWorkflowModelSourceId),
            embeddingModelSourceId=(
                str(selection.embeddingModelSourceId) if selection.embeddingModelSourceId else None
            ),
        )
    except ModelSourceNotFoundError as exc:
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND, detail=create_error_detail(ErrorCode.NOT_FOUND, str(exc))
        ) from exc
    except ModelSourceModeMismatchError as exc:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT, detail=create_error_detail(ErrorCode.CONFLICT, str(exc))
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to set default workflow model")
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
        ) from exc
