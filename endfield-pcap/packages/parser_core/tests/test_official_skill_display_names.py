"""Names use explicit official identities without altering combat grouping."""
import json

import pytest

from parser_core import battle_log_parser as parser


@pytest.mark.parametrize('key,expected', [
    ('chr_0007_ikut_ultimate_skill_abentity', '轰雷掣电'),
    ('buff_chr_0030_zhuangfy_sword_triggerd', '惊霆诀'),
    ('chr_0030_zhuangfy_attack2_abilityrange', '疾电法'),
    ('chr_0030_zhuangfy_attack5_abilityrange', '疾电法'),
    ('chr_0030_zhuangfy_attack1_projhit', '疾电法'),
    ('chr_0030_zhuangfy_attack2_sword_projhit', '疾电法'),
    ('chr_0030_zhuangfy_attack1_ult_3_abilityrange', '万钧风雷'),
    ('chr_0035_liino_normal_skill_projhit_02', '闪耀焦点'),
    ('chr_0035_liino_normal_skill_projhit_hit', '闪耀焦点'),
    ('chr_0035_liino_ultimate_skill_soundwave_projhit', '晨星的协奏曲'),
    ('chr_0035_liino_combo_skill', '悦心音调'),
    ('chr_0030_zhuangfy_attack1_ult_2_abilityrange', '万钧风雷'),
    ('chr_0030_zhuangfy_normal_skill_ult_abilityrange', '惊霆诀'),
    ('chr_0004_pelica_combo_skill_projhit', '即时协议·闪链'),
    ('chr_0004_pelica_attack4_projhit', '协议α·突破'),
])
def test_exact_reviewed_display_name(key, expected):
    assert parser._resolve_skill_name(key) == expected


def test_unlisted_similar_spelling_does_not_inherit_a_name():
    assert parser._resolve_skill_name('chr_0035_liino_normal_skill_projhit_unreviewed_fixture') is None


@pytest.mark.parametrize('mode', ['valid', 'version_mismatch', 'conflict', 'invalid_entries'])
def test_official_display_catalog_validation_and_legacy_fallback(monkeypatch, tmp_path, mode):
    catalog = {'schemaVersion': 1, 'source': 'official_CharGrowth_I18n_typed_skill_display',
               'decoder': dict(parser._CURRENT_TYPED_RESOURCE_BINDING),
               'entries': {'fixture_skill': {'displayName': '官表名称',
                                            'proofKind': 'official_char_growth_skill_list'}}, 'conflicts': {}}
    if mode == 'version_mismatch':
        catalog['decoder']['gameAssemblySha256'] = '0' * 64
    elif mode == 'conflict':
        catalog['conflicts'] = {'fixture_skill': ['first', 'second']}
    elif mode == 'invalid_entries':
        catalog['entries'] = []
    path = tmp_path / 'data/local_static/skill/display_name_catalog.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(catalog), encoding='utf8')
    with monkeypatch.context() as patch:
        patch.setattr(parser, '_repo_root', lambda: tmp_path)
        patch.setattr(parser, '_load_skill_name_prefixes', lambda: [('fixture_skill', '旧名称')])
        parser._load_skill_exact_names.cache_clear()
        expected = '官表名称' if mode == 'valid' else '' if mode == 'conflict' else '旧名称'
        assert parser._load_skill_exact_names()['fixture_skill'] == expected
        if mode == 'conflict':
            assert parser._resolve_skill_name('fixture_skill') is None
    parser._load_skill_exact_names.cache_clear()
