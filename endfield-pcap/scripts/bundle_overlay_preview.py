"""Bundle only the isolated preview, editable examples and API documentation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from verify_overlay_preview import verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    application = args.application.resolve(strict=True)
    if not str(application).lower().startswith("d:\\test\\_agent\\endfield-overlays-20261018\\dist\\"):
        raise ValueError("Expected this task's owned preview directory")
    examples = application / "examples"
    examples.mkdir(exist_ok=True)
    for filename in ("effects-monitor.html", "topic-inspector.html", "data-api-minimal.html", "wolfblood-monitor.html"):
        shutil.copy2(source / "src/endfield_pcap/overlay_examples" / filename, examples / filename)
    shutil.copy2(source / "docs/overlay_pages_v1.md", application / "开发接口与测试说明.md")
    shutil.copy2(source / "docs/overlay_data_api_v1.md", application / "数据接口与HTML示例说明.md")
    # Keep the relative API link and the acceptance evidence usable in the
    # standalone bundle, without including any capture or user records.
    shutil.copy2(source / "docs/overlay_data_api_v1.md", application / "overlay_data_api_v1.md")
    shutil.copy2(source / "docs/overlay_wolfblood_example.md", application / "狼血示例说明.md")
    shutil.copy2(source / "docs/overlay_wolfblood_example.md", application / "overlay_wolfblood_example.md")
    evidence = application / "docs/evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    for filename in ("overlay_preview_v4_acceptance_20261019.json", "overlay_generic_api_replay_20261019.json",
                     "overlay_effect_definitions_20261019.json", "overlay_action_finish_native_20261019.json",
                     "overlay_keyword_native_20261019.json", "overlay_runtime_value_boundaries_20261019.json",
                     "overlay_wolfblood_example_acceptance_20261020.json", "overlay_client_operation_sequence_native_20261020.json",
                     "overlay_wolf_stack_native_evidence_20261020.json", "overlay_wolf_parent_child_upload_native_20261020.json",
                     "overlay_wolf_static_scope_summary_20261020.json", "overlay_wolf_static_graph_evidence_20261020.json",
                     "overlay_all_exact_layer_final_references_20261020.json", "overlay_extend_buff_exclusion_evidence_20261020.json",
                     "overlay_selected_enum_schema_20261020.json"):
        shutil.copy2(source / "docs/evidence" / filename, evidence / filename)
    (application / "启动预览.cmd").write_text('@echo off\r\nstart "" "%~dp0EndfieldOverlayPreview.exe" --state-root "%~dp0state"\r\n', encoding="ascii")
    (application / "使用说明.txt").write_text(
        "终末地网页悬浮窗 · 本地测试版 0.1.0-preview.6\n\n"
        "双击“启动预览.cmd”打开设置与效果监控。这个独立程序不采集、不上传；没有实时数据时显示等待状态。\n"
        "examples 中的 HTML 可以自行编辑，再从窗口管理中的“添加本地 HTML”加载。data-api-minimal.html 为无编辑器的最小接口示例。\n"
        "本机完整客户端的实战测试入口：C:\\path\\to\\endfield-suite\\启动悬浮窗实战测试.cmd。请先关闭正式客户端，测试期间关闭自动上传。\n"
        "尚未证明游戏所有效果的实战覆盖。未知层数、时长、来源和目标关系不会推算。\n",
        encoding="utf-8")
    result = verify(source, application)
    if not result["ok"]:
        raise ValueError("Frozen source/assets verification failed")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        for path in sorted(application.rglob("*")):
            if path.is_file():
                archive.write(path, str(Path(application.name) / path.relative_to(application)))
    with zipfile.ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive CRC verification failed")
    with args.output.open("rb") as stream:
        result["archiveSha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    result.update(archiveBytes=args.output.stat().st_size, archivePath=str(args.output),
                  archiveCrcVerified=True, formalReleasePublished=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("ok", "version", "archiveBytes", "archiveSha256", "fileCount")}))


if __name__ == "__main__":
    main()
