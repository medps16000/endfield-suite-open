"""Read-only Buff definitions linked only through official, typed foreign keys."""
from __future__ import annotations

from collections import defaultdict, deque
import gzip
import hashlib
import json
from pathlib import Path
import sys


def _plain(value):
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items() if not key.startswith("$")}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _text(value, texts):
    if isinstance(value, dict):
        result = texts.get(str(value.get("id"))) or value.get("text")
        return result if isinstance(result, str) else ""
    return value if isinstance(value, str) else ""


def _blackboard(root):
    return {row["key"]: {"value": row.get("valueDouble"), "valueStr": row.get("valueStr", ""),
                         "isDynamic": row.get("isDynamic") is True}
            for row in root.get("blackboard", []) if isinstance(row, dict) and row.get("key")}


def _edges(root, kind, identity):
    """An ID-like string is never sufficient; the containing typed field is required."""
    edges, unresolved = [], []
    pending = [("$", root, None, False)]
    relations = {
        "Beyond.Gameplay.Core.CreateBuffActionInput": {"buffId": ("BuffData", None)},
        "Beyond.Gameplay.Core.BuffInput": {"buffId": ("BuffData", None)},
        "Beyond.Gameplay.Core.LaunchProjectile.Data": {
            "projectileSkillId": ("SkillData", "castSkillOnHit"),
            "skillIdOnReach": ("SkillData", "castSkillOnReach"),
            "skillIdOnBlock": ("SkillData", "castSkillOnBlock"),
            "skillIdOnFinish": ("SkillData", "castSkillOnFinish"),
        },
        "Beyond.Gameplay.Core.SpawnAbilityEntity.Data": {"abilityEntitySkillId": ("SkillData", None)},
        "Beyond.Gameplay.Core.CastSkill.Data": {"skillId": ("SkillData", None)},
    }
    while pending:
        path, node, action, child = pending.pop()
        if isinstance(node, dict):
            if node.get("isEnable") is False:
                continue
            action = node.get("serverActionIndex", action)
            child = node.get("asChildBuff", child)
            for field, (target_kind, gate) in relations.get(node.get("$type"), {}).items():
                if gate and node.get(gate) is not True:
                    continue
                target = node.get(field)
                dynamic = node.get("readIdFromBlackboard") is True
                key = node.get("buffIdKey")
                if isinstance(target, dict):
                    dynamic = target.get("useBlackboardKey") is True
                    key = target.get("blackboardKey")
                    target = target.get("value")
                proof = {"fromKind": kind, "fromId": identity, "toKind": target_kind,
                         "field": field, "path": path + "." + field,
                         "type": node["$type"], "serverActionIndex": action,
                         "asChildBuff": child is True,
                         "assignBlackboard": node.get("assignBlackboard") is True,
                         "assignItems": _plain(node.get("assignItems", []))}
                if dynamic:
                    unresolved.append({**proof, "reason": "dynamic-blackboard-id", "blackboardKey": key})
                elif isinstance(target, str) and target:
                    edges.append({**proof, "toId": target})
            pending.extend((path + "." + key, item, action, child)
                           for key, item in reversed(list(node.items())) if not key.startswith("$"))
        elif isinstance(node, list):
            pending.extend((f"{path}[{index}]", item, action, child)
                           for index, item in reversed(list(enumerate(node))))
    return edges, unresolved


def _static_config(root):
    stacking = root.get("stackingSettings", {})
    refs = []
    pending = [("$", root)]
    while pending:
        path, node = pending.pop()
        if isinstance(node, dict):
            if node.get("useBlackboardKey") is True and node.get("blackboardKey"):
                refs.append({"path": path, "key": node["blackboardKey"]})
            pending.extend((path + "." + key, item) for key, item in node.items() if not key.startswith("$"))
        elif isinstance(node, list):
            pending.extend((f"{path}[{index}]", item) for index, item in enumerate(node))
    if stacking.get("useMaxStackCntKey") is True:
        refs.append({"path": "$.stackingSettings.maxStackCnt", "key": stacking.get("maxStackCntKey")})
    return {"valueKind": "static-configuration", "stackingType": stacking.get("stackingType"),
            "lifeType": root.get("lifeType"), "maxStack": {
                "usesBlackboard": stacking.get("useMaxStackCntKey") is True,
                "blackboardKey": stacking.get("maxStackCntKey"),
                "configuredValue": stacking.get("maxStackCnt")},
            "duration": _plain(root.get("duration")),
            "stackingSettings": _plain(stacking),
            "blackboard": _blackboard(root),
            "blackboardReferences": sorted(refs, key=lambda row: (row["path"], str(row["key"])))}


def _source_roots(tables):
    texts = tables["I18nTextTable_CN"]
    roots = []
    for identity, row in sorted(tables.get("EnemyTable", {}).items()):
        if row.get("enemyId") != identity:
            raise ValueError("EnemyTable identity differs from its exact key")
        template_id = row.get("templateId")
        display = tables.get("EnemyDisplayInfoTable", {}).get(identity, {})
        if display and display.get("enemyId") != identity:
            raise ValueError("Enemy display identity differs from its exact key")
        template = tables.get("EnemyTemplateDisplayInfoTable", {}).get(template_id, {})
        name = _text(display.get("name"), texts) or _text(template.get("name"), texts)
        for buff in row.get("bornBuffs", []):
            if isinstance(buff, str) and buff:
                roots.append({"sourceType": "enemy", "sourceId": identity, "sourceName": name,
                              "skillId": "", "rootId": buff, "kind": "BuffData",
                              "field": "EnemyTable.bornBuffs", "sourceScope": "born-buff-configuration",
                              "enemyTemplateId": template_id})
    for identity, row in sorted(tables["WeaponBasicTable"].items()):
        name = _text(tables["ItemTable"].get(identity, {}).get("name"), texts)
        for field in ("weaponPotentialSkill", "weaponSkillList"):
            values = row.get(field, [])
            for skill in values if isinstance(values, list) else [values]:
                if isinstance(skill, str) and skill:
                    roots.append({"sourceType": "weapon", "sourceId": identity, "sourceName": name,
                                  "skillId": skill, "field": "WeaponBasicTable." + field,
                                  "kind": "SkillData"})
    for identity, row in sorted(tables["CharGrowthTable"].items()):
        name = _text(row.get("name"), texts)
        for group_id, group in sorted(row.get("skillGroupMap", {}).items()):
            for skill in group.get("skillIdList", []):
                if isinstance(skill, str) and skill:
                    roots.append({"sourceType": "character", "sourceId": identity, "sourceName": name,
                                  "skillId": skill, "skillName": _text(group.get("name"), texts),
                                  "skillDescription": _text(group.get("desc"), texts),
                                  "field": "CharGrowthTable.skillGroupMap.skillIdList",
                                  "skillGroupId": group_id, "kind": "SkillData"})
        for node_id, node in sorted(row.get("talentNodeMap", {}).items()):
            passive = node.get("passiveSkillNodeInfo", {})
            effect_id = passive.get("talentEffectId")
            effect = tables["PotentialTalentEffectTable"].get(effect_id, {})
            for index, data in enumerate(effect.get("dataList", [])):
                for field, key, kind in (("attachSkill", "skillId", "SkillData"), ("attachBuff", "buffId", "BuffData")):
                    target = data.get(field, {}).get(key)
                    if isinstance(target, str) and target:
                        roots.append({"sourceType": "character", "sourceId": identity, "sourceName": name,
                                      "skillId": target if kind == "SkillData" else "",
                                      "rootId": target, "skillName": _text(passive.get("name"), texts),
                                      "skillDescription": _text(effect.get("desc"), texts),
                                      "field": "CharGrowthTable.talentNodeMap.passiveSkillNodeInfo.talentEffectId"
                                               + f"->PotentialTalentEffectTable.dataList[{index}].{field}.{key}",
                                      "talentEffectId": effect_id, "talentNodeId": node_id, "kind": kind})
    for suit_id, row in sorted(tables["EquipSuitTable"].items()):
        for level in row.get("list", []):
            skill = level.get("skillID")
            if not isinstance(skill, str) or not skill:
                continue
            relation = {"sourceType": "equipment-set", "sourceId": suit_id,
                        "sourceName": _text(level.get("suitName"), texts), "skillId": skill,
                        "field": "EquipSuitTable.list.skillID", "kind": "SkillData",
                        "requiredEquipCount": level.get("equipCnt"), "skillLevel": level.get("skillLv")}
            roots.append(relation)
            for equipment_id, equip in sorted(tables["EquipTable"].items()):
                if equip.get("suitID") == suit_id and equipment_id in row.get("equipList", []):
                    roots.append({**relation, "sourceType": "equipment", "sourceId": equipment_id,
                                  "sourceName": _text(tables["ItemTable"].get(equipment_id, {}).get("name"), texts),
                                  "setId": suit_id,
                                  "field": "EquipTable.suitID->EquipSuitTable.equipList/list.skillID"})
    return roots


def _effective_parameters(root, patch_rows, steps, buff):
    """Patch replaces template values; only explicit AssignPair edges propagate keys."""
    template = _blackboard(root)
    variants = patch_rows or [{"level": root.get("level"), "blackboard": []}]
    result = []
    for patch in variants:
        values = {key: dict(value) for key, value in template.items()}
        for pair in patch.get("blackboard", []):
            if pair.get("key"):
                values[pair["key"]] = {"value": pair.get("value"), "valueStr": pair.get("valueStr", ""),
                                       "isDynamic": False, "valueSource": "SkillPatchTable"}
        for edge in steps:
            if edge.get("toKind") != "BuffData":
                # No undocumented implicit blackboard inheritance between skills.
                values = {}
                continue
            assigned = {}
            if edge.get("assignBlackboard") is True:
                for pair in edge.get("assignItems", []):
                    target = pair.get("targetKey")
                    if not isinstance(target, str) or not target:
                        continue
                    if pair.get("useDirectValue") is True:
                        assigned[target] = {"value": pair.get("numericValue"), "valueStr": pair.get("stringValue", ""),
                                            "isDynamic": False, "valueSource": "AssignPair.direct"}
                    elif pair.get("inputValueKey") in values:
                        assigned[target] = {**values[pair["inputValueKey"]], "inputValueKey": pair["inputValueKey"]}
            values = assigned
        stacking = buff.get("stackingSettings", {})
        duration = buff.get("duration", {})
        maximum = (values.get(stacking.get("maxStackCntKey")) if stacking.get("useMaxStackCntKey") is True
                   else {"value": stacking.get("maxStackCnt"), "valueSource": "BuffData.stackingSettings"})
        lifetime = (values.get(duration.get("blackboardKey")) if duration.get("useBlackboardKey") is True
                    else {"value": duration.get("value"), "valueSource": "BuffData.duration"})
        result.append({"level": patch.get("level"), "assignedBlackboard": values,
                       "maxStack": maximum, "duration": lifetime})
    return {"templateBlackboard": template, "patchLevels": [{"level": row.get("level"), "blackboard": row.get("blackboard", [])}
             for row in patch_rows], "effectiveLevels": result, "valueKind": "static-configuration-not-runtime"}


def build_definitions(root: Path, source_root: Path | None = None):
    infra = root.parent / "endfield-logs/infra/scripts"
    sys.path.insert(0, str(infra))
    from typed_memorypack import DEFAULT_SCHEMA_PATH, decode_resource, load_schema, load_manifest_string_pool
    from game_resource_snapshot import verify_extraction

    manifest_path = root / "data/local_static/skill/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    source_root = source_root or Path(manifest["localSourceDir"]).parents[1]
    run = source_root.parents[1]
    receipt = json.loads((run / "game_resource_snapshot.json").read_text(encoding="utf-8-sig"))
    verify_extraction(Path(receipt["gameRoot"]), run)
    table_names = ("CharGrowthTable", "PotentialTalentEffectTable", "WeaponBasicTable", "EquipTable",
                   "EquipSuitTable", "ItemTable", "SkillPatchTable", "I18nTextTable_CN", "NumIdStrTable")
    table_names += ("EnemyTable", "EnemyDisplayInfoTable", "EnemyTemplateDisplayInfoTable")
    tables, hashes = {}, {}
    for name in table_names:
        content = (source_root / f"TableCfg/{name}.json").read_bytes()
        tables[name] = json.loads(content.decode("utf-8-sig"))
        hashes[name] = hashlib.sha256(content).hexdigest()
    if tables["NumIdStrTable"] != json.loads((root / "data/local_tables/NumIdStrTable.json").read_text(encoding="utf-8-sig")):
        raise ValueError("Overlay identities do not match the selected extraction")
    typed_bytes = (root / "data/local_static/buff/typed_roots.json.gz").read_bytes()
    typed = json.loads(gzip.decompress(typed_bytes))["entries"]
    schema = load_schema(DEFAULT_SCHEMA_PATH)
    pool = load_manifest_string_pool([manifest_path], schema)
    graph, resources, roots_by_key = {}, {}, {}
    # Every displayed static configuration is bound to the selected extraction,
    # including Buffs that have no proven character/weapon/equipment owner.
    for identity, entry in sorted(typed.items()):
        if any(char in identity for char in ("/", "\\", ":")) or identity in {".", ".."}:
            raise ValueError("Invalid typed Buff identity")
        content = (source_root / f"Json/BuffData/{identity}.json").read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if (entry["proof"]["sourceSha256"] != digest or entry["root"].get("id") != identity
                or entry["root"].get("$end") != len(content)):
            raise ValueError("Buff typed root does not match selected extraction: " + identity)
        resources["BuffData/" + identity] = digest
    sources = _source_roots(tables)
    pending = deque((row["kind"], row.get("rootId") or row["skillId"]) for row in sources)
    while pending:
        kind, identity = pending.popleft()
        key = (kind, identity)
        if key in graph:
            continue
        if any(char in identity for char in ("/", "\\", ":")) or identity in {".", ".."}:
            raise ValueError("Invalid exact resource identity")
        path = source_root / f"Json/{kind}/{identity}.json"
        if not path.is_file():
            graph[key] = ([], [{"reason": "missing-exact-resource", "resourceKind": kind, "resourceId": identity}])
            roots_by_key[key] = {}
            continue
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if kind == "BuffData" and identity in typed:
            entry = typed[identity]
            if entry["proof"]["sourceSha256"] != digest:
                raise ValueError("Buff typed root does not match selected extraction: " + identity)
            decoded = entry["root"]
        else:
            decoded = decode_resource(content, "Beyond.Gameplay.Core." + kind, schema, string_pool=pool)
        if decoded.get("skillId" if kind == "SkillData" else "id") != identity or decoded.get("$end") != len(content):
            raise ValueError("Typed resource did not decode completely: " + identity)
        roots_by_key[key] = decoded
        resources[kind + "/" + identity] = digest
        graph[key] = _edges(decoded, kind, identity)
        pending.extend((edge["toKind"], edge["toId"]) for edge in graph[key][0])
    relations = defaultdict(list)
    paths = defaultdict(list)
    parameters = defaultdict(list)
    unresolved_by_buff = defaultdict(list)
    texts = tables["I18nTextTable_CN"]
    for source in sources:
        start = (source["kind"], source.get("rootId") or source["skillId"])
        queue, seen = deque([(start, [])]), set()
        patch_rows = tables["SkillPatchTable"].get(source["skillId"], {}).get("SkillPatchDataBundle", [])
        first_patch = patch_rows[0] if patch_rows else {}
        relation = {key: value for key, value in source.items() if key not in {"kind", "rootId"}}
        relation.update({"skillName": relation.get("skillName") or _text(first_patch.get("skillName"), texts),
                         "skillDescription": relation.get("skillDescription") or _text(first_patch.get("description"), texts),
                         "relationKind": "official-static-definition"})
        while queue:
            key, steps = queue.popleft()
            kind, identity = key
            if kind == "BuffData" and identity in typed:
                relations[identity].append(relation)
                paths[identity].append({"sourceType": source["sourceType"], "sourceId": source["sourceId"],
                                        "skillId": source["skillId"], "field": source["field"], "steps": steps})
                if start[0] == "SkillData":
                    parameters[identity].append({"sourceType": source["sourceType"], "sourceId": source["sourceId"],
                       "skillId": source["skillId"], "sourcePathIndex": len(paths[identity]) - 1,
                       **_effective_parameters(roots_by_key[start], patch_rows, steps, typed[identity]["root"])})
                unresolved_by_buff[identity].extend(graph[key][1])
            if key in seen:
                continue
            seen.add(key)
            queue.extend(((edge["toKind"], edge["toId"]), steps + [edge]) for edge in graph[key][0])
    numeric = defaultdict(list)
    for number, identity in tables["NumIdStrTable"]["buff_id"]["dic"].items():
        numeric[identity].append(str(number))
    notes = json.loads((root / "data/overlay/descriptions.json").read_text(encoding="utf-8-sig"))
    if notes.get("source", {}).get("typedRootsSha256") != hashlib.sha256(typed_bytes).hexdigest():
        raise ValueError("Descriptions do not match the selected typed roots")
    catalog = json.loads((root / "data/overlay/effects.json").read_text(encoding="utf-8-sig"))
    if catalog.get("source", {}).get("typedRootsSha256") != hashlib.sha256(typed_bytes).hexdigest():
        raise ValueError("Effect catalog does not match the selected typed roots")
    icons = {row["canonicalId"]: row for row in catalog["entries"]}
    entries = []
    for identity in sorted(set(typed) | set(numeric)):
        config = typed.get(identity, {}).get("root", {})
        note = notes["entries"].get(identity, {})
        source_relations = sorted({json.dumps(row, ensure_ascii=False, sort_keys=True): row for row in relations[identity]}.values(),
                                 key=lambda row: (row["sourceType"], row["sourceId"], row["skillId"], row["field"]))
        label = note.get("label")
        label_source = "official-config-summary" if label else None
        if source_relations:
            names = sorted({row["sourceName"] or row["skillName"] for row in source_relations if row["sourceName"] or row["skillName"]})
            if names:
                category = {"Stack": "堆叠配置", "NonStack": "效果配置"}.get(config.get("stackingSettings", {}).get("stackingType"), "效果配置")
                label = (names[0] if len(names) == 1 else "多个官方来源") + " · " + category
                label_source = "official-source-config-summary"
        icon = icons.get(identity, {})
        entries.append({"effectId": identity, "canonicalId": identity, "numericIds": sorted(numeric[identity], key=int),
                        "label": label, "labelSource": label_source,
                        "description": note.get("description"), "descriptionSource": note.get("descriptionSource"),
                        "sourceRelations": source_relations,
                        "iconKey": config.get("iconConfig", {}).get("_spritePath") if config.get("hasIcon") is True else None,
                        "iconPresentation": icon.get("iconPresentation"), "hasIcon": config.get("hasIcon") is True,
                        "staticConfig": _static_config(config), "definitionOnly": True,
                        "details": {"sourcePaths": paths[identity], "skillParameters": parameters[identity],
                                    "unresolvedReferences": unresolved_by_buff[identity],
                                    "buffSourceSha256": typed.get(identity, {}).get("proof", {}).get("sourceSha256")}})
    return {"schemaVersion": 1, "coverage": "official-static-definition-graph",
            "source": {"typedRootsSha256": hashlib.sha256(typed_bytes).hexdigest(),
                       "skillManifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                       "tablesSha256": hashes, "resourcesSha256": dict(sorted(resources.items())),
                       "resourceFingerprint": {"source": receipt["source"], "extracted": receipt["extracted"]}},
            "summary": {"entryCount": len(entries), "linkedBuffCount": sum(bool(row["sourceRelations"]) for row in entries),
                        "sourceRootCount": len(sources), "verifiedResourceCount": len(resources),
                        "decodedGraphResourceCount": len(roots_by_key),
                        "unresolvedReferenceCount": sum(len(value[1]) for value in graph.values()),
                        "sourceTypes": dict(sorted((kind, sum(any(rel["sourceType"] == kind for rel in row["sourceRelations"]) for row in entries))
                                                   for kind in {row["sourceType"] for row in sources}))},
            "unresolvedReferences": [{"resourceKind": kind, "resourceId": identity, **row}
                                      for (kind, identity), (_, rows) in sorted(graph.items()) for row in rows],
            "entries": entries}
