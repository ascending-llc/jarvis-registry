"""Read-only audit for stored federationMetadata before deploying strict models."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError

from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation_metadata import (
    A2AFederationMetadata,
    AgentCoreMcpFederationMetadata,
)

_BLOCKING_ISSUES = {
    "inconsistent_federation_state",
    "missing_provider_type",
    "invalid_provider",
    "missing_core_fields",
    "duplicate_runtime_arns",
    "azure_mcp_anomaly",
    "invalid_samples",
}
_ATTENTION_ISSUES = {
    "unknown_extras",
    "bad_datetime",
    "empty_runtime_arn",
}


@dataclass(frozen=True)
class CollectionAudit:
    collection: str
    total: int
    federated: int
    non_federated: int
    by_provider: dict[str, int]
    issue_counts: dict[str, int]
    inconsistent_federation_state: list[dict[str, Any]]
    missing_provider_type: list[str]
    invalid_provider: list[dict[str, Any]]
    missing_core_fields: list[dict[str, Any]]
    unknown_extras: list[dict[str, Any]]
    bad_datetime: list[dict[str, Any]]
    empty_runtime_arn: list[str]
    duplicate_runtime_arns: list[dict[str, Any]]
    azure_mcp_anomaly: list[str]
    invalid_samples: list[dict[str, Any]]


def _record_issue[SampleT](
    issue_counts: Counter[str],
    issue_name: str,
    samples: list[SampleT],
    sample: SampleT,
    sample_limit: int,
) -> None:
    issue_counts[issue_name] += 1
    if len(samples) < sample_limit:
        samples.append(sample)


def _provider_type_of(metadata: Any) -> str | None:
    if isinstance(metadata, dict):
        return metadata.get("providerType")
    return getattr(metadata, "providerType", None)


def _runtime_arn_of(metadata: Any) -> Any:
    if isinstance(metadata, dict):
        return metadata.get("runtimeArn")
    return getattr(metadata, "runtimeArn", None)


def _core_fields_for(provider_type: str | None) -> list[str]:
    if provider_type == FederationProviderType.AWS_AGENTCORE.value:
        return ["runtimeArn", "runtimeId", "runtimeVersion", "runtimeStatus"]
    if provider_type == FederationProviderType.AZURE_AI_FOUNDRY.value:
        return ["runtimeArn", "agentName", "agentVersion"]
    return []


def _is_bad_datetime(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, datetime):
        return False
    if isinstance(value, int):
        # Accept epoch seconds/milliseconds; Mongo driver usually returns datetime.
        return value < 0
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return False
        except ValueError:
            return True
    return True


def _find_bad_datetime_fields(metadata: dict[str, Any]) -> list[str]:
    datetime_fields = {"lastUpdatedAt", "createdAt", "modifiedAt", "enrichedAt"}
    bad: list[str] = []
    for key in datetime_fields:
        if key in metadata and _is_bad_datetime(metadata[key]):
            bad.append(key)
    return bad


def _find_unknown_extras(metadata: dict[str, Any], provider_type: str | None) -> list[str]:
    known_common = {
        "enrichmentError",
        "providerType",
        "runtimeArn",
        "lastUpdatedAt",
        "createdAt",
        "enrichedAt",
    }
    known_agentcore = {
        "runtimeId",
        "runtimeName",
        "runtimeVersion",
        "runtimeStatus",
        "serverProtocol",
        "failureReason",
        "workloadIdentityDetails",
        "protocolConfiguration",
        "authorizerConfiguration",
        "runtimeTags",
    }
    known_azure = {
        "agentName",
        "agentVersion",
        "agentGuid",
        "versionId",
        "status",
        "modifiedAt",
        "projectEndpoint",
        "a2aBaseUrl",
        "agentCardPath",
        "authorizationSchemes",
    }
    allowed = known_common
    if provider_type == FederationProviderType.AWS_AGENTCORE.value:
        allowed = allowed | known_agentcore
    elif provider_type == FederationProviderType.AZURE_AI_FOUNDRY.value:
        allowed = allowed | known_azure
    return [key for key in metadata if key not in allowed]


def _core_field_errors(metadata: dict[str, Any]) -> list[str]:
    provider_type = _provider_type_of(metadata)
    required = _core_fields_for(provider_type)
    missing: list[str] = []
    for field in required:
        value = metadata.get(field)
        if value is None or value == "":
            missing.append(field)
    return missing


def _is_azure_in_mcp(provider_type: str | None, collection: str) -> bool:
    return provider_type == FederationProviderType.AZURE_AI_FOUNDRY.value and collection == "mcpservers"


async def _audit_collection(
    *,
    collection_name: str,
    metadata_adapter: TypeAdapter[Any],
    sample_limit: int,
) -> CollectionAudit:
    collection = MongoDB.get_database().get_collection(collection_name)
    cursor = collection.find(
        {},
        {"federationMetadata": 1, "federationRefId": 1},
    )

    total = 0
    federated = 0
    non_federated = 0
    by_provider: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    inconsistent_federation_state: list[dict[str, Any]] = []
    missing_provider_type: list[str] = []
    invalid_provider: list[dict[str, Any]] = []
    missing_core_fields: list[dict[str, Any]] = []
    unknown_extras: list[dict[str, Any]] = []
    bad_datetime: list[dict[str, Any]] = []
    empty_runtime_arn: list[str] = []
    azure_mcp_anomaly: list[str] = []
    invalid_samples: list[dict[str, Any]] = []

    arn_counts: Counter[tuple[str, str]] = Counter()
    arn_first_document: dict[tuple[str, str], str] = {}
    duplicate_runtime_arns: list[dict[str, Any]] = []
    sampled_duplicate_keys: dict[tuple[str, str], dict[str, Any]] = {}

    async for document in cursor:
        total += 1
        doc_id = str(document.get("_id"))
        federation_ref_id = document.get("federationRefId")
        metadata = document.get("federationMetadata")
        has_federation_ref = federation_ref_id is not None
        has_metadata = metadata is not None

        if not has_federation_ref and not has_metadata:
            non_federated += 1
            continue

        federated += 1
        if has_federation_ref != has_metadata:
            _record_issue(
                issue_counts,
                "inconsistent_federation_state",
                inconsistent_federation_state,
                {
                    "_id": doc_id,
                    "federationRefId": str(federation_ref_id) if has_federation_ref else None,
                    "reason": ("missing federationMetadata" if has_federation_ref else "missing federationRefId"),
                },
                sample_limit,
            )
        if not has_metadata:
            continue

        # Provider type classification.
        provider_type = _provider_type_of(metadata)
        if provider_type is None:
            _record_issue(
                issue_counts,
                "missing_provider_type",
                missing_provider_type,
                doc_id,
                sample_limit,
            )
            continue
        if provider_type not in {
            FederationProviderType.AWS_AGENTCORE.value,
            FederationProviderType.AZURE_AI_FOUNDRY.value,
        }:
            _record_issue(
                issue_counts,
                "invalid_provider",
                invalid_provider,
                {"_id": doc_id, "providerType": provider_type},
                sample_limit,
            )
            continue

        by_provider[provider_type] += 1

        # Normalize to dict for structural checks.
        if isinstance(metadata, dict):
            metadata_dict = metadata
        else:
            try:
                metadata_dict = metadata.model_dump(mode="json")
            except Exception as exc:
                _record_issue(
                    issue_counts,
                    "invalid_samples",
                    invalid_samples,
                    {"_id": doc_id, "error": f"model_dump failed: {exc}"},
                    sample_limit,
                )
                continue

        # Core field presence.
        core_missing = _core_field_errors(metadata_dict)
        if core_missing:
            _record_issue(
                issue_counts,
                "missing_core_fields",
                missing_core_fields,
                {"_id": doc_id, "missing": core_missing},
                sample_limit,
            )

        # Unknown extra fields.
        extras = _find_unknown_extras(metadata_dict, provider_type)
        if extras:
            _record_issue(
                issue_counts,
                "unknown_extras",
                unknown_extras,
                {"_id": doc_id, "extras": extras},
                sample_limit,
            )

        # Datetime parseability.
        bad_dt_fields = _find_bad_datetime_fields(metadata_dict)
        if bad_dt_fields:
            _record_issue(
                issue_counts,
                "bad_datetime",
                bad_datetime,
                {"_id": doc_id, "fields": bad_dt_fields},
                sample_limit,
            )

        # Runtime ARN presence.
        runtime_arn = _runtime_arn_of(metadata_dict)
        if runtime_arn is None or runtime_arn == "":
            _record_issue(
                issue_counts,
                "empty_runtime_arn",
                empty_runtime_arn,
                doc_id,
                sample_limit,
            )
        elif has_federation_ref:
            arn_key = (str(federation_ref_id), str(runtime_arn))
            arn_counts[arn_key] += 1
            if arn_counts[arn_key] == 1:
                arn_first_document[arn_key] = doc_id
            elif arn_counts[arn_key] == 2:
                issue_counts["duplicate_runtime_arns"] += 1
                if len(duplicate_runtime_arns) < sample_limit:
                    sample = {
                        "federationRefId": arn_key[0],
                        "runtimeArn": arn_key[1],
                        "documentIds": [arn_first_document[arn_key], doc_id],
                    }
                    duplicate_runtime_arns.append(sample)
                    sampled_duplicate_keys[arn_key] = sample
            elif arn_key in sampled_duplicate_keys:
                sampled_ids = sampled_duplicate_keys[arn_key]["documentIds"]
                if len(sampled_ids) < sample_limit:
                    sampled_ids.append(doc_id)

        # Azure metadata in MCP collection.
        if _is_azure_in_mcp(provider_type, collection_name):
            _record_issue(
                issue_counts,
                "azure_mcp_anomaly",
                azure_mcp_anomaly,
                doc_id,
                sample_limit,
            )

        # Schema validation.
        try:
            metadata_adapter.validate_python(metadata)
        except ValidationError as exc:
            _record_issue(
                issue_counts,
                "invalid_samples",
                invalid_samples,
                {
                    "_id": doc_id,
                    "errors": exc.errors(include_url=False, include_input=False),
                },
                sample_limit,
            )

    return CollectionAudit(
        collection=collection_name,
        total=total,
        federated=federated,
        non_federated=non_federated,
        by_provider=dict(by_provider),
        issue_counts=dict(issue_counts),
        inconsistent_federation_state=inconsistent_federation_state,
        missing_provider_type=missing_provider_type,
        invalid_provider=invalid_provider,
        missing_core_fields=missing_core_fields,
        unknown_extras=unknown_extras,
        bad_datetime=bad_datetime,
        empty_runtime_arn=empty_runtime_arn,
        duplicate_runtime_arns=duplicate_runtime_arns,
        azure_mcp_anomaly=azure_mcp_anomaly,
        invalid_samples=invalid_samples,
    )


async def _run(sample_limit: int, db_name: str | None) -> int:
    await MongoDB.connect_db(config=MongoConfig(), db_name=db_name)
    try:
        results = [
            await _audit_collection(
                collection_name="mcpservers",
                metadata_adapter=TypeAdapter(AgentCoreMcpFederationMetadata),
                sample_limit=sample_limit,
            ),
            await _audit_collection(
                collection_name="a2a_agents",
                metadata_adapter=TypeAdapter(A2AFederationMetadata),
                sample_limit=sample_limit,
            ),
        ]
    finally:
        await MongoDB.close_db()

    output = {
        "results": [asdict(result) for result in results],
        "summary": {
            "has_blocking_issues": any(
                result.issue_counts.get(issue_name, 0) > 0 for result in results for issue_name in _BLOCKING_ISSUES
            ),
            "has_attention_items": any(
                result.issue_counts.get(issue_name, 0) > 0 for result in results for issue_name in _ATTENTION_ISSUES
            ),
        },
    }
    print(json.dumps(output, indent=2, default=str))
    return 1 if output["summary"]["has_blocking_issues"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-name", default=None, help="MongoDB database name; otherwise use the URI path")
    parser.add_argument("--sample-limit", type=int, default=20, help="Maximum reported examples per issue type")
    args = parser.parse_args()
    if args.sample_limit < 0:
        parser.error("--sample-limit must be non-negative")
    return asyncio.run(_run(args.sample_limit, args.db_name))


if __name__ == "__main__":
    raise SystemExit(main())
