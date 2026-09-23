import pytest
from beanie import PydanticObjectId
from pydantic import ValidationError

from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus


def test_target_embedding_model_source_id_is_required() -> None:
    # The executor cannot run a job without knowing which model to re-embed against.
    with pytest.raises(ValidationError):
        EmbeddingReindexJob()


def test_target_embedding_model_source_id_is_stored() -> None:
    target = PydanticObjectId()
    job = EmbeddingReindexJob.model_construct(targetEmbeddingModelSourceId=target)
    assert job.targetEmbeddingModelSourceId == target
    assert job.status == EmbeddingReindexJobStatus.RUNNING


def test_status_enum_values() -> None:
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
    # One compound index on (status, leaseExpiresAt) drives the active-job query.
    assert len(EmbeddingReindexJob.Settings.indexes) == 1


def test_status_is_str_enum_for_query_filtering() -> None:
    # The active-job query filters on the literal "running" value, so the enum
    # must serialize to that string.
    assert isinstance(EmbeddingReindexJobStatus.RUNNING, str)
    assert EmbeddingReindexJobStatus.RUNNING == "running"
