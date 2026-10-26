from pathlib import Path

from endfield_pcap.packet_resolver import PacketResolveContext, PacketResolver


def test_numeric_namespace_does_not_use_wulfa_fingerprints() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    resolver = PacketResolver(root=repo_root)

    assert resolver.resolve_skill(int_id=2257) == "chr_0028_wulfa_combo_2_skill"
    assert resolver.resolve_skill(int_id=2258) == "chr_0028_wulfa_combo_3_skill"
    # These numbers exist in both namespaces. The packet's template type,
    # not a character fingerprint, selects which table to read.
    assert resolver.resolve_skill(int_id=2017) == "chr_0030_zhuangfy_plunging_attack_end"
    assert resolver.resolve_buff(int_id=2017) == "buff_chr_0028_wulfa_combo_2_damage"
    assert resolver.resolve_skill(int_id=633) == "chr_0011_seraph_dodge"
    assert resolver.resolve_buff(int_id=633) == "buff_physical_airborne"


def test_current_static_buff_table_overlays_stale_resolver_bundle() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    resolver = PacketResolver(root=repo_root)
    stale_loadout = PacketResolveContext(
        owner="eny_0063_agmelee2",
        src="chr_0032_lizhiyan",
        active_weapons_by_char={"chr_0027_tangtang": "wpn_pistol_0011"},
    )

    assert resolver.resolve_buff(int_id=3724, context=stale_loadout) == "buff_wpn_funnel_0016_will_dmg"
    assert resolver.resolve_buff(int_id=3694, context=stale_loadout) == "buff_chr_0032_lizhiyan_combo_skill_precheck"
    assert resolver.resolve_buff(int_id=1407, context=stale_loadout) == "buff_equipsuit_combosuit_01_adddamage"
