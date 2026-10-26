"""Explicit packet-identity fixtures, never nearest-cast combo recovery."""
from copy import deepcopy
from itertools import permutations
import hashlib
import json
from pathlib import Path

import pytest

from parser_core import battle_log_parser as parser


TRIGGER = 'buff_common_affixes_combo_trigger'
IMBUE = 'buff_common_affixes_skillimbue'
MODIFIER = 'buff_common_affixes_skillimbue_atk'
SESSION = 'synthetic-combo-session'


@pytest.mark.parametrize('kind,expected,rate', [
    ('ultimate', {491, 502, 532, 554, 576}, 0.2),
    ('skill', {1107, 1119, 1138}, 0.3),
])
def test_live_v49_combo_capture_preserves_source_scope_and_each_hit(kind, expected, rate):
    fixtures = Path(__file__).with_name('fixtures')
    manifest = json.loads((fixtures / 'combo_live_20260914.json').read_text(encoding='utf8'))
    case = next(row for row in manifest['cases'] if row['kind'] == kind)
    path = fixtures / case['file']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case['sha256']
    parsed = parser.parse_raw_battle_log_text(path.read_text(encoding='utf8'), include_rdps_debug=True)
    actual = set()
    for hit in parsed['debug_hits']:
        assert not hit.get('combo_unresolved_reasons')
        assert sum(row['value'] for row in hit['rdps_contributions']) == pytest.approx(hit['hit_value'])
        combo = [row for row in hit['zones'] if row['zone'] == 'combo']
        if hit['packet_seq_id'] not in expected:
            assert combo == []  # Captured normal attack/burning events are excluded.
            continue
        actual.add(hit['packet_seq_id'])
        assert len(combo) == 1
        assert combo[0]['external_rate'] == pytest.approx(rate)
        contributors = [row for row in combo[0]['contributors'] if row['scope'] == 'external']
        assert len(contributors) == 1
        assert contributors[0]['source_character_key'] == 'chr_0019_karin'
        assert contributors[0]['rdps_whitelist_keys'] == ['buff_common_affixes_combo_trigger::']
        assert hit['character_key'] == 'chr_0006_wolfgd'
    assert actual == expected


def combo_chain(*, rate=0.2, source='chr_0019_karin', source_actor='100', first_uid=10001):
    records = []
    for position, template in enumerate((TRIGGER, IMBUE, MODIFIER)):
        uid = str(first_uid + position)
        record = {'uid': uid, 'event_key': template, 'raw_event_key': template,
                  'source_character_key': source, 'source_actor_id': source_actor,
                  'target_character_key': 'chr_0004_pelica', 'target_actor_id': '200',
                  'ts_ms': 1000 + position * 100, 'line_no': 10 + position * 10,
                  'end_ts_ms': 2000, 'end_line_no': 200,
                  'bb_values': {'imbue_scale': rate}, 'origin_observations': [],
                  'creation_observations': [], 'enable_observations': []}
        if position:
            action = 4 if position == 1 else 0
            record['origin_observations'] = [{
                '_boundCreateRecord': True, 'schemaVersion': 1,
                'buffUid': uid, 'buffTemplate': template,
                'sourceActorId': source_actor, 'targetActorId': '200',
                'environmentType': 'Buff', 'ownerType': '1',
                'environmentInstId': str(first_uid + position - 1),
                'templateStrId': (TRIGGER, IMBUE)[position - 1], 'templateIntId': None,
                'actionId': action, 'operationSeq': 10 + position, 'triggerType': 0}]
            record['creation_observations'] = [{
                'schemaVersion': 1, 'buffUid': uid, 'buffTemplate': template,
                'sourceActorId': source_actor, 'targetActorId': '200',
                'sessionId': SESSION, 'operationSeq': 10 + position,
                'sourceEvent': 'BattleOpTriggerAction',
                'ts_ms': record['ts_ms'], 'line_no': record['line_no'] + 2,
                'assignedItems': [{'target_key': 'imbue_scale', 'use_direct_value': True,
                                   'numeric_value': rate, 'string_value': ''}]}]
        records.append(record)
    # Consumption ends the trigger, but its exact creation origin remains
    # valid at the earlier point that created the imbue instance.
    records[0]['end_ts_ms'] = 1150
    records[0]['end_line_no'] = 28
    return records


def combo_hit(*, mask=0x100, seq=20, line=50, uid='10003', value=130):
    return {'character_key': 'chr_0004_pelica', 'source_actor_id': '200',
            'target_enemy_key': 'eny_0051_rodin', 'target_actor_id': '300',
            'hit_value': value, 'ts_ms': 1500, 'line_no': line,
            'damage_element': 'pulse', 'damage_school': 'spell',
            'damage_decorate_mask': mask, 'packet_session_id': SESSION, 'packet_seq_id': seq,
            'damage_unit_index': 0, 'packet_modifier_seen': True,
            'packet_modifier_uids': {'attacker': [uid], 'defender': []},
            'packet_modifier_handles': {'attacker': [{'buff_inst_id': uid, 'index': 0, 'processor_debug_args': {}}], 'defender': []}}


@pytest.mark.parametrize('mask,damage,credit', [(0x100, 130, 30), (0x200, 120, 20), (0x300, 130, 30)])
def test_exact_combo_chain_allocates_only_packet_carried_rate(mask, damage, credit):
    hit = combo_hit(mask=mask, value=damage)
    windows = parser._build_special_combo_windows([hit], combo_chain(), battle_end_ms=2000)
    assert len(windows) == 1
    window = windows[0]
    assert window['combo_source_uids'] == ['10001', '10002', '10003']
    assert window['combo_rate_model'] == 'exact_packet_modifier_and_creation_chain'
    assert window['zone_effects'][0]['rdps_whitelist_keys'] == [TRIGGER + '::']
    assert window['zone_effects'][0]['rate'] == pytest.approx(credit / 100)
    result = parser._allocate_rdps_for_hit(hit, windows, include_debug=True)
    assert result['contributions_map'] == pytest.approx({'chr_0004_pelica': 100, 'chr_0019_karin': credit})


@pytest.mark.parametrize('layers,final_scale', [(2, .3), (3, .4), (4, .5)])
@pytest.mark.parametrize('mask,factor', [(0x100, 1.5), (0x200, 1.0)])
def test_multilayer_final_rate_is_not_enhanced_again_and_explicit_sources_stay_distinct(layers, final_scale, mask, factor):
    # Layer count labels the scenario. Only the forwarded packet value is
    # consumed by production, never the number of nearby Buffs.
    damage = 100 * (1 + final_scale * factor)
    hit = combo_hit(mask=mask, value=damage)
    windows = parser._build_special_combo_windows([hit], combo_chain(rate=final_scale), battle_end_ms=2000)
    result = parser._allocate_rdps_for_hit(hit, windows)
    assert result['contributions_map'] == pytest.approx({'chr_0004_pelica': 100, 'chr_0019_karin': 100*final_scale*factor})
    records, handles, expected = [], [], {'chr_0004_pelica': 100}
    for index, rate in enumerate([.2] + [.1] * (layers - 1)):
        provider, actor = (('chr_0019_karin', '100') if index % 2 == 0 else ('chr_0015_lifeng', '101'))
        first_uid = (index + 1) * 10000 + 1
        records += combo_chain(rate=rate, source=provider, source_actor=actor, first_uid=first_uid)
        handles.append({'buff_inst_id': str(first_uid + 2), 'index': 0, 'processor_debug_args': {}})
        expected[provider] = expected.get(provider, 0) + 100 * rate * factor
    for order in permutations(handles):
        hit = combo_hit(mask=mask, value=damage)
        hit['packet_modifier_handles']['attacker'] = list(order)
        hit['packet_modifier_uids']['attacker'] = [row['buff_inst_id'] for row in order]
        windows = parser._build_special_combo_windows([hit], deepcopy(records), battle_end_ms=2000)
        result = parser._allocate_rdps_for_hit(hit, windows)
        assert result['contributions_map'] == pytest.approx(expected)
        assert sum(result['contributions_map'].values()) == pytest.approx(damage)


@pytest.mark.parametrize('static_mask,allowed', [(0x100, True), (0x200, True), (0x80, False)])
def test_known_static_category_is_used_without_fabricating_runtime_mask(static_mask, allowed):
    hit = combo_hit(mask=None)
    hit.update(static_damage_decorate_mask=static_mask,
               static_damage_decorate_mask_source='current_typed_skill_or_buff_exact_unit_generation_only')
    windows = parser._build_special_combo_windows([hit], combo_chain(), battle_end_ms=2000)
    assert bool(windows) is allowed
    assert hit['damage_decorate_mask'] is None
    if allowed:
        assert windows[0]['combo_skill_scope_source'] == hit['static_damage_decorate_mask_source']


def test_observed_non_skill_mask_overrides_positive_static_category():
    hit = combo_hit(mask=0x80)
    hit.update(static_damage_decorate_mask=0x100,
               static_damage_decorate_mask_source='current_typed_skill_or_buff_exact_unit_generation_only')
    assert parser._build_special_combo_windows([hit], combo_chain(), battle_end_ms=2000) == []


def test_single_hit_projection_does_not_spill_to_adjacent_same_millisecond_hit():
    first = combo_hit()
    second = combo_hit(seq=21, line=51, value=100)
    second['packet_modifier_handles']['attacker'] = []
    second['packet_modifier_uids']['attacker'] = []
    windows = parser._build_special_combo_windows([first, second], combo_chain(), battle_end_ms=2000)
    assert len(windows) == 1
    assert parser._allocate_rdps_for_hit(first, windows)['contributions_map'] == pytest.approx(
        {'chr_0004_pelica': 100, 'chr_0019_karin': 30})
    assert parser._allocate_rdps_for_hit(second, windows)['contributions_map'] == {'chr_0004_pelica': 100}


def test_same_modifier_can_apply_to_each_explicitly_reported_hit_without_name_matching():
    hits = [combo_hit(seq=20, line=50), combo_hit(seq=21, line=51)]
    hits[0]['skill_key'] = 'unrelated_display_label'
    hits[1]['skill_key'] = 'another_unrelated_label'
    windows = parser._build_special_combo_windows(hits, combo_chain(), battle_end_ms=2000)
    assert len(windows) == 2
    for hit in hits:
        assert parser._allocate_rdps_for_hit(hit, windows)['contributions_map'] == pytest.approx(
            {'chr_0004_pelica': 100, 'chr_0019_karin': 30})


def test_duplicate_handle_is_one_effect_and_distinct_packet_providers_both_apply():
    hit = combo_hit(value=145)
    hit['packet_modifier_handles']['attacker'].append(deepcopy(hit['packet_modifier_handles']['attacker'][0]))
    hit['packet_modifier_handles']['attacker'].append({'buff_inst_id': '20003', 'index': 0, 'processor_debug_args': {}})
    hit['packet_modifier_uids']['attacker'].append('20003')
    records = combo_chain() + combo_chain(rate=0.1, source='chr_0015_lifeng', source_actor='101', first_uid=20001)
    windows = parser._build_special_combo_windows([hit], records, battle_end_ms=2000)
    assert len(windows) == 2
    result = parser._allocate_rdps_for_hit(hit, windows)['contributions_map']
    assert result == pytest.approx({'chr_0004_pelica': 100, 'chr_0019_karin': 30, 'chr_0015_lifeng': 15})


@pytest.mark.parametrize('change', [
    'missing_final_origin', 'missing_parent_origin', 'wrong_parent_action', 'wrong_parent_template',
    'conflicting_origins', 'missing_final_anchor', 'foreign_session', 'conflicting_rate',
    'forward_rate_disagreement', 'wrong_provider', 'wrong_recipient', 'ambiguous_instance',
    'wrong_modifier_index', 'missing_modifier_index', 'boolean_modifier_index',
    'unbound_origin', 'negative_rate', 'nan_rate', 'missing_hit_line', 'unknown_static_mask',
    'disabled_modifier', 'conflicting_enable_state',
])
def test_incomplete_or_conflicting_combo_chain_never_guesses(change):
    records = combo_chain()
    hit = combo_hit()
    final = records[2]
    if change == 'missing_final_origin':
        final['origin_observations'] = []
    elif change == 'missing_parent_origin':
        records[1]['origin_observations'] = []
    elif change == 'wrong_parent_action':
        final['origin_observations'][0]['actionId'] = 1
    elif change == 'wrong_parent_template':
        final['origin_observations'][0]['templateStrId'] = TRIGGER
    elif change == 'conflicting_origins':
        final['origin_observations'].append({**final['origin_observations'][0], 'environmentInstId': '29999'})
    elif change == 'missing_final_anchor':
        final['creation_observations'] = []
    elif change == 'foreign_session':
        final['creation_observations'][0]['sessionId'] = 'other-session'
    elif change == 'conflicting_rate':
        final['creation_observations'][0]['assignedItems'].append({'target_key': 'imbue_scale', 'use_direct_value': True, 'numeric_value': 0.3})
    elif change == 'forward_rate_disagreement':
        final['creation_observations'][0]['assignedItems'][0]['numeric_value'] = 0.3
    elif change == 'wrong_provider':
        records[0]['source_actor_id'] = '999'
    elif change == 'wrong_recipient':
        hit['source_actor_id'] = '201'
    elif change == 'ambiguous_instance':
        records.append(deepcopy(final))
    elif change == 'wrong_modifier_index':
        hit['packet_modifier_handles']['attacker'][0]['index'] = 1
    elif change == 'missing_modifier_index':
        hit['packet_modifier_handles']['attacker'][0].pop('index')
    elif change == 'boolean_modifier_index':
        hit['packet_modifier_handles']['attacker'][0]['index'] = True
    elif change == 'unbound_origin':
        final['origin_observations'][0]['_boundCreateRecord'] = False
    elif change in {'negative_rate', 'nan_rate'}:
        final['creation_observations'][0]['assignedItems'][0]['numeric_value'] = -0.2 if change == 'negative_rate' else float('nan')
    elif change == 'missing_hit_line':
        hit.pop('line_no')
    elif change == 'unknown_static_mask':
        hit.update(damage_decorate_mask=None, static_damage_decorate_mask=0x100,
                   static_damage_decorate_mask_source='name_based_fixture')
    elif change == 'disabled_modifier':
        final['enable_observations'] = [{'operationSeq': 15, 'enabled': False}]
    elif change == 'conflicting_enable_state':
        final['enable_observations'] = [{'operationSeq': 15, 'enabled': False}, {'operationSeq': 15, 'enabled': True}]
    assert parser._build_special_combo_windows([hit], records, battle_end_ms=2000) == []


def test_removed_whitelist_projection_cannot_transfer_combo_credit(monkeypatch):
    registry = deepcopy(parser._load_rdps_semantics_registry())
    registry['verified_prefixes'] = []
    monkeypatch.setattr(parser, '_load_rdps_semantics_registry', lambda: registry)
    assert parser._build_special_combo_windows([combo_hit()], combo_chain(), battle_end_ms=2000) == []


@pytest.mark.parametrize('modifier_index,credit', [(0, 30), (1, 0)])
def test_wire_service_bridge_parser_preserve_complete_combo_chain(tmp_path, modifier_index, credit):
    # Current real protobuf schemas with explicitly synthetic game events.
    # The skill template/action/unit identify an existing official NormalSkill
    # damage unit; no skill-name classifier or injected runtime mask is used.
    service = pytest.importorskip('endfield_pcap.service')
    bridge_module = pytest.importorskip('endfield_pcap.trace_bridge')
    proto = pytest.importorskip('endfield_pcap.proto_generated.CS_BATTLE_OP_pb2')
    packet = proto.CS_BATTLE_OP()
    op = packet.client_data.op_list.add(op_type=1, seq_id=10, owner_id=200, generation=1, client_tick_tms=1000)
    added = op.add_buff_op_data
    added.buff_id.type = 1
    added.buff_id.int_id = 1067
    added.buff_inst_id = 10001
    added.src_inst_id = 100
    added.target_inst_id = 200
    for seq, parent_id, parent_uid, action_id, child_id, child_uid in [
        (11, 1067, 10001, 4, 997, 10002), (12, 997, 10002, 0, 1326, 10003),
    ]:
        op = packet.client_data.op_list.add(op_type=8, seq_id=seq, owner_id=200, generation=1, client_tick_tms=1000)
        trigger = op.trigger_action_op_data
        trigger.owner_id = 200
        trigger.owner_type = 1
        trigger.inst_id = parent_uid
        trigger.template_id.type = 1
        trigger.template_id.int_id = parent_id
        trigger.action.action_type = 2
        trigger.action.action_id = action_id
        detail = trigger.action.create_buff_action.details.add(
            source_id=100, target_id=200, buff_inst_id=child_uid, buff_num_id=child_id)
        detail.assigned_items.add(target_key='imbue_scale', use_direct_value=True, numeric_value=0.2)
    op = packet.client_data.op_list.add(op_type=8, seq_id=20, owner_id=200, generation=1, client_tick_tms=1000)
    trigger = op.trigger_action_op_data
    trigger.owner_id = 200
    trigger.owner_type = 3
    trigger.inst_id = 20001
    trigger.template_id.type = 0
    trigger.template_id.int_id = 4807
    trigger.action.action_type = 1
    # Current typed chr_0035_liino_normal_skill_projhit_02: action 3 / HP unit 0.
    trigger.action.action_id = 3
    damage = trigger.action.damage_action
    damage.attacker_id = 200
    damage.original_source_template_id.type = 0
    damage.original_source_template_id.int_id = 2959
    detail = damage.details.add(target_id=300, damage_unit_index=0, value=130.0, cur_hp=870.0)
    detail.modifier_args.attacker_id = 200
    detail.modifier_args.defender_id = 300
    detail.modifier_args.attacker_modifiers.add(buff_inst_id=10003, index=modifier_index)
    pipeline = service.SessionPipeline.__new__(service.SessionPipeline)
    pipeline.session_id = SESSION
    pipeline.skill_levels_by_battle_inst = {200: {pipeline._template_key('Skill', 4807, None): 1}}
    emitted = []
    pipeline._emit_event = emitted.append
    pipeline._emit_battle_events(proto.CS_BATTLE_OP.FromString(packet.SerializeToString()), 1000)
    path = tmp_path / 'synthetic_combo.log'
    bridge = bridge_module.TraceBridge(path)
    bridge.id_to_name.update({100: 'chr_0019_karin', 200: 'chr_0035_liino', 300: 'eny_0051_rodin'})
    for event in emitted:
        bridge.handle_event(event.as_dict())
    bridge._flush_pending_add_buffs()
    bridge._flush_pending_create_buff_triggers()
    text = path.read_text(encoding='utf-8')
    assert text.count(' BUFF_ORIGIN ') == 2
    assert text.count(' BUFF_CREATE_OBSERVED ') == 3
    assert text.count(' PKT_MOD_DETAIL ') == 1
    parsed = parser.parse_raw_battle_log_text(text, include_rdps_debug=True)
    assert len(parsed['debug_hits']) == 1
    hit = parsed['debug_hits'][0]
    assert hit['packet_modifier_handles']['attacker'][0]['index'] == modifier_index
    assert hit['damage_decorate_mask'] is None
    assert hit['static_damage_decorate_mask'] & 0x100
    assert hit['external_pool'] == pytest.approx(credit)
    assert hit['attacker_share'] == pytest.approx(130 - credit)
