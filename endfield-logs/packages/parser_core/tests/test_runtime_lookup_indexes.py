from copy import deepcopy
import threading

from parser_core import battle_log_parser as parser
from parser_core import live as live_module


def test_indexed_registry_matches_complete_scan_for_all_official_identities(monkeypatch):
    parser._load_rdps_semantics_registry.cache_clear()
    registry = parser._load_rdps_semantics_registry()
    index = parser._RDPS_VERIFIED_INDEX
    assert index is not None and index[0] is registry
    identities = {'buff_unapproved_lookup_fixture'}
    for key, entry in registry['verified_effects'].items():
        identities.add(key)
        identities.update(str(value) for value in entry.get('aliases') or [])
        identities.update(str(value) for value in entry.get('numeric_ids') or [])
        identities.update(binding['template'] for binding in entry.get('runtime_bindings') or [])
    for key in sorted(identities):
        record = {'event_key': key, 'raw_event_key': key, 'bb_values': {}}
        for guarded in (False, True):
            actual = parser._rdps_registry_matching_effect_entry(record, require_guard=guarded)
            with monkeypatch.context() as patch:
                patch.setattr(parser, '_RDPS_VERIFIED_INDEX', None)
                expected = parser._rdps_registry_matching_effect_entry(record, require_guard=guarded)
            assert actual == expected, (key, guarded)
    # A replacement registry must not inherit the current resource's index.
    isolated = deepcopy(registry)
    isolated['verified_effects'] = {}
    with monkeypatch.context() as patch:
        patch.setattr(parser, '_load_rdps_semantics_registry', lambda: isolated)
        assert parser._rdps_registry_matching_effect_entry(
            {'event_key': next(iter(registry['verified_effects']))}, require_guard=False
        ) is None


def test_cached_buff_types_match_complete_scan_and_keep_recipient_guards(monkeypatch):
    parser._load_current_buff_roots.cache_clear()
    roots = parser._load_current_buff_roots()
    assert parser._BUFF_ACTION_TYPES_INDEX[0] is roots
    for key in roots:
        for recipient in ('chr_0004_pelica', 'eny_0051_rodin'):
            record = {'event_key': key, 'target_character_key': recipient}
            actual = parser._rdps_registry_known_non_rdps_entry(record)
            with monkeypatch.context() as patch:
                patch.setattr(parser, '_BUFF_ACTION_TYPES_INDEX', None)
                expected = parser._rdps_registry_known_non_rdps_entry(record)
            assert actual == expected, (key, recipient)


def test_ui_snapshot_reads_never_run_the_full_parser(monkeypatch):
    live = live_module.LiveOverlayBattleParser()
    live.set_live_clock_enabled(False)
    live.feed_line('[10:00:00.000] DUNGEON_CONTEXT dungeonId=indie_hard013_s')
    live.feed_line('[10:00:01.000] HP_V2 #1 hit=100 src=chr_0027_tangtang tgt=eny_0051_rodin skill="chr_0027_tangtang_attack1"')
    assert live.snapshot(refresh=False) is None
    first = live.snapshot()
    assert first['battle']['total_damage'] == 100
    live.feed_line('[10:00:02.000] HP_V2 #2 hit=200 src=chr_0027_tangtang tgt=eny_0051_rodin skill="chr_0027_tangtang_attack1"')
    with monkeypatch.context() as patch:
        patch.setattr(live_module, 'parse_overlay_battle_snapshot_text',
                      lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('UI initiated replay')))
        assert live.snapshot(refresh=False) is first
    assert live.snapshot()['battle']['total_damage'] == 300


def test_background_snapshot_cannot_restore_a_battle_after_reset(monkeypatch):
    live = live_module.LiveOverlayBattleParser()
    live.set_live_clock_enabled(False)
    live.feed_line('[10:00:01.000] HP_V2 #1 hit=100 src=chr_0027_tangtang tgt=eny_0051_rodin skill="chr_0027_tangtang_attack1"')
    entered, release = threading.Event(), threading.Event()
    original = live_module.parse_overlay_battle_snapshot_text
    results = []
    def blocked(text, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(text, **kwargs)
    monkeypatch.setattr(live_module, 'parse_overlay_battle_snapshot_text', blocked)
    worker = threading.Thread(target=lambda: results.append(live.snapshot()))
    worker.start()
    try:
        assert entered.wait(3)
        live.reset()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert results == [None]
    assert live.snapshot(refresh=False) is None
