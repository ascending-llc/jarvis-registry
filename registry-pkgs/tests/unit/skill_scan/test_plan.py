from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.enums import ModelSourceMode, ModelSourceProviderType
from registry_pkgs.models.model_source import (
    AnthropicModelConfig,
    AwsBedrockModelConfig,
    AzureOpenAIModelConfig,
    ModelSource,
    OpenAIModelConfig,
)
from registry_pkgs.models.skill_scan_result import ScanLLMInfo, SkillScanResult
from registry_pkgs.models.skill_scan_toolset import SkillScanToolDescriptor
from registry_pkgs.skill_scan import plan


def _bedrock_source(source_id: PydanticObjectId, *, mode=ModelSourceMode.CHAT, deleted=False) -> ModelSource:
    # model_construct bypasses Beanie's __init__ collection check, keeping this a unit test.
    src = ModelSource.model_construct(
        displayName="scan",
        mode=mode,
        providerConfig=AwsBedrockModelConfig(
            awsRegion="us-east-1", modelIdOrArn="anthropic.claude-x", baseModelId="anthropic.claude-x"
        ),
        deletedAt=None,
    )
    src.id = source_id
    if deleted:
        from datetime import UTC, datetime

        src.deletedAt = datetime.now(UTC)
    return src


def test_compose_tool_fingerprint_sensitivity():
    llm = ScanLLMInfo(modelSourceId=PydanticObjectId(), providerType=ModelSourceProviderType.AWS_BEDROCK, modelId="m")
    base = plan.compose_tool_fingerprint("static", llm)
    assert plan.compose_tool_fingerprint("static", llm) == base  # stable
    assert plan.compose_tool_fingerprint("other", llm) != base  # static part
    other_source = ScanLLMInfo(
        modelSourceId=PydanticObjectId(), providerType=ModelSourceProviderType.AWS_BEDROCK, modelId="m"
    )
    assert plan.compose_tool_fingerprint("static", other_source) != base  # modelSourceId
    other_model = ScanLLMInfo(
        modelSourceId=llm.modelSourceId, providerType=ModelSourceProviderType.AWS_BEDROCK, modelId="m2"
    )
    assert plan.compose_tool_fingerprint("static", other_model) != base  # modelId
    other_config = ScanLLMInfo(
        modelSourceId=llm.modelSourceId,
        providerType=ModelSourceProviderType.AWS_BEDROCK,
        modelId="m",
        configDigest="deadbeef",
    )
    assert plan.compose_tool_fingerprint("static", other_config) != base  # configDigest
    assert plan.compose_tool_fingerprint("static", None) != base  # no llm


def test_scan_llm_info_per_provider():
    bedrock_id = PydanticObjectId()
    info = plan.scan_llm_info(_bedrock_source(bedrock_id))
    assert info is not None and info.modelId == "anthropic.claude-x" and info.modelSourceId == bedrock_id

    def _azure(endpoint: str, *, api_key: str | None = None) -> ModelSource:
        src = ModelSource.model_construct(
            displayName="scan",
            mode=ModelSourceMode.CHAT,
            providerConfig=AzureOpenAIModelConfig(
                endpoint=endpoint,
                deploymentName="gpt-x-deploy",
                baseModelId="gpt-4o",
                apiVersion="2024-10-21",
                apiKeyEncrypted=api_key,
            ),
        )
        src.id = azure_id
        return src

    azure_id = PydanticObjectId()
    azure = _azure("https://x.openai.azure.com")
    azure_info = plan.scan_llm_info(azure)
    assert azure_info.modelId == "gpt-x-deploy"
    azure_fp = plan.compose_tool_fingerprint("s", azure_info)

    # Same source id and deployment name, endpoint changed in place -> new fingerprint (no stale reuse).
    moved = plan.scan_llm_info(_azure("https://y.openai.azure.com"))
    assert plan.compose_tool_fingerprint("s", moved) != azure_fp

    # Rotating only the secret api key must NOT change the fingerprint.
    rekeyed = plan.scan_llm_info(_azure("https://x.openai.azure.com", api_key="rotated"))
    assert plan.compose_tool_fingerprint("s", rekeyed) == azure_fp

    # Editing a metadata-only field (baseModelId) must NOT change the fingerprint: it is not on the allowlist.
    relabelled = _azure("https://x.openai.azure.com")
    relabelled.providerConfig = relabelled.providerConfig.model_copy(update={"baseModelId": "gpt-4o-mini"})
    assert plan.compose_tool_fingerprint("s", plan.scan_llm_info(relabelled)) == azure_fp

    assert plan.scan_llm_info(None) is None


def test_bedrock_config_digest_uses_identity_fields_only():
    sid = PydanticObjectId()
    base = plan.scan_llm_info(_bedrock_source(sid))

    moved = _bedrock_source(sid)
    moved.providerConfig = moved.providerConfig.model_copy(update={"awsRegion": "eu-west-1"})
    assert plan.scan_llm_info(moved).configDigest != base.configDigest  # region is identity

    relabelled = _bedrock_source(sid)
    relabelled.providerConfig = relabelled.providerConfig.model_copy(update={"baseModelId": "anthropic.other"})
    assert plan.scan_llm_info(relabelled).configDigest == base.configDigest  # metadata only


def _first_party_source(config: AnthropicModelConfig | OpenAIModelConfig, source_id: PydanticObjectId) -> ModelSource:
    src = ModelSource.model_construct(displayName="scan", mode=ModelSourceMode.CHAT, providerConfig=config)
    src.id = source_id
    return src


@pytest.mark.parametrize("config_cls", [AnthropicModelConfig, OpenAIModelConfig])
def test_scan_llm_info_first_party(config_cls):
    sid = PydanticObjectId()

    def _info(**overrides):
        fields = {"modelId": "model-x", "baseUrl": None, "apiKeyEncrypted": "iv:key"} | overrides
        return plan.scan_llm_info(_first_party_source(config_cls(**fields), sid))

    base = _info()
    assert (
        base is not None
        and base.modelId == "model-x"
        and base.providerType == config_cls.model_fields["providerType"].default
    )
    fp = plan.compose_tool_fingerprint("s", base)

    # A different host (another compatible vendor) or model is a different serving deployment.
    assert plan.compose_tool_fingerprint("s", _info(baseUrl="https://proxy.example.com")) != fp
    assert plan.compose_tool_fingerprint("s", _info(modelId="model-y")) != fp
    # Rotating only the secret api key must NOT change the fingerprint.
    assert plan.compose_tool_fingerprint("s", _info(apiKeyEncrypted="iv:rotated")) == fp


@pytest.mark.asyncio
async def test_resolve_prefers_scan_source(monkeypatch):
    scan_id, default_id = PydanticObjectId(), PydanticObjectId()
    selection = type("Sel", (), {"skillScanModelSourceId": scan_id, "defaultWorkflowModelSourceId": default_id})()
    monkeypatch.setattr(plan, "get_model_gateway_selection", AsyncMock(return_value=selection))
    get = AsyncMock(return_value=_bedrock_source(scan_id))
    monkeypatch.setattr(plan.ModelSource, "get", get)

    result = await plan.resolve_skill_scan_model_source()
    assert result is not None
    get.assert_awaited_once_with(scan_id)


@pytest.mark.asyncio
async def test_resolve_falls_back_to_workflow_default(monkeypatch):
    default_id = PydanticObjectId()
    selection = type("Sel", (), {"skillScanModelSourceId": None, "defaultWorkflowModelSourceId": default_id})()
    monkeypatch.setattr(plan, "get_model_gateway_selection", AsyncMock(return_value=selection))
    get = AsyncMock(return_value=_bedrock_source(default_id))
    monkeypatch.setattr(plan.ModelSource, "get", get)

    await plan.resolve_skill_scan_model_source()
    get.assert_awaited_once_with(default_id)


@pytest.mark.asyncio
async def test_resolve_none_when_soft_deleted(monkeypatch):
    sid = PydanticObjectId()
    selection = type("Sel", (), {"skillScanModelSourceId": sid, "defaultWorkflowModelSourceId": None})()
    monkeypatch.setattr(plan, "get_model_gateway_selection", AsyncMock(return_value=selection))
    monkeypatch.setattr(plan.ModelSource, "get", AsyncMock(return_value=_bedrock_source(sid, deleted=True)))
    assert await plan.resolve_skill_scan_model_source() is None


@pytest.mark.asyncio
async def test_resolve_none_when_not_chat_mode(monkeypatch):
    sid = PydanticObjectId()
    selection = type("Sel", (), {"skillScanModelSourceId": sid, "defaultWorkflowModelSourceId": None})()
    monkeypatch.setattr(plan, "get_model_gateway_selection", AsyncMock(return_value=selection))
    monkeypatch.setattr(
        plan.ModelSource, "get", AsyncMock(return_value=_bedrock_source(sid, mode=ModelSourceMode.EMBEDDING))
    )
    assert await plan.resolve_skill_scan_model_source() is None


@pytest.mark.asyncio
async def test_resolve_none_when_nothing_configured(monkeypatch):
    selection = type("Sel", (), {"skillScanModelSourceId": None, "defaultWorkflowModelSourceId": None})()
    monkeypatch.setattr(plan, "get_model_gateway_selection", AsyncMock(return_value=selection))
    assert await plan.resolve_skill_scan_model_source() is None


@pytest.mark.asyncio
async def test_tools_needing_scan_omits_done(monkeypatch):
    class _Cursor:
        def __init__(self, docs):
            self._docs = docs

        def __aiter__(self):
            async def gen():
                for d in self._docs:
                    yield d

            return gen()

    class _Coll:
        def find(self, *a, **k):
            return _Cursor([{"toolFingerprint": "fp1"}])  # only cisco is done

    monkeypatch.setattr(SkillScanResult, "get_pymongo_collection", staticmethod(lambda: _Coll()))
    needing = await plan.tools_needing_scan(PydanticObjectId(), "sha256:d", {"cisco": "fp1", "spector": "fp2"})
    assert needing == ["spector"]


@pytest.mark.asyncio
async def test_current_tool_fingerprints_composes_per_tool(monkeypatch):
    toolset = type(
        "TS", (), {"tools": [SkillScanToolDescriptor(tool="cisco", toolVersion="1", staticFingerprint="s1")]}
    )()
    monkeypatch.setattr(plan.SkillScanToolset, "get", AsyncMock(return_value=toolset))
    monkeypatch.setattr(plan, "resolve_skill_scan_model_source", AsyncMock(return_value=None))  # no LLM configured

    fingerprints = await plan.current_tool_fingerprints()
    assert fingerprints == {"cisco": plan.compose_tool_fingerprint("s1", None)}


@pytest.mark.asyncio
async def test_current_tool_fingerprints_none_without_toolset(monkeypatch):
    monkeypatch.setattr(plan.SkillScanToolset, "get", AsyncMock(return_value=None))
    assert await plan.current_tool_fingerprints() is None
