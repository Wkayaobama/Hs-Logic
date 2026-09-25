"""Entity registry.

Which business entity (SEALSQ, ICALPS, MIRAEX, WECAN, WISEKEY, ...) a HubSpot
record belongs to is stated once, in ``app/data/entities.yaml``, and turned
here into either HubSpot search ``filterGroups`` (server-side scoping) or a
predicate evaluated on returned properties (client-side, for the complement
entity). Loading this file is the "entities loaded" precondition of the probe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from app.config import settings

DEFAULT_PATH = Path(__file__).with_name("data") / "entities.yaml"
ENGAGEMENTS = {"notes", "calls", "meetings", "tasks", "emails", "communications"}
PIPELINE_PROPERTY = {"deals": "pipeline", "tickets": "hs_pipeline"}

Operator = Literal[
    "EQ", "NEQ", "HAS_PROPERTY", "NOT_HAS_PROPERTY", "IN", "NOT_IN", "CONTAINS_TOKEN"
]
ScopeMode = Literal["search", "complement", "no_marker", "unscoped", "all"]


def _tokens(raw) -> list[str]:
    """A multi-checkbox value is ';'-separated in HubSpot."""
    if raw is None:
        return []
    text = str(raw)
    if text == "":
        return []
    return [t for t in text.split(";") if t != ""]


class Marker(BaseModel):
    property: str
    operator: Operator = "HAS_PROPERTY"
    value: str | None = None
    values: list[str] = Field(default_factory=list)

    def to_filter(self) -> dict:
        f: dict = {"propertyName": self.property, "operator": self.operator}
        if self.operator in ("IN", "NOT_IN"):
            f["values"] = list(self.values) or ([self.value] if self.value is not None else [])
        elif self.operator not in ("HAS_PROPERTY", "NOT_HAS_PROPERTY"):
            f["value"] = self.value
        return f

    def matches(self, props: dict) -> bool:
        raw = props.get(self.property)
        present = raw is not None and str(raw) != ""
        if self.operator == "HAS_PROPERTY":
            return present
        if self.operator == "NOT_HAS_PROPERTY":
            return not present
        tokens = _tokens(raw)
        if self.operator == "EQ":
            return present and (str(raw) == self.value or self.value in tokens)
        if self.operator == "NEQ":
            return not (present and (str(raw) == self.value or self.value in tokens))
        if self.operator == "IN":
            wanted = set(self.values) | ({self.value} if self.value is not None else set())
            return present and (str(raw) in wanted or any(t in wanted for t in tokens))
        if self.operator == "NOT_IN":
            wanted = set(self.values) | ({self.value} if self.value is not None else set())
            return not (present and (str(raw) in wanted or any(t in wanted for t in tokens)))
        if self.operator == "CONTAINS_TOKEN":
            return present and (self.value or "").lower() in str(raw).lower()
        return False


class ObjectScope(BaseModel):
    pipelines: list[str] = Field(default_factory=list)
    any_of: list[Marker] = Field(default_factory=list)
    complement: bool = False

    def filter_groups(self, object_type: str) -> list[dict] | None:
        groups: list[dict] = []
        if self.pipelines:
            groups.append(
                {
                    "filters": [
                        {
                            "propertyName": PIPELINE_PROPERTY.get(object_type, "pipeline"),
                            "operator": "IN",
                            "values": list(self.pipelines),
                        }
                    ]
                }
            )
        for marker in self.any_of:
            groups.append({"filters": [marker.to_filter()]})
        return groups or None

    def marker_properties(self, object_type: str) -> set[str]:
        names = {m.property for m in self.any_of}
        if self.pipelines:
            names.add(PIPELINE_PROPERTY.get(object_type, "pipeline"))
        return names

    def matches(self, object_type: str, props: dict) -> bool:
        if self.pipelines:
            raw = props.get(PIPELINE_PROPERTY.get(object_type, "pipeline"))
            if raw is not None and str(raw) in self.pipelines:
                return True
        return any(m.matches(props) for m in self.any_of)


class EntitySpec(BaseModel):
    id: str
    name: str
    kind: str = "entity"
    parent: str | None = None
    brand_id: int = 0
    teams: list[str] = Field(default_factory=list)
    notes: str = ""
    objects: dict[str, ObjectScope] = Field(default_factory=dict)


class EntitiesFile(BaseModel):
    version: int = 1
    captured: str = ""
    portal_id: str = ""
    entities: list[EntitySpec]


class EntityRegistry:
    def __init__(self, spec: EntitiesFile) -> None:
        self.spec = spec
        self._by_id = {e.id: e for e in spec.entities}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "EntityRegistry":
        p = Path(path) if path else (Path(settings.entities_path) if settings.entities_path else DEFAULT_PATH)
        with open(p, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls(EntitiesFile.model_validate(raw))

    @property
    def ids(self) -> list[str]:
        return [e.id for e in self.spec.entities]

    def get(self, entity_id: str) -> EntitySpec:
        try:
            return self._by_id[entity_id]
        except KeyError as exc:
            raise KeyError(f"Unknown entity '{entity_id}'. Known: {', '.join(self.ids)}") from exc

    def scope(self, entity_id: str | None, object_type: str) -> tuple[ScopeMode, list[dict] | None]:
        """How to fetch ``object_type`` records for an entity."""
        if entity_id is None:
            return "all", None
        entity = self.get(entity_id)
        if object_type in ENGAGEMENTS:
            return "unscoped", None
        obj = entity.objects.get(object_type)
        if obj is None:
            return "no_marker", None
        if obj.complement:
            return "complement", None
        groups = obj.filter_groups(object_type)
        return ("search", groups) if groups else ("no_marker", None)

    def marker_properties(self, object_type: str) -> list[str]:
        names: set[str] = set()
        for entity in self.spec.entities:
            obj = entity.objects.get(object_type)
            if obj is not None:
                names |= obj.marker_properties(object_type)
        return sorted(names)

    def matches(self, entity_id: str, object_type: str, props: dict) -> bool:
        entity = self.get(entity_id)
        obj = entity.objects.get(object_type)
        if obj is None:
            return False
        if obj.complement:
            for other in self.spec.entities:
                if other.id == entity_id:
                    continue
                other_obj = other.objects.get(object_type)
                if other_obj is None or other_obj.complement:
                    continue
                if other_obj.matches(object_type, props):
                    return False
            return True
        return obj.matches(object_type, props)

    def classify(self, object_type: str, props: dict) -> list[str]:
        """All entities whose markers match; complement entities only when nothing else does."""
        direct = [
            e.id
            for e in self.spec.entities
            if (obj := e.objects.get(object_type)) is not None
            and not obj.complement
            and obj.matches(object_type, props)
        ]
        if direct:
            return direct
        return [
            e.id
            for e in self.spec.entities
            if (obj := e.objects.get(object_type)) is not None and obj.complement
        ]

    def describe(self) -> dict:
        out = []
        for e in self.spec.entities:
            objects = {}
            for object_type, obj in e.objects.items():
                mode, groups = self.scope(e.id, object_type)
                objects[object_type] = {
                    "mode": mode,
                    "pipelines": obj.pipelines,
                    "markers": [m.to_filter() for m in obj.any_of],
                    "filter_groups": groups,
                }
            out.append(
                {
                    "id": e.id,
                    "name": e.name,
                    "kind": e.kind,
                    "parent": e.parent,
                    "brand_id": e.brand_id,
                    "teams": e.teams,
                    "notes": e.notes,
                    "objects": objects,
                }
            )
        return {
            "version": self.spec.version,
            "captured": self.spec.captured,
            "portal_id": self.spec.portal_id,
            "entities": out,
        }


_registry: EntityRegistry | None = None


def get_registry() -> EntityRegistry:
    global _registry
    if _registry is None:
        _registry = EntityRegistry.load()
    return _registry


def set_registry(registry: EntityRegistry | None) -> None:
    global _registry
    _registry = registry
