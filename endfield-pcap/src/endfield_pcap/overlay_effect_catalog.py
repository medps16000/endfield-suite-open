"""Compact, exact-ID display catalog; no gameplay or attribution rules."""
from __future__ import annotations

import json
from pathlib import Path

from .runtime_paths import bundle_root


def classify_official_icon_group(has_icon, group, uses_numeric_priority) -> str:
    """Translate exact configured UI buckets, never infer gameplay effects."""
    if has_icon is not True or uses_numeric_priority is not False or not isinstance(group, str):
        return "unclassified"
    return {
        "CommonCharBuff": "buff", "AttentionBuff": "buff", "KeywordBuff": "buff",
        "CommonCharDebuff": "debuff", "AttentionDebuff": "debuff", "KeywordDebuff": "debuff",
    }.get(group, "unclassified")


def load_effect_catalog(root: Path | None = None) -> list[dict]:
    path = (root or bundle_root()) / "data" / "overlay" / "effects.json"
    try:
        if path.stat().st_size > 4_000_000:
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1:
            return []
        entries = payload.get("entries")
        if not isinstance(entries, list) or len(entries) > 20_000:
            return []
        descriptions = {}
        notes_path = path.with_name("descriptions.json")
        source_hash = payload.get("source", {}).get("typedRootsSha256")
        try:
            if notes_path.stat().st_size <= 4_000_000:
                notes = json.loads(notes_path.read_text(encoding="utf-8"))
                if (notes.get("schemaVersion") == 1 and isinstance(notes.get("entries"), dict)
                        and isinstance(source_hash, str) and len(source_hash) == 64
                        and all(char in "0123456789abcdef" for char in source_hash)
                        and notes.get("source", {}).get("typedRootsSha256") == source_hash):
                    descriptions = notes["entries"]
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            pass
        clean = []
        seen = set()
        for row in entries:
            if not isinstance(row, dict):
                return []
            identity = row.get("canonicalId")
            numeric = row.get("numericIds")
            if (not isinstance(identity, str) or not identity or len(identity) > 256 or identity in seen
                    or not isinstance(numeric, list) or len(numeric) > 32
                    or any(not isinstance(value, str) or not value.isascii() or not value.isdecimal() for value in numeric)):
                return []
            # These are configured official UI buckets, not a damage/rDPS rule
            # or a conclusion from an internal ID, image or modifier sign.
            icon = row.get("icon")
            if not isinstance(icon, str) or not icon.startswith("data:image/webp;base64,") or len(icon) > 100_000:
                icon = None
            group = row.get("uiGroup")
            group = group if isinstance(group, str) else None
            numeric_priority = row.get("uiGroupUsesNumericPriority")
            numeric_priority = numeric_priority if type(numeric_priority) is bool else None
            classification = classify_official_icon_group(row.get("hasIcon"), group, numeric_priority)
            note = descriptions.get(identity)
            note = note if isinstance(note, dict) else {}
            label = note.get("label")
            label = label if isinstance(label, str) and 0 < len(label) <= 80 and note.get("labelSource") == "official-config-summary" else None
            description = note.get("description")
            description = description if isinstance(description, str) and 0 < len(description) <= 800 and note.get("descriptionSource") == "official-config-summary" else None
            clean.append({"effectId": identity, "canonicalId": identity, "numericIds": numeric,
                          "label": label, "labelSource": "official-config-summary" if label else None,
                          "description": description, "descriptionSource": "official-config-summary" if description else None,
                          "classification": classification, "icon": icon,
                          "iconPresentation": (row.get("iconPresentation")
                                               if row.get("iconPresentation") in {"image", "missing-asset", "skill-button-progress"}
                                               else None),
                          "hasIcon": row.get("hasIcon") is True, "uiGroup": group,
                          "uiGroupUsesNumericPriority": numeric_priority,
                          "classificationSource": "official-icon-priority-group" if classification != "unclassified" else None})
            seen.add(identity)
        return clean
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return []


def load_target_catalog(root: Path | None = None) -> dict[str, dict]:
    path = (root or bundle_root()) / "data/overlay/targets.json"
    try:
        if path.stat().st_size > 1_000_000:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1:
            return {}
        entries = payload.get("entries")
        if not isinstance(entries, dict) or len(entries) > 20_000:
            return {}
        return {identity: {"label": row["label"]} for identity, row in entries.items()
                if isinstance(identity, str) and 0 < len(identity) <= 256 and isinstance(row, dict)
                and isinstance(row.get("label"), str) and 0 < len(row["label"]) <= 256}
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return {}


def load_effect_definitions(root: Path | None = None) -> list[dict]:
    """Load a separate static encyclopedia, never runtime source attribution.

    The broker sends bounded pages; this must not be added to the catalog's
    initial full broadcast. All values under staticConfig/details are official
    configuration values, not measured stacks, duration or an observed caster.
    """
    root = root or bundle_root()
    path = root / "data/overlay/definitions.json"
    try:
        if path.stat().st_size > 64_000_000:
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        source = payload.get("source", {})
        digest = source.get("typedRootsSha256")
        if (type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1
                or payload.get("coverage") != "official-static-definition-graph"
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            return []
        catalog_path = path.with_name("effects.json")
        if catalog_path.stat().st_size > 4_000_000:
            return []
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        if catalog.get("source", {}).get("typedRootsSha256") != digest:
            return []
        fingerprint = source.get("resourceFingerprint")
        if (not isinstance(fingerprint, dict) or not isinstance(fingerprint.get("source"), dict)
                or not isinstance(fingerprint.get("extracted"), dict)
                or not fingerprint["source"] or not fingerprint["extracted"]):
            return []
        receipt_path = root / "data/game_resource_snapshot.json"
        if receipt_path.stat().st_size > 1_000_000:
            return []
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if any(receipt.get(key) != fingerprint[key] for key in ("source", "extracted")):
            return []
        entries = payload.get("entries")
        if not isinstance(entries, list) or len(entries) > 20_000:
            return []
        clean, seen = [], set()
        source_fields = {"sourceType", "sourceId", "sourceName", "skillId", "skillName", "skillDescription", "field",
                         "relationKind", "skillGroupId", "talentEffectId", "talentNodeId", "requiredEquipCount", "skillLevel", "setId",
                         "sourceScope", "enemyTemplateId"}
        for row in entries:
            if not isinstance(row, dict):
                return []
            identity, numeric, relations = row.get("canonicalId"), row.get("numericIds"), row.get("sourceRelations")
            if (not isinstance(identity, str) or not 0 < len(identity) <= 256 or identity in seen
                    or row.get("effectId") != identity or row.get("definitionOnly") is not True
                    or not isinstance(numeric, list) or len(numeric) > 32
                    or any(not isinstance(item, str) or not item.isascii() or not item.isdecimal() for item in numeric)
                    or not isinstance(relations, list) or len(relations) > 2048
                    or not isinstance(row.get("staticConfig"), dict) or not isinstance(row.get("details"), dict)):
                return []
            safe_relations = []
            for relation in relations:
                if (not isinstance(relation, dict) or relation.get("sourceType") not in {"weapon", "character", "equipment", "equipment-set", "enemy"}
                        or not isinstance(relation.get("sourceId"), str) or not 0 < len(relation["sourceId"]) <= 256
                        or relation.get("relationKind") != "official-static-definition"
                        or any(not isinstance(relation.get(key), str) or len(relation[key]) > limit
                               for key, limit in (("sourceName", 256), ("skillId", 256), ("skillName", 512),
                                                  ("skillDescription", 8000), ("field", 1024)))):
                    return []
                if relation["sourceType"] == "enemy" and (
                        relation.get("sourceScope") != "born-buff-configuration"
                        or relation.get("field") != "EnemyTable.bornBuffs"
                        or not isinstance(relation.get("enemyTemplateId"), str)
                        or not 0 < len(relation["enemyTemplateId"]) <= 256):
                    return []
                safe_relations.append({key: value for key, value in relation.items() if key in source_fields})
            label = row.get("label")
            label_source = row.get("labelSource")
            if (not isinstance(label, str) or not 0 < len(label) <= 512 or label_source not in
                    {"official-config-summary", "official-source-config-summary"}):
                label, label_source = None, None
            description = row.get("description")
            if not isinstance(description, str) or not 0 < len(description) <= 800:
                description = None
            icon_key = row.get("iconKey")
            if not isinstance(icon_key, str) or len(icon_key) > 256 or any(char in icon_key for char in ("/", "\\", ":")):
                icon_key = None
            # Expose only exact resource bindings used by this definition.
            # User-authored rules can reject changed official graphs without
            # receiving the entire resource inventory or local file paths.
            details = {**row["details"]}
            resource_keys = {"BuffData/" + identity}
            for source_path in details.get("sourcePaths", []):
                if not isinstance(source_path, dict):
                    continue
                for step in source_path.get("steps", []):
                    if not isinstance(step, dict):
                        continue
                    for prefix in ("from", "to"):
                        kind, resource_id = step.get(prefix + "Kind"), step.get(prefix + "Id")
                        if (kind in {"BuffData", "SkillData", "ProjectileData", "AbilityEntityData"}
                                and isinstance(resource_id, str) and 0 < len(resource_id) <= 256):
                            resource_keys.add(kind + "/" + resource_id)
            resource_hashes = source.get("resourcesSha256", {})
            details["sourceResourcesSha256"] = {
                key: resource_hashes[key] for key in sorted(resource_keys)
                if isinstance(resource_hashes, dict) and isinstance(resource_hashes.get(key), str)
                and len(resource_hashes[key]) == 64
                and all(char in "0123456789abcdef" for char in resource_hashes[key])}
            details["definitionGraphBinding"] = {
                name: (value if isinstance(value := source.get(name), str) and len(value) == 64
                       and all(char in "0123456789abcdef" for char in value) else None)
                for name in ("typedRootsSha256", "skillManifestSha256")}
            clean.append({"effectId": identity, "canonicalId": identity, "numericIds": numeric,
                          "label": label, "labelSource": label_source, "description": description,
                          "descriptionSource": "official-config-summary" if description else None,
                          "sourceRelations": safe_relations, "staticConfig": row["staticConfig"],
                          "details": details, "definitionOnly": True, "iconKey": icon_key,
                          "iconPresentation": row.get("iconPresentation") if row.get("iconPresentation") in
                          {"image", "missing-asset", "skill-button-progress"} else None,
                          "hasIcon": row.get("hasIcon") is True})
            seen.add(identity)
        return clean
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return []
