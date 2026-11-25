"""Move an explicitly configured legacy workflow model into the Model Gateway."""

import logging
import os
from datetime import UTC, datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

SELECTION_ID = ObjectId("000000000000000000000001")
MIGRATED_MODEL_SOURCE_ID = ObjectId("000000000000000000002028")


async def up(db: AsyncDatabase) -> None:
    model_id = (
        os.environ.get("AWS_BEDROCK_SONNET_AIP_ARN", "").strip() or os.environ.get("AWS_WORKFLOW_LLM_MODEL", "").strip()
    )
    if not model_id:
        logger.info("Skipped workflow model seed: no explicit legacy model configured")
        return

    selections = db["model_gateway_selection"]
    selection = await selections.find_one({"_id": SELECTION_ID})
    # Preserve any existing value, including malformed ones, for an administrator to repair.
    if selection is not None and selection.get("defaultWorkflowModelSourceId") is not None:
        logger.info("Skipped workflow model seed: a default selection already exists")
        return

    now = datetime.now(UTC)
    provider_config = {
        "providerType": "aws_bedrock",
        "awsRegion": os.environ.get("AWS_REGION", "").strip() or "us-east-1",
        "modelIdOrArn": model_id,
        # ARN placeholders also prevent SkillSpector from identifying Claude; operators must
        # supply the canonical base model ID after migration. Never call Bedrock here.
        "baseModelId": model_id,
    }
    sources = db["model_sources"]
    stored_base_model_id = model_id
    try:
        await sources.insert_one(
            {
                "_id": MIGRATED_MODEL_SOURCE_ID,
                "displayName": "Workflow default (migrated)",
                "tags": [],
                "mode": "chat",
                "providerConfig": provider_config,
                "createdAt": now,
                "updatedAt": now,
            }
        )
        logger.info("Seeded workflow model: inserted=1 reused=0")
    except DuplicateKeyError as exc:
        existing = await sources.find_one({"_id": MIGRATED_MODEL_SOURCE_ID})
        config = existing.get("providerConfig") if existing else None
        if (
            existing is None
            or existing.get("deletedAt") is not None
            or existing.get("mode") != "chat"
            or not isinstance(config, dict)
            or any(config.get(key) != value for key, value in provider_config.items() if key != "baseModelId")
            or not isinstance(config.get("baseModelId"), str)
            or not config["baseModelId"].strip()
        ):
            raise ValueError(
                "Cannot reuse migrated workflow model: fixed id is missing, unavailable or changed"
            ) from exc
        logger.info("Seeded workflow model: inserted=0 reused=1")
        stored_base_model_id = config["baseModelId"]

    if stored_base_model_id.startswith("arn:"):
        logger.warning(
            "Migrated workflow model needs its canonical baseModelId configured on the Models page. "
            "Until corrected, metadata may be unavailable and SkillSpector may scan without an LLM "
            "when it inherits this model. Set the real foundation model ID, or select a correctly "
            "configured dedicated scan model, and verify an LLM-enabled scan before rollout completes."
        )

    try:
        result = await selections.update_one(
            {"_id": SELECTION_ID, "defaultWorkflowModelSourceId": None},
            {"$set": {"defaultWorkflowModelSourceId": MIGRATED_MODEL_SOURCE_ID, "updatedAt": now}},
            upsert=True,
        )
    except DuplicateKeyError:
        selection = await selections.find_one({"_id": SELECTION_ID})
        if selection is None or selection.get("defaultWorkflowModelSourceId") is None:
            raise
        logger.info("Workflow model selection preserved: concurrent administrator selection wins")
        return
    logger.info(
        "Selected migrated workflow model: matched=%d modified=%d upserted_id=%s",
        result.matched_count,
        result.modified_count,
        result.upserted_id,
    )
