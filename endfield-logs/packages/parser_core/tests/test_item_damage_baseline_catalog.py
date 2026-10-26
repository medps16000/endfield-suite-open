"""Current item card data: exact formulas, elements, source slots and boundaries.

This validates the static data declaration; integration with loadout derivation is
covered separately and is not implied by these data checks.
"""
from collections import defaultdict
import hashlib
import gzip
import json
from pathlib import Path

import pytest

from parser_core import battle_log_parser as parser


DATA = Path(__file__).resolve().parents[3] / 'data/local_static/skill'
CATALOG = json.loads((DATA / 'item_damage_baseline_catalog.json').read_text(encoding='utf8'))
ROWS = CATALOG['weaponEntries'] + CATALOG['suitEntries']


def test_damage_catalog_uses_current_typed_decoder_and_keeps_separate_legacy_arts_proof():
    arts = json.loads((DATA / 'item_arts_strength_catalog.json').read_text(encoding='utf8'))
    assert CATALOG['decoder'] == parser._CURRENT_TYPED_RESOURCE_BINDING
    # The legacy arts-native proof has its own scope; resource refresh cannot
    # relabel that proof with the current typed decoder's hashes.
    assert arts['decoder'] == {**parser._NATIVE_DAMAGE_SEMANTICS_BINDING,
                              'schemaSha256': 'da348402033bd4672bc14b1335382fb39c545987fc3d79947175932a0f8ef6d3'}
    assert CATALOG['source'] == 'current_official_item_skill_card_attribute'
    assert CATALOG['weaponCount'] == 84
    assert CATALOG['suitCount'] == 24
    assert CATALOG['scannedUniqueWeaponSkillCount'] == 123
    assert CATALOG['scannedUniqueSuitSkillCount'] == 24


@pytest.mark.parametrize('row', ROWS, ids=lambda row: f"{row['skillId']}:{row['attributeIndex']}")
def test_every_card_property_retains_typed_formula_and_exact_source(row):
    modifier = row['modifier']
    assert modifier['$type'] == 'Beyond.Gameplay.AttributeModifierData.AttributeModifier'
    assert modifier['modifyAttributeType'] == 'Specific'
    assert modifier['formulaItem'] == row['formulaItem']
    assert row['modifierType'] == {'BaseAddition': 5, 'Addition': 0}[row['formulaItem']]
    assert modifier['param']['useBlackboardKey'] is True
    assert modifier['param']['blackboardKey'] == row['bbKey']
    assert len(row['sourceSha256']) == 64
    assert row['sourceBytes'] == row['consumedBytes']
    assert 0 <= modifier['$offset'] < modifier['$end'] <= row['sourceBytes']
    assert parser._load_num_id_str_skill_map()[row['numericSkillId']] == row['skillId']
    for binding in row.get('weaponBindings', []):
        current_skills = parser._load_weapon_catalog()[binding['weaponId']]['weapon_skill_ids']
        assert current_skills[binding['skillSlot']] == row['skillId']
    if 'suitId' in row:
        assert row['requiredPieces'] == 3
        assert row['skillLevel'] == 1
        assert row['sourceSuitTableSha256'] == CATALOG['sourceTables']['EquipSuitTable']


def test_third_skill_text_groups_have_exact_card_elements_and_skill_categories():
    # Manually reviewed current Chinese text's unconditional first clauses.
    # The same "spell damage" text on generic skills has a different typed
    # element set, so it is deliberately tested separately below.
    expected = {
        'sk_wpn_claym_0004': ('phy_damage_up', {50}),
        'sk_wpn_claym_0013': ('dmg_up', {28, 32, 33}),
        'sk_wpn_claym_0016': ('phy_dmg_up', {50}),
        'sk_wpn_claym_0017': ('phy_dmg_up', {50}),
        'sk_wpn_funnel_0015': ('pulse_dmg_up', {52}),
        'sk_wpn_funnel_0018': ('nature_dmg_up', {54}),
        'sk_wpn_funnel_0011': ('nature_dmg_up', {54}),
        'sk_wpn_funnel_0019': ('spell_dmg_up', {51, 52, 53, 54}),
        'sk_wpn_lance_0006': ('dmg_up', {33}),
        'sk_wpn_lance_0007': ('fire_dmg_up', {51}),
        'sk_wpn_lance_0011': ('spell_dmg_up', {51, 52, 53, 54}),
        'sk_wpn_lance_0016': ('pulse_dmg_up', {52}),
        'sk_wpn_pistol_0004': ('dmg_up', {32}),
        'sk_wpn_pistol_0008': ('spell_dmg_up', {51, 52, 53, 54}),
        'sk_wpn_pistol_0009': ('spell_dmg_up', {51, 52, 53, 54}),
        'sk_wpn_pistol_0010': ('cryst_dmg_up', {53}),
        'sk_wpn_pistol_0011': ('cryst_dmg_up', {53}),
        'sk_wpn_sword_0006': ('fire_dmg_up', {51}),
        'sk_wpn_sword_0014': ('spell_dmg_up', {51, 52, 53, 54}),
        'sk_wpn_sword_0015': ('dmg_up', {28}),
        'sk_wpn_sword_0016': ('phy_dmg_up', {50}),
    }
    actual = defaultdict(set)
    for row in CATALOG['weaponEntries']:
        if row['skillId'].startswith('sk_wpn_'):
            actual[(row['skillId'], row['bbKey'])].add(row['attributeType'])
    assert dict(actual) == {(skill, field): attrs for skill, (field, attrs) in expected.items()}


def test_generic_second_skill_properties_are_bound_to_actual_weapon_slots():
    expected = {
        'wpn_sp_attr_crystdam_high': ('crystdam', {53}),
        'wpn_sp_attr_crystdam_mid': ('crystdam', {53}),
        'wpn_sp_attr_electrondam_high': ('electrondam', {52}),
        'wpn_sp_attr_electrondam_mid': ('electrondam', {52}),
        'wpn_sp_attr_firedam_high': ('firedam', {51}),
        'wpn_sp_attr_firedam_mid': ('firedam', {51}),
        'wpn_sp_attr_magicdam_high': ('spelldam', {51, 52, 53, 54, 55}),
        'wpn_sp_attr_magicdam_mid': ('spelldam', {51, 52, 53, 54, 55}),
        'wpn_sp_attr_magicdam_low': ('spelldam', {51, 52, 53, 54, 55}),
        'wpn_sp_attr_naturaldam_high': ('naturaldam', {54}),
        'wpn_sp_attr_phydam_high': ('phydam', {50}),
        'wpn_sp_attr_phydam_mid': ('phydam', {50}),
        'wpn_sp_attr_phydam_low': ('phydam', {50}),
    }
    actual = defaultdict(set)
    for row in CATALOG['weaponEntries']:
        if row['skillId'].startswith('wpn_sp_attr_'):
            assert all(binding['skillSlot'] == 1 for binding in row['weaponBindings'])
            actual[(row['skillId'], row['bbKey'])].add(row['attributeType'])
    assert dict(actual) == {(skill, field): attrs for skill, (field, attrs) in expected.items()}


def test_only_unconditional_suit_fields_enter_static_card_catalog():
    actual = defaultdict(set)
    for row in CATALOG['suitEntries']:
        actual[(row['suitId'], row['skillId'], row['bbKey'])].add(row['attributeType'])
    assert dict(actual) == {
        ('suit_atk01', 'passive_equipsuit_atk_01', 'dmg_up'): {28, 32, 33},
        ('suit_burst01', 'passive_equipsuit_burst_01', 'skill_dmg_up'): {28, 32, 33},
        ('suit_generaltype', 'passive_equipsuit_generaltype', 'dmg_up'): {50, 51, 52, 53, 54, 55},
        ('suit_spellburst', 'passive_equipsuit_spellburst01', 'spell_dmg_up'): {51, 52, 53, 54},
    }


def test_triggered_damage_and_secondary_attribute_placeholders_are_not_static_damage():
    keys = {(row['skillId'], row['bbKey']) for row in ROWS}
    assert ('sk_wpn_sword_0006', 'normal_atk_up') not in keys
    assert ('sk_wpn_funnel_0019', 'spell_dmg_up2') not in keys
    assert ('sk_wpn_pistol_0011', 'cryst_dmg_up2') not in keys
    assert ('sk_wpn_sword_0016', 'phy_dmg_up2') not in keys
    assert ('passive_equipsuit_burst_01', 'spell_dmg_up') not in keys
    assert ('passive_equipsuit_fninflict_01', 'fire_dmg_up') not in keys
    assert ('passive_equipsuit_fninflict_01', 'nature_dmg_up') not in keys
    assert ('sk_wpn_funnel_0008', 'second_attr_up') not in keys
    # No permanent amplification property exists in these 147 SkillData roots.
    assert not {65, 66, 67, 68} & {row['attributeType'] for row in ROWS}


def test_natural_damage_addition_keeps_its_distinct_stage():
    additions = [row for row in ROWS if row['formulaItem'] == 'Addition']
    assert len(additions) == 1
    row = additions[0]
    assert row['skillId'] == 'sk_wpn_funnel_0011'
    assert row['bbKey'] == 'nature_dmg_up'
    assert row['attributeType'] == 54
    assert row['modifierType'] == 0
    assert CATALOG['formulaSemantics']['Addition']['interchangeableWithBaseAddition'] is False
    assert CATALOG['formulaSemantics']['Addition']['stage'] == 'final_attribute_addition_input'
    assert CATALOG['formulaSemantics']['BaseAddition']['stage'] == 'base_attribute_addition'


def test_ether55_is_supported_only_by_explicit_typed_card_properties():
    ether = {(row['skillId'], row['bbKey']) for row in ROWS if row['attributeType'] == 55}
    assert ether == {
        ('wpn_sp_attr_magicdam_high', 'spelldam'),
        ('wpn_sp_attr_magicdam_mid', 'spelldam'),
        ('wpn_sp_attr_magicdam_low', 'spelldam'),
        ('passive_equipsuit_generaltype', 'dmg_up'),
    }
    assert all(row['formulaItem'] == 'BaseAddition' for row in ROWS if row['attributeType'] == 55)


def test_addition_acceptance_is_bound_to_this_version_and_exact_observed_property():
    evidence = json.loads((DATA / 'item_damage_baseline_formula_evidence.json').read_text(encoding='utf8'))
    assert evidence['decoder'] == CATALOG['decoder']
    assert evidence['attributeType'] == 54
    assert evidence['nativeStages']['generallyInterchangeable'] is False
    manifest = json.loads((DATA / 'manifest.json').read_text(encoding='utf8'))
    assert evidence['skillDataScan']['resourceCount'] == evidence['skillDataScan']['completeRootCount'] == len(manifest['entries'])
    assert evidence['skillDataScan']['errorCount'] == 0
    assert evidence['skillDataScan']['formulaCounts'] == {'BaseAddition': 13, 'Addition': 1}
    assert evidence['buffDataScan']['formulaCounts'] == {'BaseAddition': 29}
    assert evidence['nativeFormulaProofSha256'] == 'e3a0b98ab43b731546c9c3ae9401044dd8721c83d4fc921fc2478762b79e8e32'
    assert len(evidence['nativeStages']['methods']) == 5
    assert all(method['cfgComplete'] is True for method in evidence['nativeStages']['methods'])
    historical = [row for row in evidence['resourceReviews']
                  if row.get('date') == '2026-10-15' and row.get('addedBuff') == 'buff_wpn_claym_0019_teamatk']
    assert len(historical) == 1
    assert historical[0]['sourceSha256'] == 'b5d68934d59fd92106ce63629eb95d151a47fbe621bef4f0f4cac8e13bd56ae4'
    assert historical[0]['formulaItem'] == 'BaseAddition'
    review = evidence['resourceReviews'][-1]
    assert review['date'] == '2026-10-16'
    assert review['revalidatedBuff'] == 'buff_wpn_claym_0019_teamatk'
    assert review['nativeFormulaProofSha256'] == evidence['nativeFormulaProofSha256']
    assert review['changedExistingNaturalFormulas'] == 0
    buff_source = DATA.parent / 'buff/typed_roots.json.gz'
    current_roots = json.loads(gzip.decompress(buff_source.read_bytes()))['entries']
    assert evidence['buffDataScan']['resourceCount'] == evidence['buffDataScan']['completeRootCount'] == len(current_roots)
    assert hashlib.sha256(buff_source.read_bytes()).hexdigest() == evidence['buffDataScan']['sourceArtifactSha256']
    assert current_roots[review['revalidatedBuff']]['proof']['sourceSha256'] == review['revalidatedBuffSourceSha256']
    addition = next(row for row in ROWS if row['formulaItem'] == 'Addition')
    assert all(addition[key] == value for key, value in evidence['onlyAdditionProperty'].items())
