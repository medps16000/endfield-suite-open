from uploader_core.battle_payload_builder import _raw_text_loadout_state, _raw_loadout_entries_for_segment

def test_scene_refine_does_not_cross_weapon_instance_or_battle_boundary():
    char='chr_0011_seraph'
    other='chr_0034_typhoea'
    def row(t, instance, weapon, refine):
        return f'[{t}] LOADOUT slot=0 char={char} charLv=90 potential=5 weaponInstId={instance} weaponTemplate={weapon} weaponLv=0 refine={refine} break=0'
    def teammate(t):
        return f'[{t}] LOADOUT slot=1 char={other} charLv=90 potential=5 weaponInstId=111 weaponTemplate=wpn_funnel_0019 weaponLv=0 refine=5 break=0'
    lines=[
        f'[13:20:00.000] SQUAD size=2 members=[{char}_110058 {other}_110059]',
        '[13:20:00.000] LOADOUT reason=SC_SYNC_CHAR_BAG_INFO',
        row('13:20:00.000',80,'wpn_funnel_0005','5'),teammate('13:20:00.000'),
        '[13:20:15.000] LOADOUT reason=SC_WEAPON_PUTON',
        row('13:20:15.000',91,'wpn_funnel_0010','unknown'),teammate('13:20:15.000'),
        f'[13:20:53.000] SQUAD size=2 members=[{char}_110058 {other}_110059]',
        f'[13:20:53.000] CHAR_SKILLS owner={char} ownerId=110058 count=1 skills=[sk_wpn_funnel_0010:9]',
        '[13:20:53.000] LOADOUT reason=SC_SELF_SCENE_INFO',
        row('13:20:53.000',91,'wpn_funnel_0010','unknown'),teammate('13:20:53.000'),
        f'[13:20:53.000] LOADOUT_STATS slot=0 char={char} weaponInstId=91 weaponTemplate=wpn_funnel_0010 weaponSync=template_only weaponSourceSkills={{}}',
        '[13:22:00.000] LOADOUT reason=SC_WEAPON_PUTON',
        row('13:22:00.000',92,'wpn_funnel_0010','unknown'),teammate('13:22:00.000'),
        '[13:22:01.000] marker',
        f'[13:23:00.000] CHAR_SKILLS owner={char} ownerId=110058 count=1 skills=[sk_wpn_funnel_0010:9]',
    ]
    groups,fallback=_raw_text_loadout_state('\n'.join(lines))
    for line_index,instance,refine in [(3,'80',5),(7,'91',None),(8,'91',5),(12,'91',5),(16,'92',None),(17,'92',5)]:
        rows=_raw_loadout_entries_for_segment(groups,fallback,{'first_hit_line_index':line_index})
        assert len(rows)==2
        owner=next(row for row in rows if row['character_key']==char)
        assert owner['weapon_inst_id']==instance
        assert owner.get('weapon_refine')==refine, (line_index, owner)
