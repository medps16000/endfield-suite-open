import json
from pathlib import Path

import pytest

from endfield_pcap.trace_bridge import TraceBridge
from parser_core import battle_log_parser as parser


@pytest.mark.parametrize('change,allowed', [
    ('none', True), ('duplicate', True), ('missing', False), ('wrong_action', False),
    ('wrong_parent', False), ('wrong_source', False), ('wrong_child', False), ('conflicting', False),
    ('boolean_action', False), ('duplicate_keys', False),
    ('invalid_parent_uid', False),
])
def test_packet_create_origin_controls_keyword_whitelist_projection(tmp_path, change, allowed):
    path = tmp_path / 'trace.log'
    bridge = TraceBridge(path)
    bridge.id_to_name.update({100: 'chr_0026_lastrite', 200: 'eny_0051_rodin'})
    child = 'buff_common_affixes_vulnerable_crystal'
    if change == 'wrong_child':
        child = 'buff_common_affixes_vulnerable_natural'
    bridge.handle_event({'type': 'BattleOpTriggerAction', 'timestamp_ms': 1000,
        'seq_id': 7, 'client_tick_tms': 1000, 'owner_id': 100, 'owner_type': '1',
        'inst_id': 9223372036854775900, 'trigger_type': 0, 'template_type': 'Buff',
        'template_str_id': 'buff_chr_0026_lastrite_talent_1_vul',
        'action': {'action_id': 1, 'create_buff_action': {'details': [{
            'buff_inst_id': 9223372036854775901, 'source_id': 100, 'target_id': 200,
            'buff_id': child, 'assigned_items': [
                {'target_key': 'rate', 'numeric_value': 0.2},
                {'target_key': 'duration', 'numeric_value': 10},
            ]}]}}})
    bridge._flush_pending_create_buff_triggers()
    bridge._line(2000, 'HP_V2 #1 hit=120 cum=120 raw=120.0 packetFinalValue=120 pHP=100 eHP=1000 skill="fixture" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica atkId=300 tgtId=200 seg=0 critFlag=0')
    lines = path.read_text(encoding='utf-8').splitlines()
    index = next(i for i, line in enumerate(lines) if ' BUFF_ORIGIN ' in line)
    prefix, blob = lines[index].split(' BUFF_ORIGIN ', 1)
    origin = json.loads(blob)
    assert origin['buffUid'] == '9223372036854775901'
    if change == 'missing':
        lines.pop(index)
    elif change == 'duplicate':
        lines.insert(index + 1, lines[index])
    elif change == 'duplicate_keys':
        lines[index] = prefix + ' BUFF_ORIGIN ' + blob[:-1] + ',"actionId":1}'
    elif change in ('wrong_action', 'wrong_parent', 'wrong_source', 'conflicting', 'boolean_action', 'invalid_parent_uid'):
        if change in ('wrong_action', 'conflicting'):
            origin['actionId'] = 999
        elif change == 'wrong_parent':
            origin['templateStrId'] = 'buff_wpn_sword_0016_valid'
        elif change == 'boolean_action':
            origin['actionId'] = True
        elif change == 'invalid_parent_uid':
            origin['environmentInstId'] = '4294967295'
        else:
            origin['sourceActorId'] = '101'
        replacement = prefix + ' BUFF_ORIGIN ' + json.dumps(origin)
        if change == 'conflicting':
            lines.insert(index + 1, replacement)
        else:
            lines[index] = replacement
    parsed = parser.parse_raw_battle_log_text('\n'.join(lines), include_rdps_debug=True)
    record = next(row for row in parsed['buff_events'] if row['uid'] == '9223372036854775901')
    effects = parser._collect_zone_effects(record)
    window = {**record, 'zone_effects': effects}
    hit = {'character_key': 'chr_0004_pelica', 'target_enemy_key': 'eny_0051_rodin',
           'target_actor_id': '200', 'hit_value': 120, 'ts_ms': record['start_ts_ms'] + 1000,
           'damage_element': 'cryst' if change != 'wrong_child' else 'natural', 'damage_school': 'spell'}
    allocation = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    if allowed:
        assert effects[0]['rdps_whitelist_keys'] == ['buff_chr_0026_lastrite_talent_1_vul']
        assert allocation['contributions_map'] == pytest.approx({'chr_0004_pelica': 100, 'chr_0026_lastrite': 20})
    else:
        assert allocation['contributions_map'] == {'chr_0004_pelica': 120}


def test_native_keyword_mapping_is_version_bound_and_exact():
    root = Path(__file__).resolve().parents[1]
    mapping = json.loads((root / 'data/local_static/buff/keyword_buff_map.json').read_text(encoding='utf-8'))
    assert mapping['decoder'] == parser._CURRENT_KEYWORD_BINDING
    assert mapping['decoder'] != parser._NATIVE_DAMAGE_SEMANTICS_BINDING
    assert len(mapping['literalProof']) == 14
    assert mapping['mapping']['Vulnerable']['Crystal'] == 'buff_common_affixes_vulnerable_crystal'
    assert mapping['mapping']['Enhanced']['Crystal'] == 'buff_common_affixes_enhance_crystal'


@pytest.mark.parametrize('source,parent,action,main,approval,target', [
    ('chr_0011_seraph', 'buff_chr_0011_seraph_atk_buff', 6,
     'buff_common_affixes_enhance_crystal', 'buff_chr_0011_seraph_ultimate_effect', 'chr_0004_pelica'),
    ('chr_0032_lizhiyan', 'buff_chr_0032_lizhiyan_combo_skill_spell_vulnerable', 2,
     'buff_common_affixes_vulnerable_crystal', 'buff_common_affixes_vulnerable_crystal_lizhiyan_child', 'eny_0051_rodin'),
])
def test_bridge_create_origin_reaches_visual_child_whitelist_through_raw_parser(tmp_path, source, parent, action, main, approval, target):
    bridge = TraceBridge(tmp_path / 'visual-projection.log')
    bridge.id_to_name.update({100: source, 200: target})
    bridge.handle_event({'type': 'BattleOpTriggerAction', 'timestamp_ms': 1000,
        'seq_id': 7, 'client_tick_tms': 1000, 'owner_id': 100, 'owner_type': '1',
        'inst_id': 9223372036854775900, 'trigger_type': 0, 'template_type': 'Buff',
        'template_str_id': parent, 'action': {'action_id': action,
        'create_buff_action': {'details': [{'buff_inst_id': 9223372036854775901,
            'source_id': 100, 'target_id': 200, 'buff_id': main,
            'assigned_items': [{'target_key': 'rate', 'numeric_value': 0.37},
                               {'target_key': 'duration', 'numeric_value': 10}]}]}}})
    bridge._flush_pending_create_buff_triggers()
    attacker_id, defender_id = (200, 300) if target == 'chr_0004_pelica' else (300, 200)
    bridge._line(2000, f'HP_V2 #1 hit=137 cum=137 raw=137 packetFinalValue=137 pHP=100 eHP=1000 skill="fixture" hits=1 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica atkId={attacker_id} tgtId={defender_id} seg=0 critFlag=0')
    text = bridge.trace_file.read_text(encoding='utf8')
    result = parser.parse_raw_battle_log_text(text, include_rdps_debug=True)
    record = next(row for row in result['buff_events'] if row['uid'] == '9223372036854775901')
    effects = record['zone_effects']
    assert effects and all(row['rdps_whitelist_keys'] == [approval] for row in effects)
    assert all(row['rate'] == 0.37 and row['external_rdps_allowed'] is True for row in effects)
    assert record['origin_observations'][0]['_boundCreateRecord'] is True
    without_origin = '\n'.join(line for line in text.splitlines() if ' BUFF_ORIGIN ' not in line)
    missing = parser.parse_raw_battle_log_text(without_origin, include_rdps_debug=True)
    unresolved = next(row for row in missing['buff_events'] if row['uid'] == '9223372036854775901')
    assert unresolved['zone_effects']
    assert all(row['external_rdps_allowed'] is False for row in unresolved['zone_effects'])


@pytest.mark.parametrize('mutation', ['none', 'version', 'missing_subtype', 'wrong_type'])
def test_keyword_mapping_rejects_wrong_version_or_partial_groups(tmp_path, monkeypatch, mutation):
    root = Path(__file__).resolve().parents[1]
    mapping = json.loads((root / 'data/local_static/buff/keyword_buff_map.json').read_text(encoding='utf-8'))
    if mutation == 'version':
        mapping['decoder']['gameAssemblySha256'] = '0' * 64
    elif mutation == 'missing_subtype':
        del mapping['mapping']['Vulnerable']['Crystal']
    elif mutation == 'wrong_type':
        mapping['mapping']['Vulnerable'] = 'invalid'
    target = tmp_path / 'data/local_static/buff/keyword_buff_map.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(mapping), encoding='utf-8')
    registry = tmp_path / 'data/packet_semantics/rdps_semantics_registry.json'
    registry.parent.mkdir(parents=True)
    registry.write_text('{"verified_effects": {}}', encoding='utf-8')
    monkeypatch.setattr(parser, '_repo_root', lambda: tmp_path)
    parser._load_rdps_semantics_registry.cache_clear()
    try:
        result = parser._load_rdps_semantics_registry()
        assert bool(result.get('_keyword_buff_map')) is (mutation == 'none')
    finally:
        parser._load_rdps_semantics_registry.cache_clear()
