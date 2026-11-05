"""Scan-planning helpers shared by Registry and the scanner worker, so both apply one rule set.

The fingerprint's LLM part is the *configured* model, not whether a given adapter can use it: Registry can
compute it without adapter logic, and a run where an adapter fell back to no-LLM still records the configured
fingerprint with status ``partial``.
"""

import hashlib
import json

from beanie import PydanticObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from ..database.model_gateway_selection_repository import get_model_gateway_selection
from ..models.enums import ModelSourceMode, SkillScanResultStatus
from ..models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig, ModelSource
from ..models.skill_scan_job import SkillScanJob
from ..models.skill_scan_result import ScanLLMInfo, SkillScanResult
from ..models.skill_scan_toolset import SKILL_SCAN_TOOLSET_ID, SkillScanToolset

_DONE_STATUSES = (SkillScanResultStatus.SUCCEEDED.value, SkillScanResultStatus.SKIPPED.value)

# Config fields that are secrets, not model identity: excluded from the fingerprint so a key rotation
# does not invalidate caches, and so no secret enters a fingerprint digest.
_SECRET_CONFIG_FIELDS = frozenset({"apiKeyEncrypted"})


def _provider_config_digest(config: AwsBedrockModelConfig | AzureOpenAIModelConfig) -> str:
    """sha256 of the non-secret provider config, so any change to which resource/model is served invalidates caches."""
    data = config.model_dump(mode="json", exclude=_SECRET_CONFIG_FIELDS)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


async def resolve_skill_scan_model_source() -> ModelSource | None:
    """skillScanModelSourceId, else defaultWorkflowModelSourceId.

    None when unset, missing, soft-deleted, or not mode CHAT. Mirrors resolve_model's fallback/checks.
    """
    selection = await get_model_gateway_selection(create_if_missing=False)
    if selection is None:
        return None
    source_id = selection.skillScanModelSourceId or selection.defaultWorkflowModelSourceId
    if source_id is None:
        return None
    source = await ModelSource.get(source_id)
    if source is None or source.deletedAt is not None or source.mode != ModelSourceMode.CHAT:
        return None
    return source


def scan_llm_info(source: ModelSource | None) -> ScanLLMInfo | None:
    """The ScanLLMInfo for the configured scan model, or None when none is configured."""
    if source is None or source.id is None:
        return None
    config = source.providerConfig
    if isinstance(config, AwsBedrockModelConfig):
        model_id = config.modelIdOrArn
    elif isinstance(config, AzureOpenAIModelConfig):
        model_id = config.deploymentName
    else:  # pragma: no cover — discriminated union is exhaustive
        return None
    return ScanLLMInfo(
        modelSourceId=source.id,
        providerType=config.providerType,
        modelId=model_id,
        configDigest=_provider_config_digest(config),
    )


def compose_tool_fingerprint(static_fingerprint: str, llm: ScanLLMInfo | None) -> str:
    """Combine an adapter's static fingerprint with the configured LLM so a model change invalidates caches."""
    model_source_id = str(llm.modelSourceId) if llm else "-"
    model_id = llm.modelId if llm else "-"
    config_digest = (llm.configDigest or "-") if llm else "-"
    raw = f"{static_fingerprint}|{model_source_id}|{model_id}|{config_digest}"
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def current_tool_fingerprints() -> dict[str, str] | None:
    """{tool: composed fingerprint} from the published toolset + resolved model; None if no toolset published."""
    toolset = await SkillScanToolset.get(SKILL_SCAN_TOOLSET_ID)
    if toolset is None:
        return None
    llm = scan_llm_info(await resolve_skill_scan_model_source())
    return {t.tool: compose_tool_fingerprint(t.staticFingerprint, llm) for t in toolset.tools}


async def tools_needing_scan(
    skill_id: PydanticObjectId, content_digest: str, fingerprints: dict[str, str]
) -> list[str]:
    """Tools whose (skillId, digest, fingerprint) result is missing or not SUCCEEDED/SKIPPED. One query."""
    done_cursor = SkillScanResult.get_pymongo_collection().find(
        {
            "skillId": skill_id,
            "contentDigest": content_digest,
            "toolFingerprint": {"$in": list(fingerprints.values())},
            "status": {"$in": list(_DONE_STATUSES)},
        },
        projection={"toolFingerprint": 1},
    )
    done = {doc["toolFingerprint"] async for doc in done_cursor}
    return [tool for tool, fingerprint in fingerprints.items() if fingerprint not in done]


async def delete_skill_scan_state(skill_id: PydanticObjectId, *, session: AsyncClientSession | None) -> None:
    """Delete the skill's SkillScanJob and all its SkillScanResult documents."""
    await SkillScanJob.get_pymongo_collection().delete_many({"skillId": skill_id}, session=session)
    await SkillScanResult.get_pymongo_collection().delete_many({"skillId": skill_id}, session=session)
