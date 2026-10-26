from __future__ import annotations

import re
from types import SimpleNamespace
import pytest

from endfield_pcap.models import RuntimeMetrics
from endfield_pcap.proto_generated.SC_OBJECT_ENTER_VIEW_pb2 import SC_OBJECT_ENTER_VIEW
from endfield_pcap.service import SessionPipeline


def _pipeline_shell() -> SessionPipeline:
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.session_id = "test-session"
    pipeline.char_bag_by_objid = {}
    pipeline.current_team_char_ids = []
    pipeline.scene_team_char_ids = []
    pipeline.dungeon_team_char_ids = None
    pipeline.equip_by_inst_id = {}
    pipeline.weapon_by_inst_id = {}
    pipeline.gem_by_inst_id = {}
    pipeline.gem_by_weapon_id = {}
    pipeline._last_loadout_signature = None
    pipeline.metrics = RuntimeMetrics()
    pipeline.on_event = lambda event: None
    return pipeline


def test_template_only_refinement_requires_exact_third_skill():
    pipeline = _pipeline_shell()
    pipeline.char_bag_by_objid[1] = _char(1, 'chr_0004_pelica')
    pipeline.char_bag_by_objid[1]['weapon_id'] = 10
    pipeline.current_team_char_ids = [1]
    pipeline.weapon_by_inst_id[10] = {
        'template_string': 'wpn_funnel_0005', 'sync_source': 'template_only', 'refine_lv': 0,
    }
    assert pipeline._loadout_rows()[0]['refine'] is None
    pipeline.char_bag_by_objid[1]['weapon_source_skills'] = [{'skill_int_id': 2217, 'level': 9}]
    assert pipeline._loadout_rows()[0]['refine'] == 5
    pipeline.weapon_by_inst_id[10]['sync_source'] = 'WEAPON_DATA'
    assert pipeline._loadout_rows()[0]['refine'] == 0


def test_scene_weapon_skill_refresh_repopulates_equipped_weapon(monkeypatch):
    pipeline = _pipeline_shell()
    pipeline.skill_levels_by_battle_inst = {}
    pipeline.global_skill_levels = {}
    pipeline.squad_index = {110058: SimpleNamespace(obj_id=1, templateid='chr_0011_seraph')}
    pipeline.char_bag_by_objid[1] = _char(1, 'chr_0011_seraph')
    pipeline.char_bag_by_objid[1]['weapon_id'] = 91
    pipeline.current_team_char_ids = [1]
    pipeline.weapon_by_inst_id[91] = {'template_string': 'wpn_funnel_0010', 'sync_source': 'template_only'}
    source = [{'skill_int_id': 2230, 'skill_str_id': 'sk_wpn_funnel_0010', 'level': 9, 'potential_lv': 5}]
    monkeypatch.setattr(pipeline, '_weapon_source_skills', lambda info: source)
    monkeypatch.setattr(pipeline, '_template_id_payload', lambda item: ('Skill', 2230, 'sk_wpn_funnel_0010'))
    info = SimpleNamespace(skill_list=[SimpleNamespace(skill_id=None, level=9)])
    assert pipeline._loadout_rows()[0]['refine'] is None
    pipeline._cache_scene_skill_levels(999, info)
    assert pipeline._loadout_rows()[0]['refine'] is None
    pipeline._cache_scene_skill_levels(110058, info)
    assert pipeline._loadout_rows()[0]['refine'] == 5
    assert pipeline.char_bag_by_objid[1]['weapon_source_skills'][0]['potential_lv'] == 0
    assert source[0]['potential_lv'] == 5
    pipeline.char_bag_by_objid[1]['weapon_source_skills'][0]['blackboard'] = {'atk_up': 0.1}
    pipeline._cache_scene_skill_levels(110058, info)
    assert pipeline.char_bag_by_objid[1]['weapon_source_skills'][0]['blackboard'] == {'atk_up': 0.1}
    pipeline.char_bag_by_objid[1]['weapon_id'] = 92
    pipeline.char_bag_by_objid[1]['weapon_source_skills'] = []
    pipeline.weapon_by_inst_id[92] = {'template_string': 'wpn_funnel_0005', 'sync_source': 'template_only'}
    pipeline._cache_scene_skill_levels(110058, info)
    assert pipeline._loadout_rows()[0]['refine'] is None


def test_scene_weapon_refine_refresh_accepts_real_server_skill_message():
    from endfield_pcap.proto_generated.SC_SELF_SCENE_INFO_pb2 import SC_SELF_SCENE_INFO
    pipeline = _pipeline_shell()
    pipeline.skill_levels_by_battle_inst = {}
    pipeline.global_skill_levels = {}
    pipeline.squad_index = {110058: SimpleNamespace(obj_id=1, templateid='chr_0011_seraph')}
    pipeline.char_bag_by_objid[1] = _char(1, 'chr_0011_seraph')
    pipeline.char_bag_by_objid[1]['weapon_id'] = 91
    pipeline.current_team_char_ids = [1]
    pipeline.weapon_by_inst_id[91] = {'template_string': 'wpn_funnel_0010', 'sync_source': 'template_only'}
    info = SC_SELF_SCENE_INFO().detail.char_list.add().battle_info
    skill = info.skill_list.add()
    skill.skill_id.type = 2
    skill.skill_id.int_id = 2230
    skill.skill_id.str_id = 'sk_wpn_funnel_0010'
    skill.source = 2
    skill.level = 9
    skill.inst_id = 110077
    for field in ('str_id', 'int_id'):
        pipeline.char_bag_by_objid[1]['weapon_source_skills'] = []
        if field == 'str_id': skill.skill_id.str_id = 'sk_wpn_funnel_0010'
        else: skill.skill_id.int_id = 2230
        pipeline._cache_scene_skill_levels(110058, info)
        assert pipeline._loadout_rows()[0]['refine'] == 5


def _char(char_id: int, template: str, *, equip_col: dict[int, int] | None = None) -> dict[str, object]:
    return {
        "objid": char_id,
        "templateid": template,
        "level": 90,
        "potential": 0,
        "weapon_id": 0,
        "equip_col": dict(equip_col or {}),
        "equip_suit": {},
        "skill_int_ids": [],
        "weapon_source_skills": [],
    }


def _equip(equip_id: int, template: str, char_id: int) -> dict[str, object]:
    return {
        "equipid": equip_id,
        "templateid": 0,
        "template_string": template,
        "equip_char_id": char_id,
        "enhance": {},
        "legacy_enhance": {},
    }


def _weapon_source(skill_int_id: int, skill_str_id: str, *, potential: int) -> dict[str, object]:
    return {
        "skill_int_id": skill_int_id,
        "skill_str_id": skill_str_id,
        "inst_id": skill_int_id + 10_000,
        "potential_lv": potential,
        "level": 4,
        "blackboard": {"test": 1.0},
    }


def _map_count(text: object) -> int:
    return len(re.findall(r"\[\d+\]=", str(text or "")))


def test_detail_iteration_uses_protobuf_7_repeated_field_api() -> None:
    pipeline = _pipeline_shell()
    repeated_descriptor = SimpleNamespace(name="characters", is_repeated=True)
    singular_descriptor = SimpleNamespace(name="metadata", is_repeated=False)
    character = SimpleNamespace(common_info=SimpleNamespace(), battle_info=SimpleNamespace())
    detail = SimpleNamespace(
        ListFields=lambda: [
            (singular_descriptor, SimpleNamespace()),
            (repeated_descriptor, [character]),
        ]
    )

    rows = list(pipeline._iter_detail_objects(detail))

    assert rows == [("characters", character, character.common_info, character.battle_info)]


def test_real_object_enter_view_detail_is_compatible_with_runtime_descriptor() -> None:
    pipeline = _pipeline_shell()
    message = SC_OBJECT_ENTER_VIEW()
    monster = message.detail.monster_list.add()
    monster.common_info.id = 51
    monster.common_info.templateid = "eny_0051_rodin"
    monster.battle_info.battle_inst_id = 10051

    rows = list(pipeline._iter_detail_objects(message.detail))

    assert len(rows) == 1
    assert rows[0][0] == "monster_list"
    assert rows[0][1] is monster
    assert rows[0][2].templateid == "eny_0051_rodin"
    assert rows[0][3].battle_inst_id == 10051


def test_loadout_rows_use_current_team_order_when_scene_members_match() -> None:
    pipeline = _pipeline_shell()
    pipeline.char_bag_by_objid = {
        1: _char(1, "chr_0001_alpha"),
        2: _char(2, "chr_0002_beta"),
        3: _char(3, "chr_0003_gamma"),
    }
    pipeline.current_team_char_ids = [2, 1, 3]
    pipeline.scene_team_char_ids = [1, 2, 3]

    rows = pipeline._loadout_rows()

    assert [row["char"] for row in rows] == [
        "chr_0002_beta",
        "chr_0001_alpha",
        "chr_0003_gamma",
    ]


def test_loadout_rows_keep_scene_members_when_current_team_is_stale() -> None:
    pipeline = _pipeline_shell()
    pipeline.char_bag_by_objid = {
        1: _char(1, "chr_0001_alpha"),
        2: _char(2, "chr_0002_beta"),
        3: _char(3, "chr_0003_gamma"),
        4: _char(4, "chr_0004_delta"),
    }
    pipeline.current_team_char_ids = [2, 1, 4]
    pipeline.scene_team_char_ids = [1, 2, 3]

    rows = pipeline._loadout_rows()

    assert [row["char"] for row in rows] == [
        "chr_0001_alpha",
        "chr_0002_beta",
        "chr_0003_gamma",
    ]


def test_loadout_rows_reuse_current_team_when_enter_dungeon_omits_party() -> None:
    pipeline = _pipeline_shell()
    pipeline.char_bag_by_objid = {
        1: _char(1, "chr_0031_mifu"),
        2: _char(2, "chr_0028_wulfa"),
    }
    pipeline.current_team_char_ids = [1, 2]
    pipeline.dungeon_team_char_ids = None

    rows = pipeline._loadout_rows()

    assert [row["char"] for row in rows] == ["chr_0031_mifu", "chr_0028_wulfa"]


def test_loadout_rows_use_explicit_dungeon_team_before_scene_sync() -> None:
    pipeline = _pipeline_shell()
    pipeline.char_bag_by_objid = {
        1: _char(1, "chr_0031_mifu"),
        2: _char(2, "chr_0027_tangtang"),
        3: _char(3, "chr_0017_yvonne"),
    }
    pipeline.current_team_char_ids = [1]
    pipeline.dungeon_team_char_ids = [3, 2]

    rows = pipeline._loadout_rows()

    assert [row["char"] for row in rows] == ["chr_0017_yvonne", "chr_0027_tangtang"]


def test_enter_dungeon_treats_empty_team_as_no_party_override() -> None:
    pipeline = _pipeline_shell()
    pipeline.char_bag_by_objid = {
        1: _char(1, "chr_0031_mifu"),
        2: _char(2, "chr_0028_wulfa"),
    }
    pipeline.current_team_char_ids = [1, 2]
    pipeline.scene_team_char_ids = [1, 2]
    pipeline.merge_multi_phase_enemy_battles = False
    emitted = []
    pipeline.on_event = emitted.append

    pipeline._handle_enter_dungeon(
        SimpleNamespace(dungeon_id="indie_battletower007_ex", char_team=[]),
        1_000,
    )

    assert pipeline.scene_team_char_ids == []
    assert pipeline.dungeon_team_char_ids is None
    assert emitted[-1].payload["char_team_count"] == 0

    pipeline._emit_loadout_event("BATTLE_START", 1_100, force=True)

    assert emitted[-1].event_type == "LOADOUT"
    assert [row["char"] for row in emitted[-1].payload["rows"]] == [
        "chr_0031_mifu",
        "chr_0028_wulfa",
    ]


def test_loadout_slots_require_unambiguous_type_or_explicit_equip_col() -> None:
    pipeline = _pipeline_shell()
    pipeline.char_bag_by_objid = {
        1: _char(
            1,
            "chr_0027_tangtang",
            equip_col={0: 100},
        )
    }
    pipeline.current_team_char_ids = [1]
    pipeline.equip_by_inst_id = {
        100: _equip(100, "item_equip_t4_suit_attri01_hand_02", 1),
        101: _equip(101, "item_equip_t4_suit_attri01_body_05", 1),
        102: _equip(102, "item_equip_t4_suit_attri01_edc_07", 1),
        103: _equip(103, "item_equip_t4_suit_attri01_edc_08", 1),
    }

    row = pipeline._loadout_rows()[0]

    assert _map_count(row["equip_inst_ids"]) == 2
    assert "[0]=100" in row["equip_inst_ids"]
    assert "[1]=101" in row["equip_inst_ids"]
    assert '[2]=' not in row['equip_inst_ids']
    assert '[3]=' not in row['equip_inst_ids']
    assert row['unresolved_equip_inst_ids'] == [102, 103]
    # Both accessory slots accept both items. Only the packet resolves which
    # instance occupies each slot; no iteration order is a valid assignment.
    pipeline.char_bag_by_objid[1]['equip_col'].update({2: 103, 3: 102})
    row = pipeline._loadout_rows()[0]
    assert _map_count(row['equip_inst_ids']) == 4
    assert '[2]=103' in row['equip_inst_ids']
    assert '[3]=102' in row['equip_inst_ids']
    assert row['unresolved_equip_inst_ids'] == []


def test_equip_puton_clears_replaced_and_previous_owner_equips() -> None:
    pipeline = _pipeline_shell()
    pipeline.char_bag_by_objid = {
        1: _char(1, "chr_0001_alpha", equip_col={1: 101}),
        2: _char(2, "chr_0002_beta", equip_col={1: 201}),
    }
    pipeline.current_team_char_ids = [1, 2]
    pipeline.equip_by_inst_id = {
        101: _equip(101, "item_equip_t4_suit_attri01_body_01", 1),
        201: _equip(201, "item_equip_t4_suit_attri01_body_02", 2),
    }
    message = SimpleNamespace(
        charid=1,
        slotid=1,
        equipid=201,
        suitinfo={"suit_attri01": 1},
        put_off_charid=2,
        old_owner_suitinfo={},
    )

    pipeline._update_equip_puton(message, 1234)
    rows = pipeline._loadout_rows()

    assert pipeline.equip_by_inst_id[101]["equip_char_id"] == 0
    assert pipeline.equip_by_inst_id[201]["equip_char_id"] == 1
    assert pipeline.char_bag_by_objid[2]["equip_col"] == {}
    assert "[1]=201" in rows[0]["equip_inst_ids"]
    assert "101" not in rows[0]["equip_inst_ids"]


def test_equip_puton_does_not_move_replaced_item_to_a_slot_from_suit_counts() -> None:
    pipeline = _pipeline_shell()
    pipeline.char_bag_by_objid = {
        1: _char(
            1,
            "chr_0017_yvonne",
            equip_col={0: 100, 1: 101, 2: 102},
        )
    }
    pipeline.current_team_char_ids = [1]
    pipeline.equip_by_inst_id = {
        100: _equip(100, "item_equip_t4_suit_criti01_hand_02", 1),
        101: _equip(101, "item_equip_t4_suit_criti01_body_02", 1),
        102: _equip(102, "item_equip_t4_suit_criti01_edc_03", 1),
        200: _equip(200, "item_equip_t4_suit_heal01_edc_03", 0),
    }
    message = SimpleNamespace(
        charid=1,
        slotid=2,
        equipid=200,
        suitinfo={"suit_criti01": 3},
        put_off_charid=0,
        old_owner_suitinfo={},
    )

    pipeline._update_equip_puton(message, 1234)
    row = pipeline._loadout_rows()[0]

    assert "[0]=100" in row["equip_inst_ids"]
    assert "[1]=101" in row["equip_inst_ids"]
    assert "[2]=200" in row["equip_inst_ids"]
    assert '[3]=' not in row['equip_inst_ids']
    assert pipeline.equip_by_inst_id[102]['equip_char_id'] == 0


def test_moving_same_equipment_instance_within_character_clears_old_slot():
    pipeline = _pipeline_shell()
    pipeline.char_bag_by_objid = {1: _char(1, 'chr_0004_pelica', equip_col={2: 100})}
    pipeline.current_team_char_ids = [1]
    pipeline.equip_by_inst_id = {100: _equip(100, 'item_equip_t4_suit_attri01_edc_07', 1)}
    pipeline._update_equip_puton(SimpleNamespace(charid=1, slotid=3, equipid=100,
        suitinfo={}, put_off_charid=0, old_owner_suitinfo={}), 1234)
    assert pipeline.char_bag_by_objid[1]['equip_col'] == {3: 100}
    assert pipeline._loadout_rows()[0]['equip_inst_ids'] == '{[3]=100}'


def test_equipment_slot_does_not_come_from_name_or_last_empty_accessory_slot():
    assert SessionPipeline._equip_slot_candidates('fake_item_hand_01') == []
    assert SessionPipeline._infer_equip_slot('item_equip_t4_suit_attri01_edc_07', {0, 1, 2}) is None


def test_complete_character_bag_equipment_snapshot_overrides_stale_item_owner():
    from endfield_pcap.proto_generated.SC_SYNC_CHAR_BAG_INFO_pb2 import SC_SYNC_CHAR_BAG_INFO
    pipeline = _pipeline_shell()
    pipeline.char_potential_levels = {}
    message = SC_SYNC_CHAR_BAG_INFO()
    message.char_info.add(objid=1, templateid='chr_0004_pelica', level=90)
    pipeline._update_char_bag_info(message)
    pipeline.current_team_char_ids = [1]
    pipeline.equip_by_inst_id = {100: _equip(100, 'item_equip_t4_suit_attri01_body_05', 1)}
    row = pipeline._loadout_rows()[0]
    assert row['equipment_snapshot_complete'] is True
    assert row['equips'] == '{}' and row['equip_inst_ids'] == '{}'
    assert row['unresolved_equip_inst_ids'] == []


def test_invalid_current_team_index_is_not_replaced_by_first_team():
    from endfield_pcap.proto_generated.SC_SYNC_CHAR_BAG_INFO_pb2 import SC_SYNC_CHAR_BAG_INFO
    pipeline = _pipeline_shell()
    pipeline.char_potential_levels = {}
    message = SC_SYNC_CHAR_BAG_INFO(curr_team_index=9)
    message.team_info.add().char_team.append(1)
    pipeline._update_char_bag_info(message)
    assert pipeline.current_team_char_ids == []


def test_weapon_puton_swaps_weapon_state_between_current_and_previous_owner() -> None:
    pipeline = _pipeline_shell()
    seraph = _char(11, "chr_0011_seraph")
    seraph["potential"] = 5
    seraph["weapon_id"] = 1008
    seraph["weapon_source_skills"] = [
        _weapon_source(2228, "sk_wpn_funnel_0008", potential=5),
    ]
    seraph["skill_int_ids"] = [208, 2228]
    lizhiyan = _char(32, "chr_0032_lizhiyan")
    lizhiyan["weapon_id"] = 1016
    lizhiyan["weapon_source_skills"] = [
        _weapon_source(3629, "sk_wpn_funnel_0016", potential=0),
    ]
    lizhiyan["skill_int_ids"] = [648, 3629]
    pipeline.char_bag_by_objid = {11: seraph, 32: lizhiyan}
    pipeline.current_team_char_ids = [11, 32]
    pipeline.weapon_by_inst_id = {
        1008: {"inst_id": 1008, "template_string": "wpn_funnel_0008", "equip_char_id": 11},
        1016: {"inst_id": 1016, "template_string": "wpn_funnel_0016", "equip_char_id": 32},
    }
    message = SimpleNamespace(
        charid=32,
        weaponid=1008,
        offweaponid=1016,
        put_off_charid=11,
    )

    pipeline._update_weapon_puton(message, 1234)

    assert lizhiyan["weapon_id"] == 1008
    assert lizhiyan["weapon_source_skills"][0]["skill_str_id"] == "sk_wpn_funnel_0008"
    assert lizhiyan["weapon_source_skills"][0]["potential_lv"] == 0
    assert 2228 in lizhiyan["skill_int_ids"]
    assert 3629 not in lizhiyan["skill_int_ids"]
    assert seraph["weapon_id"] == 1016
    assert seraph["weapon_source_skills"][0]["skill_str_id"] == "sk_wpn_funnel_0016"
    assert seraph["weapon_source_skills"][0]["potential_lv"] == 5
    assert 3629 in seraph["skill_int_ids"]
    assert 2228 not in seraph["skill_int_ids"]
    assert pipeline.weapon_by_inst_id[1008]["equip_char_id"] == 32
    assert pipeline.weapon_by_inst_id[1016]["equip_char_id"] == 11


def test_weapon_puton_from_bag_invalidates_stale_weapon_source_skills() -> None:
    pipeline = _pipeline_shell()
    pelica = _char(4, "chr_0004_pelica")
    pelica["weapon_id"] = 1005
    pelica["weapon_source_skills"] = [
        _weapon_source(2217, "sk_wpn_funnel_0005", potential=0),
    ]
    pelica["skill_int_ids"] = [98, 2217]
    pipeline.char_bag_by_objid = {4: pelica}
    pipeline.current_team_char_ids = [4]
    pipeline.weapon_by_inst_id = {
        1005: {"inst_id": 1005, "template_string": "wpn_funnel_0005", "equip_char_id": 4},
        1014: {"inst_id": 1014, "template_string": "wpn_funnel_0014", "equip_char_id": 0},
    }
    message = SimpleNamespace(
        charid=4,
        weaponid=1014,
        offweaponid=1005,
        put_off_charid=0,
    )

    pipeline._update_weapon_puton(message, 1234)

    assert pelica["weapon_id"] == 1014
    assert pelica["weapon_source_skills"] == []
    assert pelica["skill_int_ids"] == [98]
    assert pipeline.weapon_by_inst_id[1014]["equip_char_id"] == 4
    assert pipeline.weapon_by_inst_id[1005]["equip_char_id"] == 0


def test_loadout_refinement_uses_gem_bound_to_current_weapon():
    pipeline = _pipeline_shell()
    char = _char(3, 'chr_9000_endmin')
    char['weapon_id'] = 91
    char['weapon_source_skills'] = [_weapon_source(3759, 'sk_wpn_sword_0026', potential=3)]
    char['weapon_source_skills'][0]['level'] = 4
    pipeline.char_bag_by_objid = {3: char}
    pipeline.current_team_char_ids = [3]
    pipeline.weapon_by_inst_id = {91: {'template_string': 'wpn_sword_0026', 'sync_source': 'template_only', 'attach_gem_id': 42}}
    pipeline.gem_by_inst_id = {42: {'gem_id': 42, 'weapon_id': 91, 'terms': [{'term_num_id': 66, 'cost': 3}]}}
    assert pipeline._loadout_rows()[0]['refine'] == 0
    pipeline.gem_by_inst_id[42]['weapon_id'] = 92
    assert pipeline._loadout_rows()[0]['refine'] is None


@pytest.mark.parametrize('kind', ['SC_WEAPON_ATTACH_GEM', 'SC_WEAPON_DETACH_GEM', 'term_update'])
def test_gem_change_invalidates_previous_weapon_skill_totals(kind):
    pipeline = _pipeline_shell()
    char = _char(3, 'chr_9000_endmin')
    char['weapon_id'] = 91
    char['weapon_source_skills'] = [_weapon_source(3759, 'sk_wpn_sword_0026', potential=3)]
    char['weapon_source_skills'][0]['level'] = 4
    pipeline.char_bag_by_objid = {3: char}
    pipeline.current_team_char_ids = [3]
    pipeline.weapon_by_inst_id = {91: {'template_string': 'wpn_sword_0026', 'sync_source': 'template_only', 'attach_gem_id': 42}}
    pipeline.gem_by_inst_id = {42: {'gem_id': 42, 'weapon_id': 91, 'terms': [{'term_num_id': 66, 'cost': 3}]},
                              43: {'gem_id': 43, 'weapon_id': 0, 'terms': [{'term_num_id': 66, 'cost': 2}]}}
    if kind == 'term_update':
        pipeline._index_gem_payload({'gem_id': 42, 'weapon_id': 91, 'terms': [{'term_num_id': 66, 'cost': 2}]})
    else:
        pipeline._update_weapon_delta(kind, SimpleNamespace(weaponid=91, gemid=43, detach_gemid=42), 100)
    assert char['weapon_source_skills'] == []
    assert pipeline._loadout_rows()[0]['refine'] is None
