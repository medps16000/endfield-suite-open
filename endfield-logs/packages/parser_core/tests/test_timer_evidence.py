from copy import deepcopy
import pytest
from parser_core.battle_log_parser import _extract_fields, _parse_prefixed_timestamp_ms, parse_raw_battle_log_text
from parser_core.timer_evidence import collect_timer_evidence, validate_timer_evidence

GAME='dung01_bossrush03_03'
START=f'[02:14:33.955] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId={GAME} challengeStartTs=1790100876783'
END=f'[02:15:07.908] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId={GAME} isPass=1 passTime=20120'
FREEZE=f'[02:14:58.979] GAME_MECHANICS_TIME_FREEZE source=SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE gameId={GAME} count=2 freezeInfos=[1:99999,0:13833] totalFreezeMs=99999'


def evidence(*middle):
    return collect_timer_evidence('\n'.join([START,*middle,END]),_extract_fields,_parse_prefixed_timestamp_ms)


def test_frozen_challenge_accepts_exact_official_duration_without_summing_time_keys():
    e=evidence('[02:14:53.553] GAME_TIMER_END source=PacketBattleState startMs=3847143 endMs=3864162 packetElapsedMs=17019',
               '[02:14:58.948] GAME_TIMER_START source=PacketBattleState startMs=3866994',FREEZE)
    assert validate_timer_evidence(e,duration_ms=20120,game_id=GAME)==[]
    assert e['complete']['observedMs']-e['start']['observedMs']==33953
    assert e['events'][-1]['freezeInfos']=='[1:99999,0:13833]'


def test_rewritten_uploaded_duration_is_rejected_even_if_client_boolean_says_valid():
    assert 'official_duration_payload_mismatch' in validate_timer_evidence(evidence(FREEZE),duration_ms=151,game_id=GAME)


@pytest.mark.parametrize('label',['GAME_INST_RESTART','GAME_INST_LEAVE'])
def test_explicit_lifecycle_boundary_invalidates_old_start(label):
    e=evidence(f'[02:14:59.000] {label} source=SC_GAME_MECHANICS_SYNC_LEAVE_GAME_INST gameId={GAME}')
    assert 'official_challenge_interrupted' in validate_timer_evidence(e,duration_ms=20120,game_id=GAME)


def test_phase_clock_arithmetic_is_checked_in_its_own_domain():
    e=evidence('[02:14:54.000] GAME_TIMER_END source=PacketBattleState startMs=100 endMs=800 packetElapsedMs=900')
    assert 'packet_stage_timer_arithmetic_conflict' in validate_timer_evidence(e,duration_ms=20120,game_id=GAME)


@pytest.mark.parametrize('value',['[0:-1]','[0:10,0:20]','[0:unknown]'])
def test_malformed_freeze_is_not_filled_with_zero_or_maximum(value):
    e=evidence(FREEZE.replace('[1:99999,0:13833]',value))
    assert validate_timer_evidence(e,duration_ms=20120,game_id=GAME)


def test_freeze_counter_regression_within_one_challenge_is_a_conflict():
    e=evidence(FREEZE,FREEZE.replace('13833','8914'))
    assert 'official_freeze_counter_regressed' in validate_timer_evidence(e,duration_ms=20120,game_id=GAME)


def test_late_freeze_after_completion_is_not_carried_into_the_result():
    e=collect_timer_evidence('\n'.join([START,FREEZE,END,FREEZE.replace('13833','0')]),_extract_fields,_parse_prefixed_timestamp_ms)
    assert len(e['events'])==1
    assert validate_timer_evidence(e,duration_ms=20120,game_id=GAME)==[]


def test_capture_reset_cannot_reuse_an_old_challenge_start():
    e=collect_timer_evidence('\n'.join([START,'[02:14:40.000] GAME_TIMER_RESET source=CAPTURE_SESSION_START',END]),_extract_fields,_parse_prefixed_timestamp_ms)
    assert 'missing_official_start' in validate_timer_evidence(e,duration_ms=20120,game_id=GAME)


def test_new_challenge_does_not_inherit_freeze_or_completion():
    e=collect_timer_evidence('\n'.join([START,FREEZE,END,START.replace('1790100876783','1790100999999')]),_extract_fields,_parse_prefixed_timestamp_ms)
    assert e['complete'] is None and e['events']==[]
    assert validate_timer_evidence(e,duration_ms=20120,game_id=GAME)==['missing_official_completion']


def test_other_game_freeze_is_not_attached_and_other_game_end_does_not_close_scope():
    e=evidence(FREEZE.replace(GAME,'another_game'),END.replace(GAME,'another_game'))
    assert e['complete']['gameId']==GAME
    assert validate_timer_evidence(e,duration_ms=20120,game_id=GAME)==[]


def test_conflicting_official_completions_are_rejected():
    e=collect_timer_evidence('\n'.join([START,END,END.replace('20120','100')]),_extract_fields,_parse_prefixed_timestamp_ms)
    assert 'conflicting_official_timer_events' in validate_timer_evidence(e,duration_ms=20120,game_id=GAME)


def test_missing_arrival_clock_is_not_a_guessed_elapsed_time():
    e=evidence(FREEZE);e['complete']['observedMs']=None
    assert validate_timer_evidence(e,duration_ms=20120,game_id=GAME)==[]


def test_missing_start_identity_is_not_replaced_by_nearby_packet_start():
    e=evidence(FREEZE)
    e['start']['challengeStartTs']=None
    assert 'missing_official_challenge_identity' in validate_timer_evidence(e,duration_ms=20120,game_id=GAME)


def test_damage_membership_uses_official_event_boundaries_not_clock_proximity():
    hit = ('[02:14:35.000] HP_V2 #{number} hit={value} eHP=1000 '
           'skill="chr_0030_zhuangfy_attack1" src=chr_0030_zhuangfy tgt=eny_0052_palesent')
    result = parse_raw_battle_log_text('\n'.join([
        hit.format(number=1,value=100), START,
        hit.format(number=2,value=200), END,
        hit.format(number=3,value=400),
    ]))
    assert result['battle']['total_damage']==200
    assert result['battle']['duration_ms']==20120
    assert result['battle']['timer_integrity_ok'] is True


def test_one_official_challenge_crossing_midnight_keeps_both_phases():
    text='\n'.join([
        f'[23:59:50.000] OFFICIAL_TIMER_START source=SC_GAME_MECHANICS_SYNC_CHALLENGE_START gameId={GAME} challengeStartTs=1',
        '[23:59:51.000] SQUAD size=1 members=[chr_0030_zhuangfy_1001]',
        '[23:59:52.000] HP_V2 #1 hit=100 cum=100 raw=100 pHP=1 eHP=900 skill="chr_0030_zhuangfy_attack1" hits=1 src=chr_0030_zhuangfy tgt=eny_0052_palesent atk=chr_0030_zhuangfy seg=0 shared=2 critFlag=0',
        '[23:59:55.000] GAME_TIMER_END source=PacketBattleState startMs=1 endMs=5001 packetElapsedMs=5000 elapsedMs=5000 sane=1 official=1',
        '[00:00:01.000] GAME_TIMER_START source=PacketBattleState startMs=11001 official=1',
        '[00:00:03.000] HP_V2 #2 hit=900 cum=900 raw=900 pHP=1 eHP=0 skill="chr_0030_zhuangfy_attack1" hits=1 src=chr_0030_zhuangfy tgt=eny_0052_palesent atk=chr_0030_zhuangfy seg=0 shared=2 critFlag=0',
        f'[00:00:05.000] OFFICIAL_TIMER_END source=SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE gameId={GAME} isPass=1 passTime=7000',
    ])
    result=parse_raw_battle_log_text(text,file_name='trace_20260922-235950.log')
    assert result['battle']['total_damage']==1000
    assert result['battle']['duration_ms']==7000
    assert result['battle']['timer_integrity_ok'] is True
    assert result['battle']['official_timer_observed_elapsed_ms']==15000
