"""The `registry_migrations` tracking collection: one document per applied migration."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.asynchronous.database import AsyncDatabase

MIGRATIONS_COLLECTION = "registry_migrations"


class MigrationRecord(BaseModel):
    """Proof that a migration ran to completion. Applied state is the set of records; there is no version pointer."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")  # version, e.g. "0001"
    name: str
    checksum: str
    applied_at: datetime  # UTC
    duration_ms: int
    build_version: str

    @field_validator("applied_at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        # pymongo returns naive datetimes (holding UTC) unless the client is created with tz_aware=True.
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def read_records(db: AsyncDatabase) -> list[MigrationRecord]:
    """Return every record, sorted by version."""
    documents = await db[MIGRATIONS_COLLECTION].find({}).sort("_id", 1).to_list()
    return [MigrationRecord.model_validate(document) for document in documents]


async def insert_record(db: AsyncDatabase, record: MigrationRecord) -> None:
    """Insert the record. Raises `DuplicateKeyError` if another runner already recorded this version."""
    await db[MIGRATIONS_COLLECTION].insert_one(record.model_dump(by_alias=True))
