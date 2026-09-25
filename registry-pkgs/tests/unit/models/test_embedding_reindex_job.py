import pytest
from beanie import PydanticObjectId
from pydantic import ValidationError

from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus


def test_target_embedding_model_source_id_is_required_and_stored() -> None:
    # The executor cannot run a job without knowing which model to re-embed against.
    with pytest.raises(ValidationError):
        EmbeddingReindexJob()
    target = PydanticObjectId()
    job = EmbeddingReindexJob.model_construct(targetEmbeddingModelSourceId=target)
    assert job.targetEmbeddingModelSourceId == target
    assert job.status == EmbeddingReindexJobStatus.RUNNING


def test_v2_fields_default_none_and_store() -> None:
    # requestedBy (audit), the previous (model, generation) pair, and switchedAt all default None.
    job = EmbeddingReindexJob.model_construct(targetEmbeddingModelSourceId=PydanticObjectId())
    assert job.requestedBy is None
    assert job.previousEmbeddingModelSourceId is None
    assert job.previousCollectionGeneration is None
    assert job.switchedAt is None
    job.requestedBy = "user-1"
    job.previousCollectionGeneration = "gen0"
    assert job.requestedBy == "user-1"
    assert job.previousCollectionGeneration == "gen0"


def test_status_enum_serializes_to_string_values() -> None:
    # The active-job query filters on the literal "running" value, so the enum must be a str subclass
    # that serializes to these strings.
    assert isinstance(EmbeddingReindexJobStatus.RUNNING, str)
    assert EmbeddingReindexJobStatus.RUNNING.value == "running"
    assert EmbeddingReindexJobStatus.COMPLETED.value == "completed"
    assert EmbeddingReindexJobStatus.FAILED.value == "failed"


def test_defaults_to_running_with_no_lease() -> None:
    job = EmbeddingReindexJob.model_construct()
    assert job.status == EmbeddingReindexJobStatus.RUNNING
    assert job.leaseOwner is None
    assert job.leaseExpiresAt is None
    assert job.heartbeatAt is None
    assert job.finishedAt is None
    assert job.error is None


def test_collection_name_and_index() -> None:
    assert EmbeddingReindexJob.Settings.name == "embedding_reindex_jobs"
    # Only the active-job query index; no single-flight unique index (CAS handles it).
    assert len(EmbeddingReindexJob.Settings.indexes) == 1
    assert not any(i.document.get("unique") for i in EmbeddingReindexJob.Settings.indexes)
