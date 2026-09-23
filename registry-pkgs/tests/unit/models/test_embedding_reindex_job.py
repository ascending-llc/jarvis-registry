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


def test_triggered_by_defaults_none_and_is_stored() -> None:
    # The executor commits the gateway selection after the swap using this audit value.
    job = EmbeddingReindexJob.model_construct(targetEmbeddingModelSourceId=PydanticObjectId())
    assert job.triggeredBy is None
    job.triggeredBy = "user-1"
    assert job.triggeredBy == "user-1"


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
    # A compound index drives the active-job query, plus a single-flight unique index.
    assert len(EmbeddingReindexJob.Settings.indexes) == 2


def test_running_single_flight_unique_index() -> None:
    # At most one RUNNING job may exist, enforced by a partial unique index on status == "running".
    unique = [i for i in EmbeddingReindexJob.Settings.indexes if i.document.get("unique")]
    assert len(unique) == 1
    assert unique[0].document["partialFilterExpression"] == {"status": "running"}


def test_status_is_str_enum_for_query_filtering() -> None:
    # The active-job query filters on the literal "running" value, so the enum
    # must serialize to that string.
    assert isinstance(EmbeddingReindexJobStatus.RUNNING, str)
    assert EmbeddingReindexJobStatus.RUNNING == "running"
