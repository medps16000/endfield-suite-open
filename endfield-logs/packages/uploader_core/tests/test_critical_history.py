from uploader_core import battle_payload_builder as builder


def test_split_battles_inherit_only_prior_explicit_actor_attributes(monkeypatch, tmp_path):
    path = tmp_path / 'trace.log'
    path.write_text('\n'.join([
        '[10:00:00.000] ENTITY_STATS id=71 template=chr_test kind=character attrs=[9:0.2/0.05 10:0.5/0.5]',
        '[10:00:01.000] marker',
        '[10:00:02.000] ENTITY_ATTR_UPDATE id=71 template=chr_test attrs=[9:0.8/0.05]',
        '[10:00:03.000] marker',
        '[10:00:04.000] ENTITY_ATTR_UPDATE id=71 template=chr_test attrs=[9:0.99/0.05]',
        '[10:00:05.000] GAME_TIMER_RESET source=CAPTURE_SESSION_START',
        '[10:00:06.000] marker',
    ]), encoding='utf-8')
    segments = [{'battle_index': 1, 'start_line_index': 1}, {'battle_index': 2, 'start_line_index': 3},
                {'battle_index': 3, 'start_line_index': 6}]
    monkeypatch.setattr(builder, '_split_trace_into_battles', lambda raw: segments)
    monkeypatch.setattr(builder, '_raw_text_loadout_state', lambda raw: ([], {}))
    monkeypatch.setattr(builder, '_raw_loadout_entries_for_segment', lambda *args: [])
    monkeypatch.setattr(builder, '_is_uploadable_battle_payload', lambda payload: True)
    observations = []
    def capture(**kwargs):
        observations.append(kwargs['critical_attributes_override_by_actor'])
        return {'battle': {'battleFingerprint': str(len(observations))}}
    monkeypatch.setattr(builder, '_build_payload_from_segment', capture)
    builder.build_battle_upload_payloads_from_log(path, fast_unverified=True)
    assert observations[0]['71']['values'][9] == 0.2
    assert observations[1]['71']['values'][9] == 0.8
    assert observations[1]['71']['values'][10] == 0.5
    assert observations[2] == {}
    assert observations[0]['71']['lines'][9] <= 0
