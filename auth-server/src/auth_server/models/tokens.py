"""
Pydantic models for token validation and generation.
"""

from pydantic import BaseModel


class TokenValidationResponse(BaseModel):
    """Response model for token validation"""

    valid: bool
    scopes: list[str] = []
    error: str | None = None
    method: str | None = None
    client_id: str | None = None
    username: str | None = None
