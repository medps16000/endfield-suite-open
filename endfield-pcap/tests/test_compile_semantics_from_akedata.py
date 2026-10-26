"""正向编译器（compile_semantics_from_akedata.py）回归护栏。

akedata 每次版本更新后重新生成语义表；这些测试锁住：
1) 富文本抽取的乘区/元素域/攻守侧/叠加规则判定（四个已知闭合案例）；
2) 运行时 buff id 匹配规则（套装父/子拓扑、角色 match_tokens、载体误命中）。
证据链见 docs/SIM_ENGINE_ROADMAP.md。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "infra" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from compile_semantics_from_akedata import (  # noqa: E402
    _extract_grants,
    _load_runtime_universe,
    _preprocess,
    compile_characters,
    compile_suits,
    compile_weapons,
    is_suit_carrier_child,
    match_tokens_hit,
)


@pytest.fixture(scope="module")
def compiled_effects() -> list[dict]:
    universe = _load_runtime_universe()
    effects: list[dict] = []
    unparsed: list[dict] = []
    compile_weapons(universe, effects, unparsed)
    compile_suits(universe, effects, unparsed)
    compile_characters(universe, effects, unparsed)
    return effects


def _find(effects: list[dict], **criteria) -> dict | None:
    for effect in effects:
        if all(effect.get(key) == value for key, value in criteria.items()):
            return effect
    return None


def test_weapon_vuln_funnel_0008(compiled_effects):
    effect = _find(compiled_effects, source_id="wpn_funnel_0008", value_key="spell_damage_taken_up")
    assert effect is not None
    assert effect["zone"] == "vuln_taken"
    assert effect["side"] == "defender"
    assert effect["elements"] == ["spell"]
    assert abs(effect["ladder"][2] - 0.126) < 1e-9  # refine3 档
    assert effect["no_stack"] is True  # 「同名效果无法叠加」在独立 info 句里
    ids = {entry["id"] for entry in effect["runtime"]["buff_ids"]}
    assert "buff_wpn_funnel_0008_magic_damage_taken_up" in ids  # magic↔spell 别名


def test_suit_usp02_dmg_inc(compiled_effects):
    effect = _find(compiled_effects, source_id="suit_usp02", value_key="dmg_up")
    assert effect is not None
    assert effect["zone"] == "dmg_inc"
    assert effect["scope"] == "allies_others"
    assert abs(effect["ladder"][0] - 0.16) < 1e-9


def test_suit_parent_child_topology(compiled_effects):
    """套装拓扑：父窗口=佩戴常驻标记（不接线），proc 窗=父id+值键 子 id。"""
    effect = _find(compiled_effects, source_id="suit_combo_cd01", value_key="spell_up")
    assert effect is not None
    tokens = effect["runtime"]["match_tokens"]
    assert match_tokens_hit(tokens, "buff_equipsuit_combo_cd01_spellup")
    assert not match_tokens_hit(tokens, "buff_equipsuit_combo_cd01")
    # 「所有技能伤害」不含普攻（321 实测普攻误接翻坏 13 hit）
    assert effect["skill_domains"] == ["all_skills_no_normal_attack"]
    assert is_suit_carrier_child("buff_equipsuit_usp_02", "buff_equipsuit_usp_02_dmgup")
    assert not is_suit_carrier_child("buff_equipsuit_usp_02", "buff_equipsuit_usp_02")


def test_char_amp_antal(compiled_effects):
    effect = _find(compiled_effects, source_id="chr_0023_antal", zone="amp")
    assert effect is not None
    assert set(effect["elements"]) == {"pulse", "fire"}
    assert effect["scope"] == "team"
    tokens = effect["runtime"]["match_tokens"]
    # 运行时 id 不在任何解包清单（buff 定义在未解包 ability blob），靠动态匹配
    assert match_tokens_hit(tokens, "buff_chr_0023_antal_ultimate_enhance")
    assert not match_tokens_hit(tokens, "buff_chr_0023_antal_ultimate_icon")  # 载体不误接


def test_char_exact_key_laevat(compiled_effects):
    effect = _find(compiled_effects, source_id="chr_0016_laevat", zone="res_ignore")
    assert effect is not None
    assert "fire" in effect["elements"]
    ids = {entry["id"] for entry in effect["runtime"]["buff_ids"]}
    assert "buff_chr_0016_laevat_ignore_fire_resist" in ids  # 后缀==黑板键 → exact


def test_scope_per_grant_segment():
    """一句多授予各取各的 scope（sword_0016：自身 + 小队内其他干员）。"""
    text = _preprocess(
        "装备者通过自身技能恢复技力后，自身造成的物理伤害<@ba.vup>+{a:0.0%}</>，"
        "小队内其他干员造成的物理伤害<@ba.vup>+{b:0.0%}</>，持续{duration:0}秒"
    )
    grants = _extract_grants(text)
    assert len([g for g in grants if g["via"] == "dmg_phrase"]) == 2


def test_ladder_kept_raw():
    """ladder 存原始黑板值；{100*key} 等展示变换只进 display_ladder。"""
    universe = _load_runtime_universe()
    effects: list[dict] = []
    compile_characters(universe, effects, [])
    effect = _find(effects, source_id="chr_0016_laevat", zone="res_ignore")
    assert effect is not None
    assert max(effect["ladder"]) <= 1.0  # 原始 0.1/0.2，不是显示用的 10/20 点
