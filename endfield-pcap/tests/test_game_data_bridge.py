"""Real protobuf -> service -> broker -> isolated Qt HTML bridge coverage."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_every_typed_action_runtime_crosses_the_service_to_public_operation_api():
    from endfield_pcap.message_registry import MessageRegistry
    from endfield_pcap.models import Endpoint, FlowKey, RuntimeMetrics
    from endfield_pcap.overlay_topics import OverlayTopicBroker
    from endfield_pcap.service import SessionPipeline

    registry = MessageRegistry(Path(__file__).resolve().parents[1] / 'data')
    broker = OverlayTopicBroker(publish_interval_ms=0)
    broker.on_state_change('live', 'runtime-data-e2e')
    pipeline = SessionPipeline(
        flow=FlowKey(client=Endpoint('127.0.0.1', 50100), server=Endpoint('127.0.0.2', 30000)),
        session_id='runtime-data-e2e', registry=registry, private_key=None, srsa_bridge=None,
        name_index={}, multi_phase_dungeon_map={}, merge_multi_phase_enemy_battles=False,
        on_event=lambda event: broker.on_event(event.as_dict()), on_battle_operation=broker.on_event,
        on_debug_message=None, on_debug_record=None, metrics=RuntimeMetrics(),
    )
    request = registry.load_message_class('CS_BATTLE_OP')()
    action_descriptor = request.client_data.op_list.add().trigger_action_op_data.action.DESCRIPTOR
    request.client_data.ClearField('op_list')
    cases = [field.name for field in action_descriptor.oneofs_by_name['inst_impl'].fields]
    for index, case in enumerate(cases, 1):
        op = request.client_data.op_list.add(seq_id=index)
        op.op_type = op.DESCRIPTOR.fields_by_name['op_type'].enum_type.values_by_name['BattleOpTriggerAction'].number
        trigger = op.trigger_action_op_data
        trigger.action.action_id = index
        # This covers the declared wire shapes, including an explicitly present
        # empty runtime. It does not infer which game condition triggers them.
        getattr(trigger.action, case).SetInParent()
    try:
        msg_id = next(number for number, name in registry._maps['cs'].items() if name == 'CS_BATTLE_OP')
        decoded = registry.decode_messages('cs', {'msgid': msg_id}, request.SerializeToString())
        assert len(decoded) == 1
        pipeline._handle_message(decoded[0], 1000)
        envelope = broker.inspect('effects.operations.v1', {})
        operations = envelope['data']['operations']
        assert len(operations) == len(cases)
        for case, operation in zip(cases, operations, strict=True):
            assert operation['payloadState'] == 'whitelisted-fields', (case, operation)
            assert operation['payload']['action_runtime_case'] == case
            assert case in operation['payload']['action']
        dynamic = next(row for row in operations
                       if row['payload']['action_runtime_case'] == 'modify_dynamic_blackboard_action')
        assert dynamic['payload']['action']['modify_dynamic_blackboard_action']['client_value'] == 0
    finally:
        broker.close()


def test_gameplay_data_reaches_real_html_and_disconnect_clears_it(tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), '--runtime', str(tmp_path)],
        capture_output=True, text=True, timeout=75,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen',
             'QTWEBENGINE_CHROMIUM_FLAGS': '--disable-gpu'},
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-12000:]
    assert 'GAME_DATA_BRIDGE_OK' in result.stdout


def _runtime(root):
    import json
    import time
    from endfield_pcap.overlay_windows import OverlayWindowManager
    from endfield_pcap.overlay_topics import OverlayTopicBroker
    from endfield_pcap.message_registry import MessageRegistry
    from endfield_pcap.models import Endpoint, FlowKey, RuntimeMetrics
    from endfield_pcap.service import SessionPipeline
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    root = Path(root)
    root.mkdir(exist_ok=True, parents=True)
    html = root / 'game-data.html'
    html.write_text('''<!doctype html><html><body><script>
window.gameData=null;window.topics=null;window.hits=0;
window.endfieldOverlay.getTopics().then(value=>window.topics=value);
window.stop=window.endfieldOverlay.subscribe({topic:'game.events.v1',filter:{limit:64}}, value=>{
  window.lastState=value.state;window.lastReason=value.reason;window.hits++;
  window.gameData=value.state==='ready'?value.data:null;
});
</script></body></html>''', encoding='utf-8')
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    broker = OverlayTopicBroker(publish_interval_ms=0)
    broker.on_state_change('live', 'game-data-e2e')
    manager = OverlayWindowManager(broker, root / 'windows.json', root / 'profiles')
    entry = manager.add_entry('数据通路验证', 'file', str(html))
    page = manager.windows[entry['id']].page

    def evaluate(script):
        loop, result = QEventLoop(), []
        page.runJavaScript(script, lambda value: (result.append(value), loop.quit()))
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        assert result, 'JavaScript evaluation timed out'
        return result[0]

    def wait_for(script):
        until = time.monotonic() + 15
        while time.monotonic() < until:
            app.processEvents()
            if evaluate(script):
                return
            time.sleep(.01)
        raise AssertionError(f'HTML condition timed out: {script}')

    data = Path(__file__).resolve().parents[1] / 'data'
    registry = MessageRegistry(data)
    observed = []

    def on_event(event):
        observed.append(event.as_dict())
        broker.on_event(observed[-1])

    pipeline = SessionPipeline(
        flow=FlowKey(client=Endpoint('127.0.0.1', 50100), server=Endpoint('127.0.0.2', 30000)),
        session_id='game-data-e2e', registry=registry, private_key=None, srsa_bridge=None,
        name_index={}, multi_phase_dungeon_map={}, merge_multi_phase_enemy_battles=False,
        on_event=on_event, on_battle_operation=broker.on_event,
        on_debug_message=None, on_debug_record=None, metrics=RuntimeMetrics(),
    )
    try:
        wait_for("Array.isArray(window.topics)&&window.topics.some(x=>x.topic==='game.events.v1')")
        assert evaluate("JSON.stringify(Object.keys(window.endfieldOverlay).sort())") == '["getTopics","subscribe"]'
        message = registry.load_message_class('SC_ATTACH_SERVER_SKILL')()
        message.inst_id = 9001
        skill = message.skills.add(inst_id=9223372036854775917)
        skill.skill_id.int_id = 2665
        skill.blackboard.blackboard['zero'].float_value = 0
        skill.blackboard.blackboard['zero'].is_dynamic = False
        skill.is_enable = False
        message_id = next(number for number, name in registry._maps['sc'].items()
                          if name == 'SC_ATTACH_SERVER_SKILL')
        decoded = registry.decode_messages('sc', {'msgid': message_id}, message.SerializeToString())
        assert len(decoded) == 1
        pipeline._handle_message(decoded[0], 1000)
        broker.flush()
        wait_for("Boolean(window.gameData?.events?.some(x=>x.type==='SC_ATTACH_SERVER_SKILL'))")
        row = evaluate("JSON.stringify(window.gameData.events.find(x=>x.type==='SC_ATTACH_SERVER_SKILL'))")
        row = json.loads(row)
        assert row['payload']['skills'][0]['skill_inst_id'] == '9223372036854775917'
        assert row['payload']['skills'][0]['is_enable'] is False
        assert row['payload']['skills'][0]['blackboard']['blackboard']['zero']['float_value'] == 0
        assert row['payload']['skills'][0]['blackboard']['blackboard']['zero']['is_dynamic'] is False
        assert 'raw_body_hex' not in row['payload']
        # The existing SDK must deliver this new topic through its real isolated
        # transport and clear the consumer's retained data when capture ends.
        broker.on_state_change('waiting_connection', None)
        wait_for("window.lastState==='unavailable'&&window.gameData===null")
        print('GAME_DATA_BRIDGE_OK')
    finally:
        manager.close()
        broker.close()
        for _ in range(8):
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == '__main__' and '--runtime' in sys.argv:
    _runtime(sys.argv[-1])
