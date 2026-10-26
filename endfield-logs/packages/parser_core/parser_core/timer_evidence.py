"""Official challenge timing, with packet identity/lifecycle checks only.

Receipt wall time, simulation ticks and server passTime are separate clocks.
None is estimated from another. GameInstTimeFreezeKey.ChallengeTime is 0 in
the version-bound current native enum; other time keys are kept separately.
"""
from __future__ import annotations

import re
from typing import Any, Callable

START_SOURCE = 'SC_GAME_MECHANICS_SYNC_CHALLENGE_START'
END_SOURCES = {'SC_GAME_MECHANICS_SYNC_CHALLENGE_COMPLETE', 'SC_SYNC_WEEK_RAID_SETTLEMENT'}
FREEZE_SOURCE = 'SC_GAME_MECHANICS_MODIFY_INST_TIME_FREEZE'


def collect_timer_evidence(text: str, parse_fields: Callable, parse_time: Callable,
                           observed_times: dict[int, int] | None = None) -> dict[str, Any] | None:
    state = None
    for line_no, raw in enumerate(text.splitlines(), 1):
        if not any(label in raw for label in ('OFFICIAL_TIMER_', 'GAME_TIMER_', 'GAME_INST_')) and 'GAME_MECHANICS_TIME_FREEZE' not in raw:
            continue
        fields = parse_fields(raw)
        game_id = fields.get('gameId') or fields.get('game_id')
        stamp = observed_times.get(line_no) if observed_times is not None else parse_time(raw)
        if 'GAME_TIMER_RESET ' in raw and fields.get('source') == 'CAPTURE_SESSION_START':
            if state is not None:
                state = {'version': 1, 'start': None, 'complete': None, 'events': [], 'conflicts': []}
            continue
        if 'OFFICIAL_TIMER_START ' in raw:
            start = {'line': line_no, 'observedMs': stamp, 'gameId': game_id,
                     'source': fields.get('source'), 'challengeStartTs': fields.get('challengeStartTs')}
            prior = (state or {}).get('start') or {}
            if (prior.get('gameId') == game_id and start['challengeStartTs'] not in (None, '', '0')
                    and prior.get('challengeStartTs') == start['challengeStartTs'] and prior.get('source') == start['source']
                    and not any(event.get('kind') == 'boundary' for event in state['events'])):
                continue  # exact repeated challenge identity, not a time-proximity merge
            state = {'version': 1, 'start': start, 'complete': None, 'events': [], 'conflicts': []}
            continue
        if 'OFFICIAL_TIMER_END ' in raw:
            if state is not None and state.get('start') and game_id != state['start'].get('gameId'):
                continue  # another game's result cannot close this challenge
            if state is None:
                state = {'version': 1, 'start': None, 'complete': None, 'events': [], 'conflicts': []}
            complete = {'line': line_no, 'observedMs': stamp, 'gameId': game_id,
                        'source': fields.get('source'), 'isPass': fields.get('isPass'),
                        'passTimeMs': fields.get('passTime')}
            if state['complete'] is None:
                state['complete'] = complete
            elif any(state['complete'].get(k) != complete.get(k) for k in ('gameId', 'source', 'isPass', 'passTimeMs')):
                state['conflicts'].append('conflicting_official_completion')
            continue
        if state is None or state['complete'] is not None:
            continue  # no carry from another challenge or its post-completion cleanup
        event = {'line': line_no, 'observedMs': stamp, 'gameId': game_id, 'source': fields.get('source')}
        if 'GAME_MECHANICS_TIME_FREEZE ' in raw:
            event.update(kind='freeze', count=fields.get('count'), freezeInfos=fields.get('freezeInfos'))
        elif any(label in raw for label in ('GAME_TIMER_RESET ', 'GAME_INST_RESTART ', 'GAME_INST_LEAVE ')):
            event['kind'] = 'boundary'
        elif 'GAME_TIMER_START ' in raw:
            event.update(kind='battle_start', startMs=fields.get('startMs'))
        elif 'GAME_TIMER_END ' in raw:
            event.update(kind='battle_end', startMs=fields.get('startMs'), endMs=fields.get('endMs'),
                         packetElapsedMs=fields.get('packetElapsedMs'))
        else:
            continue
        state['events'].append(event)
    return state


def validate_timer_evidence(evidence: Any, *, duration_ms: int, game_id: str | None) -> list[str]:
    if not isinstance(evidence, dict) or type(evidence.get('version')) is not int or evidence.get('version') != 1:
        return ['missing_official_timer_evidence']
    start, complete = evidence.get('start'), evidence.get('complete')
    if not isinstance(start, dict):
        return ['missing_official_start']
    if not isinstance(complete, dict):
        return ['missing_official_completion']
    errors = []
    if start.get('source') != START_SOURCE or complete.get('source') not in END_SOURCES:
        errors.append('unsupported_official_timer_source')
    start_epoch = start.get('challengeStartTs')
    if not isinstance(start_epoch, str) or not re.fullmatch(r'[0-9]+', start_epoch) or int(start_epoch) <= 0:
        errors.append('missing_official_challenge_identity')
    if (not isinstance(start.get('gameId'), str) or not start['gameId']
            or complete.get('gameId') != start['gameId'] or game_id != start['gameId']):
        errors.append('official_timer_game_id_mismatch')
    if (type(start.get('line')) is not int or type(complete.get('line')) is not int
            or not 0 < start['line'] < complete['line']):
        return sorted(set(errors + ['official_timer_event_order_invalid']))
    if complete.get('isPass') != '1':
        errors.append('official_challenge_not_passed')
    raw_duration = complete.get('passTimeMs')
    if not isinstance(raw_duration, str) or not re.fullmatch(r'[0-9]+', raw_duration) or int(raw_duration) <= 0:
        errors.append('invalid_official_duration')
    elif type(duration_ms) is not int or duration_ms != int(raw_duration):
        errors.append('official_duration_payload_mismatch')
    conflicts = evidence.get('conflicts')
    if not isinstance(conflicts, list) or conflicts:
        errors.append('conflicting_official_timer_events')
    events = evidence.get('events')
    if not isinstance(events, list):
        return sorted(set(errors + ['invalid_timer_events']))
    previous_line = start['line']
    freeze_by_key: dict[int, int] = {}
    for event in events:
        if (not isinstance(event, dict) or type(event.get('line')) is not int
                or not previous_line < event['line'] < complete['line']):
            errors.append('official_timer_event_order_invalid')
            continue
        previous_line = event['line']
        if event.get('kind') == 'boundary':
            if event.get('gameId') in (None, '', start['gameId']):
                errors.append('official_challenge_interrupted')
        elif event.get('kind') == 'freeze':
            if event.get('gameId') != start['gameId']:
                continue  # explicitly different game, never attach it to this timer
            raw = event.get('freezeInfos')
            count = event.get('count')
            if raw is None and count == '0':
                raw = '[]'  # explicit empty update, not an assumed zero counter
            if (event.get('source') != FREEZE_SOURCE or not isinstance(raw, str)
                    or not re.fullmatch(r'\[(?:[0-9]+:[0-9]+(?:,[0-9]+:[0-9]+)*)?\]', raw)
                    or not isinstance(count, str) or not count.isdecimal()):
                errors.append('invalid_official_freeze_record')
                continue
            pairs = [tuple(map(int, token.split(':'))) for token in raw[1:-1].split(',') if token]
            if len(pairs) != int(count) or len({key for key, _ in pairs}) != len(pairs):
                errors.append('conflicting_official_freeze_record')
                continue
            for key, value in pairs:
                if key in freeze_by_key and value < freeze_by_key[key]:
                    errors.append('official_freeze_counter_regressed')
                freeze_by_key[key] = value
        elif event.get('kind') == 'battle_end' and event.get('source') == 'PacketBattleState':
            values = [event.get(key) for key in ('startMs', 'endMs', 'packetElapsedMs')]
            if all(isinstance(value, str) and value.isdecimal() for value in values):
                begin, end, elapsed = map(int, values)
                if end < begin or end - begin != elapsed:
                    errors.append('packet_stage_timer_arithmetic_conflict')
        elif event.get('kind') not in ('battle_start', 'battle_end'):
            errors.append('unsupported_timer_event')
    return sorted(set(errors))
