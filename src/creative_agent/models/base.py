"""Shared model base: unknown keys are always rejected (extra="forbid")."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SchemaModel(BaseModel):
    """Base for all harness schemas."""

    model_config = ConfigDict(extra="forbid")
