"""Real packet subset plus explicit mutations of identity and refresh evidence."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from parser_core import battle_log_parser as parser
from parser_core.hit_debug import parse_hit_debug_log_text
from parser_core.unified import parse_hit_viewer_log_text


FIXTURE = Path(__file__).with_name('fixtures') / 'corrosion_packet_subset_20260911.log'
BUFF = 'buff_common_natural_natural_corrupt_do'
UID = '2305843009213697514'
ATTACKER = 'chr_0016_laevat'
PROVIDER = 'chr_0025_ardelia'


def read_fixture():
    manifest = json.loads(FIXTURE.with_suffix('.json').read_text(encoding='utf8'))
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == manifest['fixtureSha256']
    return FIXTURE.read_text(encoding='utf8')


def parse(text):
    return parser.parse_raw_battle_log_text(text, include_rdps_debug=True)


def test_real_packet_subset_restores_corrosion_without_reapplying_own_ignore():
    text = read_fixture()
    result = parse(text)
    assert result['battle']['total_damage'] == 95642
    assert [hit['packet_seq_id'] for hit in result['debug_hits']] == [8279, 8435]
    for hit, resistance in zip(result['debug_hits'], ['-23.59999990463257', '-24.44000005722046']):
        # This subset isolates one external effect. The full-battle replay
        # separately checks log weighting against other simultaneous zones.
        expected_self = Decimal(hit['hit_value']) * 120 / (100 - Decimal(resistance))
        actual = {row['character_key']: row['value'] for row in hit['rdps_contributions']}
        assert actual == pytest.approx({ATTACKER: float(expected_self), PROVIDER: hit['hit_value'] - float(expected_self)}, abs=1e-8)
        assert sum(actual.values()) == pytest.approx(hit['hit_value'], abs=1e-8)
        zone = next(row for row in hit['zones'] if row['zone'] == 'res')
        assert zone['rate_unit'] == 'resistance_points'
        assert zone['effective_resistance_source'] == 'PKT_ATTR.defender.attr98'
        external = [row for row in zone['contributors'] if row['scope'] == 'external']
        assert len(external) == 1
        assert external[0]['rdps_whitelist_keys'] == ['buff_chr_0025_ardelia_dynamic_def_decrease', BUFF]
        assert hit['packet_modifier_handles']['attacker'][0]['index'] == 0
    for view in (parse_hit_debug_log_text(text), parse_hit_viewer_log_text(text)):
        assert [hit['rdps_contributions'] for hit in view['hits']] == [hit['rdps_contributions'] for hit in result['debug_hits']]


@pytest.mark.parametrize('mutation', ['no_refresh', 'foreign_refresh_session', 'missing_parameter', 'missing_creation_context'])
def test_incomplete_parameter_evidence_cannot_enable_corrosion(mutation):
    lines = []
    for line in read_fixture().splitlines():
        if UID in line:
            if mutation == 'no_refresh' and ' BUFF_ATTR_REFRESH_UNRESOLVED ' in line:
                continue
            if mutation == 'missing_creation_context' and ' BUFF_CREATE_OBSERVED ' in line:
                continue
            if mutation == 'missing_parameter' and ' BB[' in line:
                line = line.replace(' additional_def_decrease=0', '')
            if mutation == 'foreign_refresh_session' and ' BUFF_ATTR_REFRESH_UNRESOLVED ' in line:
                prefix, payload = line.split(' BUFF_ATTR_REFRESH_UNRESOLVED ', 1)
                data = json.loads(payload)
                data['sessionId'] = 'explicit-foreign-session'
                line = prefix + ' BUFF_ATTR_REFRESH_UNRESOLVED ' + json.dumps(data)
        lines.append(line)
    result = parse('\n'.join(lines))
    assert result['battle']['total_damage'] == 95642
    assert all(not any(row['character_key'] == PROVIDER for row in hit['rdps_contributions']) for hit in result['debug_hits'])


def test_late_conflicting_update_invalidates_earlier_refresh_without_erasing_damage():
    lines = read_fixture().splitlines()
    update = next(line for line in lines if UID in line and ' BB_MODIFY_DYNAMIC seq=8258 ' in line)
    conflict = update.replace('value=-3.5999999046325684', 'value=-8.0')
    position = next(i for i, line in enumerate(lines) if ' HP_V2 #348 ' in line)
    lines.insert(position, conflict)
    result = parse('\n'.join(lines))
    assert result['debug_hits'][0]['rdps_contributions'] == [
        {'character_key': ATTACKER, 'character_name': '莱万汀', 'value': 49138.0}]
    assert any(row['character_key'] == PROVIDER for row in result['debug_hits'][1]['rdps_contributions'])


@pytest.mark.parametrize('report_present,initial_stamp,extra_unmodeled,expected', [
    (True, 0, False, 12), (False, 0, False, 0), (None, 0, False, 0),
    (True, 150, False, 0), (True, 0, True, 0),
])
def test_explicit_initial_state_and_complete_report_support_persistent_resistance(report_present, initial_stamp, extra_unmodeled, expected):
    hit = {'character_key': ATTACKER, 'source_actor_id': '10', 'target_enemy_key': 'eny_0051_rodin',
           'target_actor_id': '20', 'hit_value': 132, 'ts_ms': 500, 'line_no': 50,
           'packet_seq_id': 5, 'packet_session_id': 'fixture', 'damage_element': 'fire',
           'damage_school': 'spell', 'target_resistance_points': {'fire': 0},
           'target_stats_snapshot_ts_ms': initial_stamp, 'target_stats_snapshot_line_no': 1,
           'packet_attribute_report_present': report_present, 'packet_attr_details': {'attacker': [], 'defender': []}}
    spec = {'zone': 'res', 'element': 'fire', 'parameter_keys': ['def_decrease', 'additional_def_decrease'],
            'external_rdps_allowed': True, 'rdps_whitelist_keys': [BUFF],
            'parameter_snapshots': [{'operation_seq': 2, 'line_no': 20, 'ts_ms': 200, 'session_id': 'fixture',
                                     'status': 'attribute_parameters_observed',
                                     'bb_values': {'def_decrease': -10, 'additional_def_decrease': 0}}]}
    window = {'uid': '30', 'event_key': BUFF, 'source_character_key': PROVIDER,
              'target_character_key': 'eny_0051_rodin', 'target_actor_id': '20',
              'start_ts_ms': 100, 'start_line_no': 10, 'end_ts_ms': 1000, 'end_line_no': 100,
              'zone_effects': [], 'dynamic_effects': [spec]}
    records = ([{'uid': '31', 'event_key': BUFF, 'target_actor_id': '20', 'ts_ms': 300, 'line_no': 30}]
               if extra_unmodeled else [])
    result = parser._allocate_rdps_for_hit(hit, [window], include_debug=True, condition_buff_records=records)
    assert result['contributions_map'].get(PROVIDER, 0) == pytest.approx(expected)
    assert sum(result['contributions_map'].values()) == pytest.approx(132)


def test_explicit_foreign_hit_session_cannot_consume_parameter_snapshots():
    result = parse(read_fixture().replace('packetSessionId="offline_schema_probe"', 'packetSessionId="foreign-session"'))
    assert all(not any(row['character_key'] == PROVIDER for row in hit['rdps_contributions']) for hit in result['debug_hits'])


def test_conflicting_hit_attribute_lines_do_not_choose_the_last_value():
    text = read_fixture()
    line = next(row for row in text.splitlines() if ' PKT_ATTR #348 ' in row)
    changed = line.replace('-23.59999990463257', '-40.0')
    result = parse(text.replace(line, line + '\n' + changed))
    first = result['debug_hits'][0]
    assert first['packet_attr_conflict'] is True
    assert first['rdps_contributions'] == [{'character_key': ATTACKER, 'character_name': '莱万汀', 'value': 49138.0}]
