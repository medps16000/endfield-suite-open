import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from endfield_pcap.packet_resolver import PacketResolveContext, PacketResolver
from endfield_pcap.trace_bridge import TraceBridge
from endfield_pcap import rdps_audit
from endfield_pcap.loadout_static import infer_weapon_refine_from_source_skills
from endfield_pcap.service import SessionPipeline
from endfield_pcap.proto_generated.BATTLE_ACTION_INST_pb2 import BATTLE_ACTION_INST
from endfield_pcap.proto_generated.BATTLE_TRIGGER_ACTION_OP_DATA_pb2 import BATTLE_TRIGGER_ACTION_OP_DATA
from parser_core import battle_log_parser as parser


def test_char_info_reads_current_equipment_field_six_without_losing_uint64_ids():
    from endfield_pcap.proto_generated.CHAR_INFO_pb2 import CHAR_INFO
    from google.protobuf.unknown_fields import UnknownFieldSet
    # Independent wire fixture: field 6 (0x32), map<int32,uint64>, slots 0/3.
    # The instance ids exceed 2**53 and differ by one.
    raw = bytes.fromhex('320b0800108180808080808010320b0803108280808080808010')
    message = CHAR_INFO.FromString(raw)
    assert dict(message.equip_col) == {0: 9007199254740993, 3: 9007199254740994}
    assert not list(UnknownFieldSet(message))


def test_char_info_does_not_reinterpret_legacy_field_five_as_current_equipment():
    from endfield_pcap.proto_generated.CHAR_INFO_pb2 import CHAR_INFO
    from google.protobuf.unknown_fields import UnknownFieldSet
    message = CHAR_INFO.FromString(bytes.fromhex('2a0b0800108180808080808010'))
    assert not dict(message.equip_col)
    assert [field.field_number for field in UnknownFieldSet(message)] == [5]


@pytest.fixture
def resolver(tmp_path):
    table = tmp_path / 'data' / 'local_tables' / 'NumIdStrTable.json'
    table.parent.mkdir(parents=True)
    table.write_text(json.dumps({
        'skill_id': {'dic': {'7': 'chr_0004_pelica_explicit'}},
        'buff_id': {'dic': {'9': 'buff_explicit_child'}},
    }), encoding='utf-8')
    return PacketResolver(tmp_path)


def test_numeric_identity_does_not_use_bundle_guesses(resolver):
    resolver.skill_by_numeric_unique = {'7': 'stale_guess', '8': 'another_guess'}
    resolver.buff_by_numeric = {'9': {'canonical_buff_id': 'stale_buff'}, '10': {'canonical_buff_id': 'guessed_buff'}}
    assert resolver.resolve_skill(int_id=7) == 'chr_0004_pelica_explicit'
    assert resolver.resolve_skill(int_id=8) is None
    assert resolver.resolve_buff(int_id=9, context=PacketResolveContext()) == 'buff_explicit_child'
    assert resolver.resolve_buff(int_id=10, context=PacketResolveContext()) == '10'


@pytest.mark.parametrize('value', [None, True, False, -1, 7.5, '7.5', 'unknown'])
def test_invalid_numeric_template_does_not_round_or_default(resolver, value):
    assert resolver.resolve_skill(int_id=value) is None
    assert resolver.resolve_buff(int_id=value) == 'unknown_buff'


def test_unique_blackboard_signature_is_not_template_identity(resolver):
    resolver.semantic_indexes = {'blackboard_signature_index': {'buffs': {'atk_up': ['buff_guessed']}}}
    context = PacketResolveContext(blackboard_values={'atk_up': 0.2})
    assert resolver.resolve_buff(int_id=10, context=context) == '10'
    assert resolver._resolve_buff_from_semantic_indexes(context) is None


def test_parent_single_child_and_equal_keys_do_not_identify_an_action(resolver):
    resolver.semantic_indexes = {'parent_rules': {'buffs': {'buff_parent': {
        'created_buff_ids': ['buff_guess'],
        'assignment_patterns': [{'buff_id': 'buff_guess', 'assign_map': {'a': 'b'}}],
    }}}}
    assert resolver.resolve_created_buff(parent_type='buff', parent_canonical_id='buff_parent',
                                         assigned_items=[{'input_value_key': 'a', 'target_key': 'b'}]) is None


def test_bridge_preserves_explicit_ids_and_does_not_copy_parent(resolver, tmp_path):
    bridge = TraceBridge(tmp_path / 'trace.dat')
    bridge.resolver = resolver
    bridge.skill_id_by_inst[7] = 'wrong_instance_collision'
    assert bridge._resolve_skill_template(int_id=7) == 'chr_0004_pelica_explicit'
    event = {'template_type': 'buff', 'template_str_id': 'buff_parent', 'inst_id': 1}
    bridge.buff_id_by_uid[1] = 'buff_parent'
    assert bridge._resolve_created_buff_template(event, {}, 2) == 'unknown_buff'
    assert bridge._resolve_created_buff_template(event, {'buff_num_id': 9}, 2) == 'buff_explicit_child'
    bridge._current_enemy_hint = 'eny_0051_rodin'
    bridge.id_to_name[999] = 'eny_0000_unknown'
    assert bridge._name_for_damage_target(999, 'chr_0004_pelica') == 'eny_0000_unknown'
    assert bridge._name_for_damage_target(998, 'chr_0004_pelica') == 'id_998'


def test_bridge_preserves_zero_action_index_and_template_namespace(resolver, tmp_path):
    trace = tmp_path / 'damage.dat'
    bridge = TraceBridge(trace)
    bridge.resolver = resolver
    bridge.id_to_name.update({1001: 'chr_0004_pelica', 2001: 'eny_0051_rodin'})
    detail = {'value': 100, 'cur_hp': 1000, 'target_id': 2001, 'damage_unit_index': 0}
    event = {'template_type': 'buff', 'template_int_id': 9, 'owner_id': 1001,
             'action': {'action_id': 0}}
    bridge._write_damage(100, event, {'attacker_id': 1001, 'details': [detail]})
    text = trace.read_text(encoding='utf-8')
    assert 'skill="buff_explicit_child"' in text
    assert 'templateType=buff actionId=0' in text
    assert 'damageUnitIndex=0' in text
    assert 'source=damage_skill' not in text


def test_bridge_preserves_explicit_zero_event_timestamp(tmp_path):
    bridge = TraceBridge(tmp_path / 'timestamp.dat')
    bridge.handle_event({'type': 'UNMODELED_EVENT', 'timestamp_ms': 0})
    assert bridge._last_ts_ms == 0


@pytest.mark.parametrize('action_id,unit_index', [(0, 0), (7, 2)])
def test_live_action_serialization_preserves_protobuf_implicit_defaults(action_id, unit_index):
    action = BATTLE_ACTION_INST(action_id=action_id)
    detail = action.damage_action.details.add(target_id=2001, value=-100, cur_hp=900,
                                             damage_unit_index=unit_index)
    assert action.DESCRIPTOR.fields_by_name['action_id'].has_presence is False
    assert detail.DESCRIPTOR.fields_by_name['damage_unit_index'].has_presence is False
    assert action.WhichOneof('inst_impl') == 'damage_action'
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.session_id = 'fixture'
    events = []
    pipeline._emit_event = events.append
    op = SimpleNamespace(trigger_action_op_data=SimpleNamespace(action=action))
    pipeline._emit_trigger_action_event(op, seq_id=1, client_tick_tms=0, timestamp_ms=0)
    result = events[0].payload['action']
    assert result['action_id'] == action_id
    assert result['damage_action']['details'][0]['damage_unit_index'] == unit_index
    assert result['damage_action']['details'][0]['is_crit'] is False
    # Message/oneof absence has distinct protocol semantics and stays absent.
    assert 'heal_action' not in result
    assert 'calculation_check_args' not in result['damage_action']['details'][0]


def test_skill_level_export_requires_one_exact_owner_relation():
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.skill_levels_by_battle_inst = {100: {"skill:int:2228": 9}}
    pipeline.global_skill_levels = {"skill:int:2228": 4}
    action = SimpleNamespace(damage_action=SimpleNamespace(attacker_id=100))
    trigger = SimpleNamespace(owner_id=100)
    op = SimpleNamespace(owner_id=100)
    assert pipeline._skill_level_for_trigger(op, trigger, "skill", 2228, None, action) == 9

    # Conflicting explicit owners are not resolved by recency or candidate order.
    conflict = SimpleNamespace(owner_id=101)
    assert pipeline._skill_level_for_trigger(op, conflict, "skill", 2228, None, action) is None
    missing = SimpleNamespace(owner_id=102)
    assert pipeline._skill_level_for_trigger(op, missing, "skill", 2228, None, action) is None


@pytest.mark.parametrize('phase', [0, 1, 2])
def test_live_action_serialization_preserves_outer_trigger_phase(phase):
    trigger = BATTLE_TRIGGER_ACTION_OP_DATA(trigger_type=phase)
    trigger.action.create_buff_action.details.add(buff_inst_id=77)
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.session_id = 'fixture'
    events = []
    pipeline._emit_event = events.append
    pipeline._emit_trigger_action_event(SimpleNamespace(trigger_action_op_data=trigger), 1, 0, 0)
    assert events[0].payload['trigger_type'] == phase


def test_create_buff_end_phase_finishes_exact_uid_without_recreating(resolver, tmp_path):
    path = tmp_path / 'phases.dat'
    bridge = TraceBridge(path)
    bridge.resolver = resolver
    detail = {'buff_inst_id': 77, 'source_id': 1001, 'target_id': 2001, 'buff_num_id': 9}
    event = {'type': 'BattleOpTriggerAction', 'timestamp_ms': 1000, 'inst_id': 10,
             'trigger_type': 0, 'action': {'action_type': 'BattleActionCreateBuff',
             'action_id': 0, 'create_buff_action': {'details': [detail]}}}
    bridge.handle_event(event)
    # A different parent between Execute/End must not reorder the two phases.
    bridge.handle_event(dict(event, inst_id=11, trigger_type=2, action={
        **event['action'], 'create_buff_action': {'details': [{'buff_inst_id': 77}]}}))
    bridge.handle_event(dict(event, action={**event['action'], 'create_buff_action': {
        'details': [dict(detail, buff_inst_id=88)]}}))
    bridge._flush_pending_create_buff_triggers()
    bridge._flush_pending_buff_starts()
    lines = path.read_text(encoding='utf8').splitlines()
    origins = [json.loads(line.split(' BUFF_ORIGIN ', 1)[1]) for line in lines if ' BUFF_ORIGIN ' in line]
    assert [origin['buffUid'] for origin in origins] == ['77', '88']
    assert all(origin['triggerType'] == 0 for origin in origins)
    lines = [line for line in lines if ' BUFF_START ' in line or ' BUFF_END ' in line]
    assert ['START' if 'BUFF_START' in line else 'END' for line in lines] == ['START', 'END', 'START']
    assert 'uid=77' in lines[1]
    assert 77 not in bridge.buff_id_by_uid
    assert bridge.buff_id_by_uid[88] == 'buff_explicit_child'


@pytest.mark.parametrize('phase', [None, 1, 99, '0', True])
def test_missing_or_unsupported_buff_phase_never_becomes_creation(resolver, tmp_path, phase):
    path = tmp_path / 'unknown-phase.dat'
    bridge = TraceBridge(path)
    bridge.resolver = resolver
    event = {'trigger_type': phase, 'seq_id': 12, 'action': {'action_id': 0}}
    bridge._write_create_buffs(1000, event, {'details': [{'buff_inst_id': 77, 'buff_num_id': 9}]})
    bridge._flush_pending_buff_starts()
    text = path.read_text(encoding='utf8')
    assert 'BUFF_START' not in text
    assert 'BUFF_END' not in text
    assert 'BUFF_ACTION_UNRESOLVED uid=77 seq=12' in text
    assert 77 not in bridge.buff_id_by_uid
    report = parser.parse_raw_battle_log_text(text)
    assert report['rdps_damage_basis']['rdps_strict_ok'] is False
    assert report['unresolved_buff_actions'][0]['reason'] == 'unresolved_buff_action_phase'


def test_bridge_does_not_fill_missing_action_or_actor_from_skill_name(resolver, tmp_path):
    trace = tmp_path / 'unknown.dat'
    bridge = TraceBridge(trace)
    bridge.resolver = resolver
    bridge._write_damage(100, {'template_type': 'skill', 'template_int_id': 7}, {
        'attacker_id': 999, 'details': [{'value': 100, 'target_id': 2001, 'cur_hp': 900}],
    })
    text = trace.read_text(encoding='utf-8')
    assert 'actionId=unknown' in text
    assert 'damageUnitIndex=unknown' in text
    assert 'src=id_999' in text
    assert 999 not in bridge.id_to_name


def test_buff_trace_keeps_actor_ids_and_does_not_fabricate_duration(tmp_path):
    path = tmp_path / 'buff.dat'
    bridge = TraceBridge(path)
    bridge._write_buff_start(0, 'buff_state', 10, 'eny_0051_rodin', 'chr_0004_pelica', {}, target_id=2001, src_id=1001)
    bridge._flush_pending_buff_starts()
    text = path.read_text(encoding='utf-8')
    assert text.startswith(datetime.fromtimestamp(0).strftime('[%H:%M:%S.000]'))
    assert 'ownerId=2001 srcId=1001' in text
    assert 'dur=unknown lifeT=unknown' in text
    bridge._handle_finish_buff_op({'buff_inst_id': 10}, 100)
    bridge._write_buff_start(200, 'buff_state', 10, 'eny_0051_rodin', 'chr_0004_pelica', {})
    bridge._flush_pending_buff_starts()
    assert 'ownerId=unknown srcId=unknown' in path.read_text(encoding='utf-8').splitlines()[-1]


def test_audit_fields_are_not_bound_to_log_order_or_numeric_uids():
    buff = 'BUFF_START #1 src=chr_0004_pelica ownerId=2001 id="buff_unknown" uid=runtime_a owner=eny_0051_rodin'
    assert rdps_audit._BUFF_START_RE.search(buff).group('uid') == 'runtime_a'
    assert rdps_audit._BB_RE.search('BB[runtime_a]: value=1').group('uid') == 'runtime_a'
    hit = 'HP_V2 #2 skill="known" tgtId=2001 atk=chr_0004_pelica hit=100 tgt=eny_0051_rodin src=chr_0004_pelica'
    assert rdps_audit._HP_RE.search(hit).groupdict() == {
        'seq': '2', 'skill': 'known', 'hit': '100', 'src': 'chr_0004_pelica',
        'atk': 'chr_0004_pelica', 'tgt': 'eny_0051_rodin',
    }


def test_missing_audit_proof_is_a_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(parser, 'parse_raw_battle_log_text', lambda *a, **k: {
        'battle': {}, 'participants': [], 'timeline_events': [], 'debug_hits': [],
    })
    audit = rdps_audit._per_hit_rdps_audit('fixture', root=tmp_path, expected_hit_count=0)
    assert audit['ok'] is False
    assert audit['rdps_proof']['packet_damage_basis_ok'] is False
    assert audit['rdps_trust_audit']['ok'] is False


def test_unavailable_parser_never_passes_trace_audit(monkeypatch, tmp_path):
    trace = tmp_path / 'trace.log'
    trace.write_text('[10:00:00.100] HP_V2 #1 hit=100 skill="chr_0004_pelica_attack1" '
                     'src=chr_0004_pelica tgt=eny_0051_rodin\n', encoding='utf-8')
    monkeypatch.setattr(rdps_audit, '_per_hit_rdps_audit', lambda *a, **k: {'available': False, 'ok': False})
    audit = rdps_audit.audit_trace(trace, root=Path(__file__).resolve().parents[1])
    assert audit['ok'] is False
    assert 'per_hit_rdps_parse' in audit['hard_blockers']


@pytest.mark.parametrize('infer', [infer_weapon_refine_from_source_skills, parser._infer_verified_weapon_refine_from_levels])
def test_weapon_potential_accepts_exact_numeric_id_and_rejects_conflict(infer):
    third = {'skill_int_id': 2217, 'level': 9}
    assert infer('wpn_funnel_0005', [third]) == 5
    assert infer('wpn_funnel_0005', [dict(third, skill_str_id='sk_wpn_funnel_0005')]) == 5
    assert infer('wpn_funnel_0005', [dict(third, skill_str_id='sk_other_weapon')]) is None
    assert infer('wpn_funnel_0005', [third, dict(third, level=1)]) is None
    assert infer('wpn_funnel_0005', [dict(third, level=4)]) is None
