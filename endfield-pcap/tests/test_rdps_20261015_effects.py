"""Current reviewed effects use actual instance fields, never level guesses."""
import json

import pytest

from endfield_pcap.trace_bridge import TraceBridge
from parser_core import battle_log_parser as parser


@pytest.mark.parametrize('key,weapon,operand,target,zone,elements', [
    ('buff_wpn_claym_0018_enemyvul', 'wpn_claym_0018', 'dmg_taken_up', 'enemy',
     'vuln_taken', {'all'}),
    ('buff_wpn_claym_0019_teamatk', 'wpn_claym_0019', 'spell_dmg_up', 'character',
     'dmg_inc', {'fire', 'pulse', 'cryst', 'natural'}),
])
@pytest.mark.parametrize('change', ['none', 'missing_weapon', 'wrong_weapon', 'missing_source',
                                   'missing_target', 'missing_uid', 'sentinel_uid', 'wrong_target_kind', 'missing_value'])
def test_new_weapon_credit_requires_instance_identity_loadout_and_packet_value(
        key, weapon, operand, target, zone, elements, change):
    source, attacker = 'chr_0036_jsspsi', 'chr_0004_pelica'
    record = {'uid': '7001', 'event_key': key, 'raw_event_key': key,
              'source_character_key': source, 'source_actor_id': '2001',
              'source_weapon_template': weapon, 'target_actor_id': '3001' if target == 'enemy' else '1001',
              'bb_values': {operand: 0.2, 'duration': 10}, 'bb_keys': [operand, 'duration']}
    record['target_enemy_key' if target == 'enemy' else 'target_character_key'] = (
        'eny_0051_rodin' if target == 'enemy' else attacker)
    if change == 'missing_weapon':
        record.pop('source_weapon_template')
    elif change == 'wrong_weapon':
        record['source_weapon_template'] = 'wpn_claym_0017'
    elif change == 'missing_source':
        record.pop('source_actor_id')
    elif change == 'missing_target':
        record.pop('target_actor_id')
    elif change == 'missing_uid':
        record.pop('uid')
    elif change == 'sentinel_uid':
        record['uid'] = '4294967295'
    elif change == 'wrong_target_kind':
        record['target_character_key' if target == 'enemy' else 'target_enemy_key'] = (
            attacker if target == 'enemy' else 'eny_0051_rodin')
    elif change == 'missing_value':
        record['bb_values'].pop(operand)
    effects = parser._collect_zone_effects(record)
    approved = [effect for effect in effects if effect.get('external_rdps_allowed') is True]
    if change == 'none':
        assert {(row['zone'], row['element'], row['rate']) for row in approved} == {
            (zone, element, 0.2) for element in elements}
        assert all(row['rdps_whitelist_keys'] == [key] for row in approved)
    else:
        assert not approved
    window = {**record, 'zone_effects': effects, 'start_ts_ms': 0, 'end_ts_ms': 1000,
              'target_character_key': record.get('target_enemy_key') or record.get('target_character_key'),
              'line_no': 1, 'enable_observations': [{'ts_ms': 0, 'line_no': 1, 'operationSeq': 1, 'enabled': True}]}
    hit = {'character_key': attacker, 'source_actor_id': '1001',
           'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '3001',
           'hit_value': 120.0, 'ts_ms': 500, 'damage_element': 'fire', 'damage_school': 'spell',
           'damage_decorate_mask': 0x100}
    allocation = parser._allocate_rdps_for_hit(hit, [window], include_debug=True)
    expected = {attacker: 100.0, source: 20.0} if change == 'none' else {attacker: 120.0}
    assert allocation['contributions_map'] == pytest.approx(expected)
    assert sum(allocation['contributions_map'].values()) == pytest.approx(120.0)
    # A disabled instance or expired UID cannot contribute even when typed and
    # loadout evidence is otherwise complete.
    if change == 'none':
        window['enable_observations'][0]['enabled'] = False
        assert parser._allocate_rdps_for_hit(hit, [window])['contributions_map'] == {attacker: 120.0}
        window['enable_observations'][0]['enabled'] = True
        hit['ts_ms'] = 1001
        assert parser._allocate_rdps_for_hit(hit, [window])['contributions_map'] == {attacker: 120.0}


@pytest.mark.parametrize('action,main,element', [
    (1, 'buff_common_affixes_enhance_fire', 'fire'),
    (2, 'buff_common_affixes_enhance_crystal', 'cryst'),
])
@pytest.mark.parametrize('change', ['none', 'missing_origin', 'wrong_action', 'disabled_physical',
                                   'wrong_source', 'wrong_uid', 'missing_rate'])
def test_jsspsi_ultimate_uses_exact_enabled_keyword_origin_and_final_rate(
        tmp_path, action, main, element, change):
    bridge = TraceBridge(tmp_path / 'jsspsi-keyword.log')
    bridge.id_to_name.update({100: 'chr_0036_jsspsi', 200: 'chr_0004_pelica'})
    if change == 'disabled_physical':
        action, main, element = 0, 'buff_common_affixes_enhance_physical', 'physical'
    assigned = [{'target_key': 'rate', 'numeric_value': 0.24},
                {'target_key': 'duration', 'numeric_value': 10}]
    if change == 'missing_rate':
        assigned.pop(0)
    bridge.handle_event({'type': 'BattleOpTriggerAction', 'timestamp_ms': 1000,
        'seq_id': 7, 'client_tick_tms': 1000, 'owner_id': 100, 'owner_type': '1',
        'inst_id': 9223372036854775900, 'trigger_type': 0, 'template_type': 'Buff',
        'template_str_id': 'buff_chr_0036_jsspsi_atk_buff_2',
        'action': {'action_id': action, 'create_buff_action': {'details': [{
            'buff_inst_id': 9223372036854775901, 'source_id': 100, 'target_id': 200,
            'buff_id': main, 'assigned_items': assigned}]}}})
    bridge._flush_pending_create_buff_triggers()
    bridge._line(2000, 'HP_V2 #1 hit=124 raw=124 packetFinalValue=124 cum=124 pHP=100 eHP=1000 skill="fixture" src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica atkId=200 tgtId=300 hits=1')
    lines = bridge.trace_file.read_text(encoding='utf-8').splitlines()
    index = next(i for i, line in enumerate(lines) if ' BUFF_ORIGIN ' in line)
    prefix, blob = lines[index].split(' BUFF_ORIGIN ', 1)
    origin = json.loads(blob)
    if change == 'missing_origin':
        lines.pop(index)
    elif change in ('wrong_action', 'wrong_source', 'wrong_uid'):
        origin[{'wrong_action': 'actionId', 'wrong_source': 'sourceActorId',
                'wrong_uid': 'buffUid'}[change]] = 999 if change == 'wrong_action' else '999'
        lines[index] = prefix + ' BUFF_ORIGIN ' + json.dumps(origin)
    result = parser.parse_raw_battle_log_text('\n'.join(lines), include_rdps_debug=True)
    record = next(row for row in result['buff_events'] if row['uid'] == '9223372036854775901')
    effects = parser._collect_zone_effects(record)
    approved = [effect for effect in effects if effect.get('external_rdps_allowed') is True]
    if change == 'none':
        assert {(row['zone'], row['element'], row['rate']) for row in approved} == {('amp', element, 0.24)}
        assert approved[0]['rdps_whitelist_keys'] == ['buff_chr_0036_jsspsi_atk_buff_2']
        # 0.24 is the captured final rate (including any potential3 bonus).
        # No second 1.2 multiplier and no other element is authorized.
    else:
        assert not approved


@pytest.mark.parametrize('weapon,buff,operand,target,target_actor', [
    ('wpn_claym_0018', 'buff_wpn_claym_0018_enemyvul', 'dmg_taken_up', 'eny_0051_rodin', 3001),
    ('wpn_claym_0019', 'buff_wpn_claym_0019_teamatk', 'spell_dmg_up', 'chr_0004_pelica', 1001),
])
@pytest.mark.parametrize('with_loadout', [True, False])
def test_new_weapon_protocol_fields_flow_to_real_allocation(tmp_path, weapon, buff, operand,
                                                           target, target_actor, with_loadout):
    bridge = TraceBridge(tmp_path / 'weapon-exact-packet-fields.log')
    if with_loadout:
        bridge._line(900, f'LOADOUT slot=0 char=chr_0036_jsspsi weaponTemplate={weapon} weaponLv=90 refine=9 break=4 attachedGem=0 equipInsts={{}} equips={{}} equipSuit={{}}')
    bridge._line(1000, f'BUFF_START #1 id="{buff}" uid=9223372036854775901 owner={target} src=chr_0036_jsspsi ownerId={target_actor} srcId=2001 dur=10')
    bridge._line(1000, f'BB[9223372036854775901]: {operand}=0.2')
    bridge.handle_event({'type': 'BattleOpSetBuffEnableState', 'timestamp_ms': 1050,
                         'owner_id': str(target_actor), 'seq_id': 10, 'client_tick_tms': 1050,
                         'generation': 4, 'states': {'9223372036854775901': True}})
    bridge._line(1200, 'HP_V2 #1 hit=120 raw=120 packetFinalValue=120 cum=120 pHP=100 eHP=1000 skill="chr_0004_pelica_combo_skill_projhit" actionId=11 damageUnitIndex=0 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica atkId=1001 tgtId=3001 hits=1')
    result = parser.parse_raw_battle_log_text(bridge.trace_file.read_text(encoding='utf-8'), include_rdps_debug=True)
    credit = sum(row['value'] for row in result['debug_hits'][0]['rdps_contributions']
                 if row['character_key'] == 'chr_0036_jsspsi')
    assert credit == pytest.approx(20.0 if with_loadout else 0.0)


def test_jsspsi_carrier_cannot_be_used_as_an_independent_rate():
    record = {'uid': '7001', 'event_key': 'buff_chr_0036_jsspsi_atk_buff_2',
              'source_character_key': 'chr_0036_jsspsi', 'source_actor_id': '100',
              'target_character_key': 'chr_0004_pelica', 'target_actor_id': '200',
              'bb_values': {'enhance_final': 0.24, 'heal_value': 10.0, 'duration': 10}}
    assert parser._collect_zone_effects(record) == []
    entry = parser._rdps_registry_verified_effect_entry(record)
    assert entry['_rdps_lifecycle_only'] is True
    assert parser._rdps_registry_validation_error(entry, record) is None


@pytest.mark.parametrize('initial_weapon', [None, 'wpn_claym_0018'])
def test_weapon_guard_retains_creation_snapshot_across_later_loadout(initial_weapon):
    line = '[10:00:00.000] LOADOUT slot=0 char=chr_0036_jsspsi weaponTemplate={} weaponLv=90 refine=9 break=4 attachedGem=0 equipInsts={{}} equips={{}} equipSuit={{}}'
    buff = '[10:00:00.100] BUFF_START #1 id="buff_wpn_claym_0018_enemyvul" uid=7001 owner=eny_0051_rodin src=chr_0036_jsspsi ownerId=3001 srcId=1001 dur=10'
    lines = [line.format(initial_weapon)] if initial_weapon else []
    lines += [buff, '[10:00:00.100] BB[7001]: dmg_taken_up=0.2', line.format('wpn_claym_0019'),
              buff.replace('00.100', '00.200'),
              '[10:00:00.300] HP_V2 #1 hit=120 raw=120 packetFinalValue=120 cum=120 pHP=100 eHP=1000 skill="fixture" src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica atkId=2001 tgtId=3001 hits=1']
    result = parser.parse_raw_battle_log_text('\n'.join(lines))
    records = [row for row in result['buff_events'] if row['uid'] == '7001']
    assert len(records) == 2
    assert [row.get('source_weapon_template') for row in records] == [initial_weapon, initial_weapon]


def test_regular_parser_version_advances_without_changing_mathematical_rules():
    assert parser.PARSER_VERSION == 'raw-log-parser-v59'
    assert parser.RULES_VERSION == 'raw-log-parser-v44'
    units = parser._load_skill_action_damage_units('chr_0004_pelica_combo_skill_projhit')
    proof = units[11][0]['damage_calculation']['sourceProof']
    assert {key: proof[key] for key in parser._CURRENT_TYPED_RESOURCE_BINDING} == parser._CURRENT_TYPED_RESOURCE_BINDING
    assert proof['scope'] == 'typed_layout_and_exact_resource_only'
