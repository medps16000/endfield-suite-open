from parser_core.critical_context import build_critical_context, critical_buff_profile, update_critical_observation
import pytest


def make_hit(**updates):
    return {"character_key": "chr_test", "source_actor_id": "71", "line_no": 20,
            "critical_flag_observed": True, "crit_flag": 0, "hit_value": 100,
            "raw_hit_value": 100, "damage_element": "physical",
            "enemy_hp_after_observed": True, "enemy_hp_before": 10000,
            "damage_calculation": {"damageProcessors": []}, **updates}


def observation(**updates):
    return {"template": "chr_test", "values": {9: 0.2, 10: 0.5}, "lines": {9: 10, 10: 10}, **updates}


def test_critical_parameters_use_observed_attributes_not_observed_critical_frequency():
    row = build_critical_context(make_hit(), observation(), {}, [])
    assert row["status"] == "verified"
    assert row["critRate"] == 0.2 and row["critDamageBonus"] == 0.5
    assert row["isCritical"] is False


def test_missing_or_different_owner_attributes_stay_unknown():
    for stats in [None, observation(template="chr_other"), observation(values={9: 0.2})]:
        assert build_critical_context(make_hit(), stats, {}, [])["status"] == "unavailable"
    assert build_critical_context(make_hit(critical_flag_observed=False), observation(), {}, [])["status"] == "unavailable"


def test_explicit_zero_final_multiplier_makes_burning_noncritical():
    processor = {"$type": "Beyond.Gameplay.Core.InstantModifyAttribute", "modifyTargetSide": "Attacker",
                 "modifier": {"attributeType": "CriticalRate", "formulaItem": "FinalMultiplier", "modifyAttributeType": "Specific",
                              "param": {"useBlackboardKey": False, "value": 0.0}}}
    hit = make_hit(damage_calculation={"damageProcessors": [processor]})
    assert build_critical_context(hit, observation(), {}, [])["critRate"] == 0
    assert build_critical_context({**hit, "crit_flag": 1}, observation(), {}, [])["status"] == "unavailable"


def test_literal_addition_and_probability_limits():
    processor = {"$type": "Beyond.Gameplay.Core.AttackCriticalRateProcessor", "addition": {"useBlackboardKey": False, "value": 2.0}}
    hit = make_hit(crit_flag=1, damage_calculation={"damageProcessors": [processor]})
    assert build_critical_context(hit, observation(), {}, [])["critRate"] == 1


def test_unbound_blackboard_parameter_is_not_replaced_by_its_serialized_default():
    processor = {"$type": "Beyond.Gameplay.Core.AttackCriticalRateProcessor", "addition": {"useBlackboardKey": True, "blackboardKey": "crit_up", "value": 1.0}}
    hit = make_hit(damage_calculation={"damageProcessors": [processor]})
    assert build_critical_context(hit, observation(), {}, [])["status"] == "unavailable"


def test_changed_critical_buff_requires_a_corresponding_attribute_update():
    profiles = {"buff_crit": {"attributes": [9], "processors": False}}
    records = [{"event_key": "buff_crit", "target_actor_id": "71", "line_no": 12}]
    assert build_critical_context(make_hit(), observation(), profiles, records)["status"] == "unavailable"
    assert build_critical_context(make_hit(), observation(lines={9: 13, 10: 10}), profiles, records)["status"] == "verified"


def test_a_buffs_own_damage_processors_do_not_modify_other_attacks():
    root = {"actions": [{"$type": "Beyond.Gameplay.Core.DamageAction.DamageUnit",
                         "damageProcessors": [{"$type": "Beyond.Gameplay.Core.AttackCriticalRateProcessor"}]}]}
    assert critical_buff_profile(root) == {"missing": False, "attributes": [], "processors": False}


def test_overkill_retains_uncapped_value_and_observed_cap_for_probability_model():
    row = build_critical_context(make_hit(crit_flag=1, hit_value=50, raw_hit_value=150, overkill_damage=100), observation(), {}, [])
    assert row["uncappedDamage"] == 150 and row["damageCap"] == 50


def test_lifedrain_bypasses_critical_but_real_damage_does_not():
    assert build_critical_context(make_hit(damage_element="lifedrain"), None, {}, [])["critRate"] == 0
    assert build_critical_context(make_hit(damage_element="real"), observation(), {}, [])["critRate"] == 0.2


def test_attribute_updates_preserve_other_fields_and_invalidate_conflicting_identity():
    states = {}
    update_critical_observation(states, '71', {'kind': 'character', 'template': 'chr_test', 'attrs': {9: 0.2, 10: 0.5}}, 1)
    update_critical_observation(states, '71', {'template': 'chr_test', 'attrs': {9: 0.8}}, 2, partial=True)
    assert states['71']['values'] == {9: 0.8, 10: 0.5}
    assert states['71']['lines'] == {9: 2, 10: 1}
    update_critical_observation(states, '71', {'template': 'chr_other', 'attrs': {9: 1}}, 3, partial=True)
    assert '71' not in states


def test_expired_critical_buff_invalidates_an_older_attribute_snapshot():
    profiles = {"buff_crit": {"attributes": [9], "processors": False}}
    windows = [{"event_key": "buff_crit", "target_actor_id": "71", "snapshot_update": True,
                "end_ts_ms": 15, "end_line_no": None}]
    assert build_critical_context(make_hit(ts_ms=20), observation(times={9: 10, 10: 10}), profiles, windows)["status"] == "unavailable"
    assert build_critical_context(make_hit(ts_ms=20), observation(times={9: 16, 10: 10}), profiles, windows)["status"] == "verified"


def test_dynamic_critical_state_change_does_not_keep_old_probability():
    profiles = {"buff_crit": {"attributes": [9], "processors": False}}
    windows = [{"event_key": "buff_crit", "target_actor_id": "71", "snapshot_update": True,
                "end_is_observation_boundary": True, "critical_state_change_lines": [12]}]
    assert build_critical_context(make_hit(), observation(), profiles, windows)["status"] == "unavailable"
    assert build_critical_context(make_hit(), observation(lines={9: 13, 10: 10}), profiles, windows)["status"] == "verified"


@pytest.mark.parametrize('kind', ['marker', 'critical', 'unknown'])
def test_parser_resolves_numeric_buff_aliases_without_waiving_unknown_effects(monkeypatch, kind):
    from parser_core import battle_log_parser as parser
    root={'id':'buff_fixture_marker'}
    if kind=='critical':
        root['processor']={'$type':'Beyond.Gameplay.Core.AttackCriticalRateProcessor'}
    monkeypatch.setattr(parser,'_load_num_id_str_buff_map',lambda:{'123456':'buff_fixture_marker'} if kind!='unknown' else {})
    monkeypatch.setattr(parser,'_load_current_buff_roots',lambda:{'buff_fixture_marker':root})
    original=parser._compute_rdps_damage_shares
    def add_retained_alias(hits, windows, condition_buff_records=None):
        result=original(hits,windows,condition_buff_records)
        for hit in hits:
            hit['_active_target_buff_ids']={'123456','buff_fixture_marker'}
        return result
    monkeypatch.setattr(parser,'_compute_rdps_damage_shares',add_retained_alias)
    observed=[]
    original_context=parser.build_critical_context
    def capture(hit,stats,profiles,windows):
        context=original_context(hit,stats,profiles,windows)
        observed.append((profiles,context))
        return context
    monkeypatch.setattr(parser,'build_critical_context',capture)
    parsed=parser.parse_raw_battle_log_text('\n'.join([
        '[10:00:00.000] ENTITY_STATS id=1001 template=chr_0004_pelica kind=character level=90 hp=1000 attrs=[9:0.2/0.2 10:0.5/0.5]',
        '[10:00:01.000] HP_V2 #1 hit=100 cum=100 raw=100 packetFinalValue=100 pHP=100 eHP=1000 skill="chr_0004_pelica_combo_skill_projhit" actionId=11 damageUnitIndex=0 src=chr_0004_pelica tgt=eny_0051_rodin atk=chr_0004_pelica atkId=1001 tgtId=3001 hits=1 critFlag=0']))
    assert len(observed)==1
    profiles,context=observed[0]
    if kind=='unknown':
        assert profiles['123456']['missing'] is True
        assert 'unresolved_active_buff' in context['reasons']
    else:
        assert profiles['123456']==profiles['buff_fixture_marker']
        assert profiles['123456']['processors'] is (kind=='critical')
        if kind=='critical': assert 'conditional_critical_modifier' in context['reasons']
        else: assert context['status']=='verified'
