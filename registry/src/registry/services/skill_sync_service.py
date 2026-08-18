import logging

from registry_pkgs.models.enums import SkillSyncJobType

from .skill_sync_job_service import SkillSyncJobService
from .skill_sync_source_crud_service import SkillSyncSourceCrudService

logger = logging.getLogger(__name__)


async def run_skill_sync_background(
    *,
    source_id: str,
    job_id: str,
    source_service: SkillSyncSourceCrudService,
    job_service: SkillSyncJobService,
) -> None:
    source = await source_service.get_source(source_id)
    if source is None:
        logger.error("Skill sync source %s disappeared before placeholder job execution", source_id)
        return
    job = await job_service.get_job(job_id, source_id=source.id)
    if job is None:
        logger.error("Skill sync job %s disappeared before placeholder execution", job_id)
        return
    await job_service.mark_not_implemented(job)
    message = "Skill sync execution is not implemented yet"
    if job.jobType == SkillSyncJobType.DELETE_SYNC:
        await source_service.restore_after_delete_failure(source, message)
        return
    await source_service.mark_sync_failed(source, message)
