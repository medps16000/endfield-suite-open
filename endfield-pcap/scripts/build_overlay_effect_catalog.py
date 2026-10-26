"""Build a small display catalog from exact official ID tables and typed roots."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from endfield_pcap.overlay_effect_catalog import classify_official_icon_group
from endfield_pcap.overlay_effect_descriptions import describe_official_buff


def build_catalog(root: Path, icons_dir: Path | None = None) -> dict:
    numbers_path = root / "data/local_tables/NumIdStrTable.json"
    typed_path = root / "data/local_static/buff/typed_roots.json.gz"
    numbers_bytes = numbers_path.read_bytes()
    typed_bytes = typed_path.read_bytes()
    numbers = json.loads(numbers_bytes.decode("utf-8-sig"))["buff_id"]["dic"]
    typed = json.loads(gzip.decompress(typed_bytes))["entries"]
    aliases: dict[str, list[str]] = {}
    for number, canonical in numbers.items():
        if not str(number).isascii() or not str(number).isdecimal() or not isinstance(canonical, str) or not canonical:
            raise ValueError("Invalid official Buff identity table")
        aliases.setdefault(canonical, []).append(str(number))
    entries = []
    for identity in sorted(set(typed) | set(aliases)):
        config = typed.get(identity, {}).get("root", {})
        order = config.get("iconConfig", {}).get("_orderPriorityConfig", {})
        group = order.get("priorityEnum") if config.get("hasIcon") is True else None
        group = group if isinstance(group, str) else None
        numeric_priority = order.get("useDirectoryValue") if config.get("hasIcon") is True else None
        numeric_priority = numeric_priority if type(numeric_priority) is bool else None
        classification = classify_official_icon_group(config.get("hasIcon"), group, numeric_priority)
        icon = None
        sprite = config.get("iconConfig", {}).get("_spritePath")
        if config.get("hasIcon") is True and isinstance(sprite, str) and sprite and Path(sprite).name == sprite:
            # Both sources use the exact configured sprite identity. Never
            # substitute a neighboring Buff's icon or a basename candidate.
            for directory in (icons_dir, root / "src/endfield_pcap/overlay_assets/effects"):
                if directory is None:
                    continue
                path = directory / (sprite + ".webp")
                if path.is_file() and path.stat().st_size <= 60_000:
                    icon = "data:image/webp;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
                    break
        icon_presentation = None
        if config.get("hasIcon") is True:
            if sprite == "" and config.get("iconConfig", {}).get("showProgressInNormalSkillButton") is True:
                icon_presentation = "skill-button-progress"
            elif isinstance(sprite, str) and sprite:
                icon_presentation = "image" if icon is not None else "missing-asset"
        entries.append({"effectId": identity, "canonicalId": identity,
                        "numericIds": sorted(aliases.get(identity, []), key=int),
                        "label": None, "classification": classification, "icon": icon,
                        "uiGroup": group, "uiGroupUsesNumericPriority": numeric_priority,
                        "classificationSource": "official-icon-priority-group" if classification != "unclassified" else None,
                        "iconPresentation": icon_presentation,
                        "hasIcon": config.get("hasIcon") is True})
    return {"schemaVersion": 1, "coverage": "official-id-catalog", "source": {
        "numIdTableSha256": hashlib.sha256(numbers_bytes).hexdigest(),
        "typedRootsSha256": hashlib.sha256(typed_bytes).hexdigest(),
    }, "entries": entries}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--icons-dir", type=Path)
    parser.add_argument("--targets-only", action="store_true")
    parser.add_argument("--definitions-only", action="store_true")
    parser.add_argument("--source-root", type=Path,
                        help="Exact extracted Data directory for read-only definitions")
    args = parser.parse_args()
    if args.targets_only and args.definitions_only:
        parser.error("--targets-only and --definitions-only are mutually exclusive")
    if args.definitions_only:
        from build_overlay_effect_definitions import build_definitions
        result = build_definitions(args.root, args.source_root)
        output = args.output or args.root / "data/overlay/definitions.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(json.dumps({**result["summary"], "bytes": output.stat().st_size, "output": str(output)}, ensure_ascii=False))
        return
    output = args.output or args.root / "data/overlay/effects.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not args.targets_only:
        result = build_catalog(args.root, args.icons_dir)
        output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    target_entries = {}
    target_source = {}
    for kind in ("character", "enemy"):
        path = args.root / "data/local_tables" / kind / "manifest.json"
        content = path.read_bytes()
        target_source[kind + "ManifestSha256"] = hashlib.sha256(content).hexdigest()
        manifest = json.loads(content.decode("utf-8-sig"))
        for row in manifest["entries"]:
            identity, label = row.get("templateId") or row.get("charId") or row.get("id"), row.get("name")
            if isinstance(identity, str) and isinstance(label, str) and label:
                target_entries[identity] = {"label": label}
        if kind == "enemy":
            # Enemy instances use EnemyTable.enemyId; the display manifest uses
            # its exact templateId foreign key. Never derive variants by name.
            table_path = (args.root.parent / manifest["sourceTablePath"]).with_name("EnemyTable.json")
            table_bytes = table_path.read_bytes()
            target_source["enemyTableSha256"] = hashlib.sha256(table_bytes).hexdigest()
            enemy_labels = {row["templateId"]: row["name"] for row in manifest["entries"]
                            if isinstance(row.get("templateId"), str) and isinstance(row.get("name"), str) and row["name"]}
            for identity, row in json.loads(table_bytes.decode("utf-8-sig")).items():
                if not isinstance(row, dict) or row.get("enemyId") != identity:
                    continue
                label = enemy_labels.get(row.get("templateId"))
                if isinstance(identity, str) and identity and label:
                    if identity in target_entries and target_entries[identity]["label"] != label:
                        raise ValueError("Conflicting exact enemy display identity")
                    target_entries[identity] = {"label": label}
    target_path = output.with_name("targets.json")
    target_path.write_text(json.dumps({"schemaVersion": 1, "source": target_source, "entries": target_entries},
                                     ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.targets_only:
        print(json.dumps({"targetLabels": len(target_entries), "output": str(target_path)}))
        return
    typed_path = args.root / "data/local_static/buff/typed_roots.json.gz"
    typed = json.loads(gzip.decompress(typed_path.read_bytes()))["entries"]
    descriptions = {identity: summary for identity, entry in typed.items()
                    if (summary := describe_official_buff(entry["root"]))}
    output.with_name("descriptions.json").write_text(json.dumps({"schemaVersion": 1,
        "source": {"typedRootsSha256": result["source"]["typedRootsSha256"]}, "entries": descriptions},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"entries": len(result["entries"]), "targetLabels": len(target_entries), "bytes": output.stat().st_size, "output": str(output)}))


if __name__ == "__main__":
    main()
