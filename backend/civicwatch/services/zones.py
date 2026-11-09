"""Zone persistence and rule validation."""

from __future__ import annotations

import json
import uuid
from typing import Any, Sequence

from ..db import audit, execute, query, query_one, utc_now
from ..detect.base import PERSON_CLASSES, VEHICLE_CLASSES
from ..geometry import validate_polygon
from ..pipeline.rules import KIND_DEFAULTS, ZONE_KINDS, ZoneSpec

ALLOWED_CLASSES = set(VEHICLE_CLASSES) | set(PERSON_CLASSES) | {"bicycle"}
SCOPES = ("all", "media", "camera")


class ZoneService:
    @staticmethod
    def list(
        scope_type: str | None = None, scope_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if scope_type:
            if scope_type not in SCOPES:
                raise ValueError("scope_type must be all, media or camera")
            where.append("(scope_type = 'all' OR scope_type = ?)")
            params.append(scope_type)
        if scope_id:
            where.append("(scope_id IS NULL OR scope_id = ?)")
            params.append(scope_id)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = query(f"SELECT * FROM zones{clause} ORDER BY created_at ASC", params)
        return [ZoneService._to_dict(row) for row in rows]

    @staticmethod
    def _to_dict(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["polygon"] = json.loads(item["polygon"])
        item["rules"] = json.loads(item["rules"] or "{}")
        item["enabled"] = bool(item["enabled"])
        item["kind_label"] = KIND_DEFAULTS.get(item["kind"], {}).get("label", item["kind"])
        return item

    @staticmethod
    def get(zone_id: str) -> dict[str, Any] | None:
        row = query_one("SELECT * FROM zones WHERE id = ?", (zone_id,))
        return ZoneService._to_dict(row) if row else None

    @staticmethod
    def create(payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("Zone name is required")
        kind = str(payload.get("kind", "")).strip()
        if kind not in ZONE_KINDS:
            raise ValueError(f"Zone type must be one of {', '.join(ZONE_KINDS)}")
        polygon = validate_polygon(payload.get("polygon", []))
        scope_type = str(payload.get("scope_type", "all"))
        if scope_type not in SCOPES:
            raise ValueError("scope_type must be all, media or camera")
        scope_id = payload.get("scope_id")
        if scope_type != "all" and not scope_id:
            raise ValueError("A media or camera zone needs a scope_id")
        if scope_type == "all":
            scope_id = None
        rules = ZoneService._validate_rules(kind, payload.get("rules") or {})
        zone_id = f"zone-{uuid.uuid4().hex[:10]}"
        now = utc_now()
        execute(
            """INSERT INTO zones
               (id, name, kind, scope_type, scope_id, polygon, rules, enabled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                zone_id,
                name,
                kind,
                scope_type,
                scope_id,
                json.dumps(polygon),
                json.dumps(rules),
                1 if payload.get("enabled", True) else 0,
                now,
                now,
            ),
        )
        audit("zone.created", {"id": zone_id, "name": name, "kind": kind})
        return ZoneService.get(zone_id) or {}

    @staticmethod
    def update(zone_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = ZoneService.get(zone_id)
        if not existing:
            raise KeyError("Zone not found")
        updates: dict[str, Any] = {}
        if "name" in payload:
            name = str(payload["name"]).strip()
            if not name:
                raise ValueError("Zone name cannot be empty")
            updates["name"] = name
        if "kind" in payload:
            if payload["kind"] not in ZONE_KINDS:
                raise ValueError("Invalid zone type")
            updates["kind"] = payload["kind"]
        if "polygon" in payload:
            updates["polygon"] = json.dumps(validate_polygon(payload["polygon"]))
        if "rules" in payload:
            kind = updates.get("kind", existing["kind"])
            updates["rules"] = json.dumps(ZoneService._validate_rules(kind, payload["rules"] or {}))
        if "enabled" in payload:
            updates["enabled"] = 1 if payload["enabled"] else 0
        if "scope_type" in payload or "scope_id" in payload:
            scope_type = payload.get("scope_type", existing["scope_type"])
            if scope_type not in SCOPES:
                raise ValueError("scope_type must be all, media or camera")
            scope_id = payload.get("scope_id", existing["scope_id"])
            if scope_type == "all":
                scope_id = None
            elif not scope_id:
                raise ValueError("A media or camera zone needs a scope_id")
            updates["scope_type"] = scope_type
            updates["scope_id"] = scope_id
        if not updates:
            return existing
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        execute(
            f"UPDATE zones SET {assignments} WHERE id = ?",
            [*updates.values(), zone_id],
        )
        audit("zone.updated", {"id": zone_id})
        return ZoneService.get(zone_id) or {}

    @staticmethod
    def delete(zone_id: str) -> None:
        if not query_one("SELECT id FROM zones WHERE id = ?", (zone_id,)):
            raise KeyError("Zone not found")
        execute("DELETE FROM zones WHERE id = ?", (zone_id,))
        audit("zone.deleted", {"id": zone_id})

    @staticmethod
    def for_source(
        scope_type: str, scope_id: str | None, enabled_only: bool = True
    ) -> list[ZoneSpec]:
        """Zones that apply to one video or camera, including global zones."""
        params: list[Any] = [scope_type]
        clause = "(scope_type = 'all' OR (scope_type = ? AND scope_id = ?))"
        params.append(scope_id)
        sql = f"SELECT * FROM zones WHERE {clause}"
        if enabled_only:
            sql += " AND enabled = 1"
        specs: list[ZoneSpec] = []
        for row in query(sql + " ORDER BY created_at ASC", params):
            try:
                specs.append(ZoneSpec.from_row(dict(row)))
            except (ValueError, KeyError) as exc:
                import logging

                logging.getLogger("civicwatch.zones").warning(
                    "Skipping zone %s: %s", row["id"], exc
                )
        return specs

    @staticmethod
    def kinds() -> list[dict[str, Any]]:
        labels = {
            "no_parking": "No parking",
            "footpath": "Footpath",
            "road": "Road boundary",
            "restricted": "Restricted area",
        }
        descriptions = {
            "no_parking": "Flags vehicles that stay stationary beyond the dwell limit.",
            "footpath": "Flags vehicles left on a pedestrian area. Pedestrians are never penalised.",
            "road": "Marks the carriageway. Only produces incidents when 'no stopping' is enabled.",
            "restricted": "Flags any listed class that enters and stays beyond the dwell limit.",
        }
        return [
            {
                "value": kind,
                "label": labels.get(kind, kind),
                "description": descriptions.get(kind, ""),
                "defaults": KIND_DEFAULTS[kind],
            }
            for kind in ZONE_KINDS
        ]

    # -- validation ------------------------------------------------------
    @staticmethod
    def _validate_rules(kind: str, rules: dict[str, Any]) -> dict[str, Any]:
        defaults = KIND_DEFAULTS[kind]
        clean: dict[str, Any] = {
            "dwell_seconds": float(rules.get("dwell_seconds", defaults["dwell_seconds"])),
            "classes": sorted(
                {c for c in (rules.get("classes") or defaults["classes"]) if c in ALLOWED_CLASSES}
            ),
            "severity": rules.get("severity", defaults.get("severity", "medium")),
            "double_parking": bool(rules.get("double_parking", defaults.get("double_parking", False))),
            "no_stopping": bool(rules.get("no_stopping", defaults.get("no_stopping", False))),
        }
        if not 1.0 <= clean["dwell_seconds"] <= 600.0:
            raise ValueError("Dwell time must be between 1 and 600 seconds")
        if clean["severity"] not in {"low", "medium", "high"}:
            raise ValueError("Severity must be low, medium or high")
        if kind in {"no_parking", "footpath", "restricted"} and not clean["classes"]:
            raise ValueError("Select at least one vehicle or pedestrian class for this zone")
        if "overlap_threshold" in rules:
            value = float(rules["overlap_threshold"])
            if not 0.0 <= value <= 1.0:
                raise ValueError("Overlap threshold must be between 0 and 1")
            clean["overlap_threshold"] = value
        if "neighbour_ratio" in rules:
            clean["neighbour_ratio"] = max(0.01, min(0.5, float(rules["neighbour_ratio"])))
        return clean


def zones_for_polygon(polygons: Sequence[Sequence[Sequence[float]]]) -> list[ZoneSpec]:
    return [ZoneSpec(id=f"temp-{i}", name="", kind="restricted", polygon=validate_polygon(p))
            for i, p in enumerate(polygons)]
