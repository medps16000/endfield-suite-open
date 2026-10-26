import copy

from endfield_pcap.rotation_clock_proof import LEGACY_NATIVE_CLOCK_PROOF, NATIVE_CLOCK_PROOF, load_rotation_clock_spec


def test_clock_policy_requires_actual_dll_and_metadata_identity(monkeypatch):
    monkeypatch.delenv("ENDFIELD_ROTATION_GAME_ASSEMBLY_SHA256", raising=False)
    monkeypatch.delenv("ENDFIELD_ROTATION_METADATA_SHA256", raising=False)
    assert load_rotation_clock_spec() is None
    monkeypatch.setenv("ENDFIELD_ROTATION_GAME_ASSEMBLY_SHA256", NATIVE_CLOCK_PROOF["gameAssemblySha256"])
    monkeypatch.setenv("ENDFIELD_ROTATION_METADATA_SHA256", "f" * 64)
    assert load_rotation_clock_spec() is None
    monkeypatch.setenv("ENDFIELD_ROTATION_METADATA_SHA256", NATIVE_CLOCK_PROOF["metadataSha256"])
    assert load_rotation_clock_spec() is not None
    monkeypatch.setenv("ENDFIELD_ROTATION_GAME_ASSEMBLY_SHA256", "e" * 64)
    assert load_rotation_clock_spec() is None


def test_algorithm_policy_constants_cannot_silently_drift(monkeypatch):
    from parser_core.rotation_clock import RotationClockSpec
    proof = copy.deepcopy(NATIVE_CLOCK_PROOF)
    assert RotationClockSpec.from_proof(proof, expected_game_assembly_sha256=proof["gameAssemblySha256"]).session_thread_sleep_ms == 20
    assert proof["constants"] == {"sessionThreadSleepMs": 20, "maxServerBiasMs": 1000,
                                  "loginBiasThresholdMs": 180000, "initialBiasMs": 0, "missingFreezeKeyMs": 0}
    assert proof["entryCreatesFreshSubGame"] is True


def test_independently_reviewed_old_and_new_versions_cannot_be_cross_paired(monkeypatch):
    for proof in (LEGACY_NATIVE_CLOCK_PROOF, NATIVE_CLOCK_PROOF):
        monkeypatch.setenv("ENDFIELD_ROTATION_GAME_ASSEMBLY_SHA256", proof["gameAssemblySha256"])
        monkeypatch.setenv("ENDFIELD_ROTATION_METADATA_SHA256", proof["metadataSha256"])
        spec = load_rotation_clock_spec()
        assert spec is not None and spec.dump_sha256 == proof["dumpSha256"]
    monkeypatch.setenv("ENDFIELD_ROTATION_METADATA_SHA256", LEGACY_NATIVE_CLOCK_PROOF["metadataSha256"])
    assert load_rotation_clock_spec() is None


def test_new_inline_policy_matches_fresh_native_review():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'docs/evidence/rotation_clock_native_proof_20261015.json'
    proof = json.loads(path.read_text('utf-8'))
    assert proof['policy'] == NATIVE_CLOCK_PROOF
    assert proof['enterLifecycle']['newSubtype'] == 'GambleBattleGame'
    assert proof['enterLifecycle']['boundedCases'] == 9
    assert proof['enterLifecycle']['restartOrStartImpliesFreshCounter'] is False
    for method in ('Counting','LoginBias','PingBias','Factory','BaseCtor','ClockFloor'):
        assert proof['methods'][method]['cfgComplete'] is True
