from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["property", "key", "source_id", "association", "lookup", "derived", "ignore"]
Decision = Literal["auto", "review", "manual", "unmapped", "ignored"]


class ColumnProfile(BaseModel):
    column: str
    index: int
    rows: int
    filled: int
    fill_rate: float
    distinct: int
    unique: bool
    inferred_type: str
    samples: list[str] = Field(default_factory=list)
    values: list[str] | None = None
    flags: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    property: str
    label: str | None = None
    type: str | None = None
    score: float
    evidence: list[str] = Field(default_factory=list)
    read_only: bool = False


class ColumnMapping(BaseModel):
    column: str
    role: Role = "property"
    property: str | None = None
    target_object: str | None = None
    match_by: str | None = None
    transform: str | None = None
    decision: Decision = "review"
    score: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    proposed_property: dict[str, Any] | None = None
    profile: ColumnProfile | None = None


class DerivedObject(BaseModel):
    """A secondary object implied by columns of the source (e.g. companies inside a deal export)."""

    object: str
    key_column: str
    name_column: str | None = None
    columns: dict[str, str] = Field(default_factory=dict)
    match_by: str = "name"
    association_type_id: int | None = None
    association_reverse_type_id: int | None = None
    association_label: str | None = None
    secondary_type_id: int | None = None
    secondary_reverse_type_id: int | None = None


class SourceSpec(BaseModel):
    name: str
    entity: str
    object: str
    file: str
    source_system: str = ""
    encoding: str = "utf-8-sig"
    delimiter: str = ","
    header_row: int = 0
    key_columns: list[str] = Field(default_factory=list)
    defaults: dict[str, str] = Field(default_factory=dict)
    mappings: list[ColumnMapping] = Field(default_factory=list)
    derived: list[DerivedObject] = Field(default_factory=list)
    status: Literal["planned", "live"] = "planned"
    generated_by: str = ""
    notes: list[str] = Field(default_factory=list)


class ProfileRequest(BaseModel):
    file: str
    header_row: int | None = None
    delimiter: str | None = None
    encoding: str = "utf-8-sig"
    sample_rows: int = 5000


class MatchRequest(BaseModel):
    entity: str
    object: str
    file: str
    name: str | None = None
    header_row: int | None = None
    delimiter: str | None = None
    encoding: str = "utf-8-sig"
    key_columns: list[str] | None = None
    source_system: str = ""
    sample_rows: int = 5000
    save: bool = False


class LoadRequest(BaseModel):
    spec: str | None = None
    spec_inline: SourceSpec | None = None
    run_id: str | None = None
    out_dir: str | None = None
    match_records: bool = True
    ingest_fake: bool = False
    limit: int | None = None
