"""Isolated local preview; never starts capture or uploads a battle."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Register the secure scheme before the first QApplication.
from .overlay_windows import OverlayWindowManager
from .overlay_topics import get_overlay_broker
from .overlay_window_settings import OverlaySettingsDialog

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication


PREVIEW_VERSION = "0.1.0-preview.6"


def _evaluate(page, source: str):
    loop = QEventLoop()
    result = []
    page.runJavaScript(source, lambda value: (result.append(value), loop.quit()))
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    if not result:
        raise TimeoutError("网页检查没有返回")
    return result[0]


def validate_runtime(app, root: Path, *, screenshot: Path | None = None, public_url: str | None = None) -> dict:
    """Exercise the packaged bridge and actual example pages with mock events."""
    broker = get_overlay_broker()
    manager = OverlayWindowManager(broker, root / "windows.json", root / "profiles")
    examples = Path(__file__).with_name("overlay_examples")
    checks = []

    def wait(predicate, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError("网页运行检查超时")

    try:
        # Repeated validation uses a new caller-owned state directory. No user
        # settings or HTML files are deleted or rewritten by the validation.
        monitor = manager.add_entry("效果监控 · 模拟验收", "file", str(examples / "effects-monitor.html"))
        inspector = manager.add_entry("数据检查 · 模拟验收", "file", str(examples / "topic-inspector.html"))
        first, second = manager.windows[monitor["id"]], manager.windows[inspector["id"]]
        catalog_count = len(broker.inspect("effects.catalog.v1")["data"]["effects"])
        assert catalog_count > 0
        wait(lambda: _evaluate(first.page, f"document.getElementById('catalog-note')?.textContent?.includes('目录 {catalog_count} 项')") is True)
        wait(lambda: _evaluate(second.page, "document.getElementById('topic')?.options.length") == len(broker.get_topics()))
        checks.append("actual_examples_and_full_catalog")
        assert _evaluate(first.page, "JSON.stringify(Object.keys(window.endfieldOverlay).sort())") == '["getTopics","subscribe"]'
        assert _evaluate(first.page, "typeof qt") == "undefined"
        checks.append("only_readonly_api_in_main_world")
        _evaluate(first.page, "window.__limitResult=null;window.__limitCancel=window.endfieldOverlay.subscribe({topic:'effects.actual.v1',filter:{effectIds:['x'.repeat(40000)]}},value=>window.__limitResult=value);true")
        wait(lambda: _evaluate(first.page, "window.__limitResult?.reason==='overlay-request-limit'") is True, seconds=2)
        _evaluate(first.page, "window.__limitCancel();true")
        checks.append("oversized_request_reports_limit_immediately")
        _evaluate(first.page, "localStorage.setItem('validation-marker','monitor')")
        assert _evaluate(second.page, "localStorage.getItem('validation-marker')===null")
        checks.append("per_instance_storage_isolation")
        # Definition lookup uses the same two-method read-only bridge. The
        # first asynchronous load may report loading before delivering a page.
        _evaluate(first.page, "window.__definitionStates=[];window.__definitionCancel=window.endfieldOverlay.subscribe({topic:'effects.definitions.v1',filter:{query:'狼之绯',sourceTypes:['weapon'],limit:10}},value=>{window.__definitionStates.push(value.state);window.__definitionResult=value;});true")
        wait(lambda: _evaluate(first.page, "window.__definitionResult?.state==='ready'") is True)
        definitions = json.loads(_evaluate(first.page, "JSON.stringify(window.__definitionResult.data)"))
        assert {"buff_wpn_sword_0022_layer", "buff_wpn_sword_0022_final"} <= {
            row["canonicalId"] for row in definitions["definitions"]}
        assert definitions["scope"] == "static-configuration" and definitions["runtimeValues"] == "not_provided"
        assert all(row["definitionOnly"] is True for row in definitions["definitions"])
        _evaluate(first.page, "window.__definitionCancel();true")
        checks.append("paged_definition_lookup_through_actual_readonly_bridge")
        minimal_entry = manager.add_entry("最小只读示例 · 模拟验收", "file", str(examples / "data-api-minimal.html"))
        minimal = manager.windows[minimal_entry["id"]]
        wait(lambda: _evaluate(minimal.page, "document.getElementById('status')?.textContent?.includes('旧观测已清空')") is True)
        if public_url:
            remote_entry = manager.add_entry("公网页面 · 模拟验收", "url", public_url)
            remote = manager.windows[remote_entry["id"]]
            wait(lambda: _evaluate(remote.page, "Boolean(document.body && document.body.textContent.trim().length)") is True)
            _evaluate(remote.page, "window.endfieldOverlay.getTopics().then(value=>window.validationTopicCount=value.length)")
            wait(lambda: _evaluate(remote.page, "window.validationTopicCount") == len(broker.get_topics()))
            assert _evaluate(remote.page, "typeof qt") == "undefined"
            checks.append("public_https_entry_and_actual_bridge")
        session = "packaged-mock-validation"
        catalog_data = broker.inspect("effects.catalog.v1")["data"]
        icon_effect = next(row["canonicalId"] for row in catalog_data["effects"]
                           if row.get("hasIcon") is True and
                           (row.get("icon") or catalog_data.get("icons", {}).get(row.get("iconKey"))))
        broker.on_state_change("live", session)
        broker.on_event({"type": "SC_SELF_SCENE_INFO", "session_id": session, "timestamp_ms": 1000,
                         "char_list": [{"battle_inst_id": "100", "id": "1000", "templateid": "validation_character", "display_name": "验收角色"}]})
        broker.on_event({"type": "BattleOpAddBuff", "session_id": session, "timestamp_ms": 1100,
                         "buff_inst_id": "9007199254740993", "str_id": icon_effect, "target_inst_id": "100"})
        broker.flush()
        wait(lambda: _evaluate(first.page, "document.querySelectorAll('.effect').length") == 1)
        assert _evaluate(first.page, "document.querySelector('.effect').dataset.instanceId") == "9007199254740993"
        assert _evaluate(first.page, "document.querySelector('.effect').title.includes('层数未知')")
        assert _evaluate(first.page, "document.querySelector('.effect').dataset.effectState") == "unknown"
        assert _evaluate(first.page, "document.querySelector('.effect .effect-status').textContent") == "状态未知"
        wait(lambda: _evaluate(minimal.page, "document.querySelectorAll('#effects li').length") == 1)
        assert _evaluate(minimal.page, "document.getElementById('effects').textContent.includes('层数：不支持')")
        assert _evaluate(minimal.page, "document.querySelectorAll('input,select,textarea,button').length") == 0
        checks.append("minimal_html_actual_bridge_unknown_fields_without_editor")
        wait(lambda: _evaluate(first.page, "[...document.images].every(x=>x.complete&&x.naturalWidth>0)") is True)
        checks.append("mock_exact_instance_and_unknown_fields")
        broker.on_event({"type": "BattleOpAddBuff", "session_id": session, "timestamp_ms": 1200,
                         "buff_inst_id": "9007199254740994", "str_id": "unverified_validation_effect", "target_inst_id": "100"})
        broker.flush()
        assert _evaluate(first.page, "document.getElementById('display-mode').value") == "game-icons"
        _evaluate(first.page, "document.getElementById('display-mode').value='all';document.getElementById('display-mode').dispatchEvent(new Event('change'))")
        wait(lambda: _evaluate(first.page, "document.querySelectorAll('.effect').length") == 2)
        _evaluate(first.page, "document.getElementById('hide-unknown').click()")
        assert _evaluate(first.page, "document.querySelectorAll('.effect').length") == 1
        _evaluate(first.page, "document.getElementById('hide-unknown').click()")
        assert _evaluate(first.page, "document.querySelectorAll('.effect').length") == 2
        _evaluate(first.page, "document.getElementById('display-mode').value='game-icons';document.getElementById('display-mode').dispatchEvent(new Event('change'))")
        wait(lambda: _evaluate(first.page, "document.querySelectorAll('.effect').length") == 1)
        checks.append("game_icon_scope_and_optional_unidentified_effects")
        manager.reload_entry(monitor["id"])
        wait(lambda: _evaluate(first.page, "document.querySelectorAll('.effect').length") == 1)
        assert _evaluate(first.page, "localStorage.getItem('validation-marker')") == "monitor"
        checks.append("reload_current_snapshot_and_persistent_settings")
        # Explicit initial-snapshot booleans exercise all three presentation
        # states without inventing an enable-op owner relation or native gate.
        broker.on_event({"type": "SC_EFFECT_SNAPSHOT", "session_id": session, "timestamp_ms": 1300,
                         "owners": [{"owner_battle_inst_id": "100", "generation": 0, "buffs": [
                             {"inst_id": "101", "buff_id": {"str_id": icon_effect}, "is_enable": True},
                             {"inst_id": "102", "buff_id": {"str_id": icon_effect}, "is_enable": False},
                             {"inst_id": "103", "buff_id": {"str_id": icon_effect}},
                         ]}]})
        broker.flush()
        wait(lambda: _evaluate(first.page, "document.querySelectorAll('.effect').length") == 4)
        wait(lambda: _evaluate(minimal.page, "document.querySelectorAll('#effects li').length") == 5)
        assert _evaluate(minimal.page, "document.getElementById('effects').textContent.includes('已启用')")
        assert _evaluate(minimal.page, "document.getElementById('effects').textContent.includes('已禁用')")
        assert _evaluate(first.page, "document.querySelectorAll('[data-effect-state=active]').length") == 1
        assert _evaluate(first.page, "document.querySelectorAll('[data-effect-state=disabled]').length") == 1
        assert _evaluate(first.page, "document.querySelectorAll('[data-effect-state=unknown]').length") == 2
        assert _evaluate(first.page, "![...document.querySelectorAll('.effect')].some(x=>/观测\\s*\\d+\\s*条/.test(x.textContent))")
        _evaluate(first.page, "document.getElementById('settings').open=false;document.getElementById('settings').ontoggle()")
        wait(lambda: _evaluate(first.page, "document.querySelectorAll('.effect').length") == 3)
        _evaluate(first.page, "document.getElementById('hide-unknown-state').click()")
        assert _evaluate(first.page, "document.querySelectorAll('.effect').length") == 1
        assert _evaluate(first.page, "document.querySelector('.effect').dataset.effectState") == "active"
        _evaluate(first.page, "document.getElementById('hide-unknown-state').click()")
        wait(lambda: _evaluate(first.page, "document.querySelectorAll('.effect').length") == 3)
        checks.append("explicit_active_disabled_unknown_states_and_filters")
        # Large actual snapshots must remain readable through the real bridge;
        # pages expose every retained match without raising the bridge limit.
        for number in range(500):
            broker.on_event({"type": "BattleOpAddBuff", "session_id": session, "timestamp_ms": 1400 + number,
                             "buff_inst_id": str(10000 + number), "str_id": "unverified_validation_budget_effect", "target_inst_id": "777"})
        broker.flush()
        _evaluate(first.page, "window.__budgetResult=null;window.__budgetCancel=window.endfieldOverlay.subscribe({topic:'effects.actual.v1',filter:{targetIds:['777']}},value=>window.__budgetResult=value);true")
        wait(lambda: _evaluate(first.page, "window.__budgetResult?.data?.matchingEffectCount===500") is True)
        budget_first = json.loads(_evaluate(first.page, "JSON.stringify({count:window.__budgetResult.data.effects.length,next:window.__budgetResult.data.nextOffset,more:window.__budgetResult.data.hasMore,reason:window.__budgetResult.data.projectionReason,ids:window.__budgetResult.data.effects.map(x=>x.instanceId)})"))
        assert budget_first["more"] and budget_first["reason"] == "payload_budget"
        _evaluate(first.page, f"window.__budgetCancel();window.__budgetResult=null;window.__budgetCancel=window.endfieldOverlay.subscribe({{topic:'effects.actual.v1',filter:{{targetIds:['777'],offset:{budget_first['next']}}}}},value=>window.__budgetResult=value);true")
        wait(lambda: _evaluate(first.page, "window.__budgetResult?.state==='ready'") is True)
        budget_second = json.loads(_evaluate(first.page, "JSON.stringify({more:window.__budgetResult.data.hasMore,ids:window.__budgetResult.data.effects.map(x=>x.instanceId)})"))
        assert not budget_second["more"]
        assert len(set(budget_first["ids"]) | set(budget_second["ids"])) == 500
        assert len(budget_first["ids"]) + len(budget_second["ids"]) == 500
        _evaluate(first.page, "window.__budgetCancel();true")
        checks.append("large_actual_snapshot_pages_through_bridge_without_record_loss")
        broker.on_event({"type": "CAPTURE_BATTLE_OPERATION", "session_id": session, "timestamp_ms": 2000,
                         "seq_id": 100, "operation_type": "BattleOpSkillStartCast", "op_type": 1})
        broker.on_event({"type": "CAPTURE_BATTLE_OPERATION", "session_id": session, "timestamp_ms": 2001,
                         "seq_id": 101, "operation_type": "UnknownBattleOperation", "op_type": 999999})
        broker.flush()
        _evaluate(first.page, "window.__operations=null;window.__operationCancel=window.endfieldOverlay.subscribe({topic:'effects.operations.v1',filter:{limit:256}},value=>window.__operations=value);true")
        wait(lambda: _evaluate(first.page, "window.__operations?.data?.operations.length===2") is True)
        assert _evaluate(first.page, "JSON.stringify(window.__operations.data.operations.map(x=>x.seqId))") == "[100,101]"
        assert _evaluate(first.page, "window.__operations.data.sourceMode") == "raw-operation-header"
        _evaluate(first.page, "window.__operationCancel();true")
        checks.append("readonly_raw_operation_headers_through_actual_bridge")
        broker.on_state_change("waiting_connection", None)
        wait(lambda: _evaluate(first.page, "document.querySelectorAll('.effect').length") == 0)
        wait(lambda: _evaluate(minimal.page, "document.querySelectorAll('#effects li').length") == 0)
        assert broker.inspect("effects.actual.v1")["data"] is None
        checks.append("disconnect_clears_actual_example")
        broker.on_state_change("live", "new-validation-session")
        assert broker.inspect("effects.actual.v1")["data"] is None
        checks.append("reconnect_waits_for_fresh_data")
        manager.update_entry(monitor["id"], visible=False, locked=True, clickThrough=True, opacity=.3)
        recovered = manager.recover_entry(monitor["id"])
        assert recovered["visible"] and not recovered["locked"] and not recovered["clickThrough"] and recovered["opacity"] == 1
        checks.append("window_controls_and_recover")
        dialog = OverlaySettingsDialog(manager, example_root=examples)
        dialog.items.setCurrentRow(2)
        dialog.show()
        app.processEvents()
        assert dialog.items.count() == 2 + len(manager.list_entries())
        if screenshot:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(screenshot), "PNG")
            assert first.view.grab().save(str(screenshot.with_name(screenshot.stem + "-monitor.png")), "PNG")
        dialog.close()
        checks.append("trusted_native_settings")
        return {"version": PREVIEW_VERSION, "frozen": bool(getattr(sys, "frozen", False)),
                "validationKind": "mock-events-with-actual-QtWebEngine-pages", "checks": checks,
                "gameCaptureStarted": False, "uploaderStarted": False}
    finally:
        manager.close()
        broker.close()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def main() -> int:
    parser = argparse.ArgumentParser(description="终末地网页悬浮窗本地测试版，不启动采集或上传")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--validate-url", help="Optional public HTTPS page for actual bridge verification")
    args = parser.parse_args()
    root = args.state_root or Path(os.environ.get("ENDFIELD_OVERLAY_SETTINGS_ROOT") or
        (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "EndfieldPCAP" / "web-overlays-preview"))
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    if args.validate:
        result = validate_runtime(app, root, screenshot=args.screenshot, public_url=args.validate_url)
        if args.result:
            args.result.parent.mkdir(parents=True, exist_ok=True)
            args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    broker = get_overlay_broker()
    manager = OverlayWindowManager(broker, root / "windows.json", root / "profiles")
    examples = Path(__file__).with_name("overlay_examples")
    if not manager.list_entries():
        entry = manager.add_entry("效果监控", "file", str(examples / "effects-monitor.html"))
        manager.update_entry(entry["id"], geometry={"x": 80, "y": 120, "width": 460, "height": 610})
    dialog = OverlaySettingsDialog(manager, example_root=examples)
    dialog.setWindowTitle("悬浮窗本地预览 · 不采集、不上传")
    if manager.list_entries():
        dialog.refresh_list(manager.list_entries()[0]["id"])
    dialog.finished.connect(app.quit)
    dialog.show()
    try:
        return app.exec()
    finally:
        manager.close()
        broker.close()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    raise SystemExit(main())
