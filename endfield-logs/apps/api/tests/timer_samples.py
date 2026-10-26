"""Explicit official packet fixtures, independent of wall-time thresholds."""


def official_timer_evidence(duration_ms: int, game_id: str) -> dict:
    return {
        'version': 1,
        'start': {'line': 1, 'observedMs': 0, 'gameId': game_id,
                  'source': 'SC_GAME_MECHANICS_SYNC_CHALLENGE_START', 'challengeStartTs': '1000'},
        'events': [{'line': 2, 'observedMs': 1000, 'gameId': game_id, 'kind': 'freeze',
                    'source': 'SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE', 'count': '1', 'freezeInfos': '[0:2000]'}],
        'complete': {'line': 3, 'observedMs': duration_ms + 2000, 'gameId': game_id,
                     'source': 'SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE', 'isPass': '1', 'passTimeMs': str(duration_ms)},
        'conflicts': [],
    }
