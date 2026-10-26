import pytest
from parser_core import battle_log_parser as parser

CHAR = 'chr_0011_seraph'
LINE = '[13:20:53.822] CHAR_SKILLS owner=chr_0011_seraph ownerId=110058 count=1 skills=[sk_wpn_funnel_0010:9]'

@pytest.mark.parametrize('mode', ['exact', 'wrong_owner', 'wrong_weapon', 'missing_instance', 'duplicate_owner', 'ambiguous_level', 'base_only', 'conflicting_level', 'known_refine'])
def test_scene_refine_requires_owner_weapon_and_unique_third_skill(mode):
    context = {'character_key': CHAR, 'weapon_inst_id': '91', 'weapon_template': 'wpn_funnel_0010', 'weapon_refine': None}
    squad = {'110058': CHAR}
    line = LINE
    if mode == 'wrong_owner': squad = {'999': CHAR}
    if mode == 'wrong_weapon': context['weapon_template'] = 'wpn_funnel_0005'
    if mode == 'missing_instance': context['weapon_inst_id'] = None
    if mode == 'duplicate_owner': squad['110059'] = CHAR
    if mode == 'ambiguous_level': line = line.replace(':9]', ':8]')
    if mode == 'base_only': line = line.replace('sk_wpn_funnel_0010:9', 'wpn_attr_will_high:9')
    if mode == 'conflicting_level': line = line.replace(':9]', ':9 sk_wpn_funnel_0010:1]')
    if mode == 'known_refine': context['weapon_refine'] = 0
    before = dict(context)
    result = parser._parse_loadout_stats_snapshot(line, loadout_context=context, squad_actor_keys=squad)
    if mode == 'exact':
        assert result['weapon_refine'] == 5
        assert result['weapon_refine_evidence']['weapon_inst_id'] == '91'
        assert result['weapon_refine_evidence']['skill_id'] == 'sk_wpn_funnel_0010'
    else:
        assert result is None
    assert context == before

def test_numeric_scene_main_skill_resolves_through_official_table():
    context = {'character_key': CHAR, 'weapon_inst_id': '91', 'weapon_template': 'wpn_funnel_0010', 'weapon_refine': None}
    result = parser._parse_loadout_stats_snapshot(LINE.replace('sk_wpn_funnel_0010', '2230'),
        loadout_context=context, squad_actor_keys={'110058': CHAR})
    assert result['weapon_refine'] == 5

def test_same_timestamp_future_scene_and_new_instance_do_not_rewrite_loadout():
    unknown={'character_key':CHAR,'weapon_inst_id':'91','weapon_template':'wpn_funnel_0010','weapon_refine':None}
    known={**unknown,'weapon_refine':5,'weapon_refine_source':'source_skill'}
    groups=[{'ts_ms':1000,'line_no':2,'rows':{CHAR:unknown}},
            {'ts_ms':1000,'line_no':4,'rows':{CHAR:known}},
            {'ts_ms':2000,'line_no':6,'rows':{CHAR:{**unknown,'weapon_inst_id':'92'}}}]
    assert parser._select_loadout_snapshot_for_battle(groups,{},anchor_ms=1000,anchor_line_no=3)[CHAR].get('weapon_refine') is None
    assert parser._select_loadout_snapshot_for_battle(groups,{},anchor_ms=1000,anchor_line_no=5)[CHAR]['weapon_refine']==5
    assert parser._select_loadout_snapshot_for_battle(groups,{},anchor_ms=2000,anchor_line_no=7)[CHAR].get('weapon_refine') is None


@pytest.mark.parametrize('mode', ['exact', 'other_weapon', 'no_gem', 'missing_weapon', 'malformed', 'duplicate_term', 'not_refreshed'])
def test_log_gem_refine_requires_explicit_same_weapon_binding(mode):
    line = ('[21:35:00.000] LOADOUT_STATS slot=3 char=chr_9000_endmin weaponInstId=91 '
            'weaponTemplate=wpn_sword_0026 weaponSourceSkills={3759:level=4:potentialLv=3:bb={}} '
            'gemInstId=42 gemWeaponId=91 gemTerms={68:old_label@cost1,86:old_label@cost1,66:old_label@cost3}')
    if mode == 'other_weapon': line = line.replace('gemWeaponId=91', 'gemWeaponId=92')
    if mode == 'no_gem': line = line.replace('gemInstId=42', 'gemInstId=0')
    if mode == 'missing_weapon': line = line.replace('weaponInstId=91', 'weaponInstId=0')
    if mode == 'malformed': line = line.replace('66:old_label@cost3', '66:broken')
    if mode == 'duplicate_term': line = line.replace('66:old_label@cost3', '66:old_label@cost3,66:old_label@cost1')
    row = parser._parse_loadout_stats_snapshot(line, allow_bound_gem=mode != 'not_refreshed')
    assert row['weapon_refine'] == (0 if mode == 'exact' else None)
