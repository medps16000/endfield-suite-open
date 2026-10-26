"""Version-bound native clock policy for the independent guide observer.

Parameters come from the sealed native image/metadata/dump and explicit
instruction paths. They are not fitted to battle duration or network samples.
"""
from __future__ import annotations

import os

LEGACY_NATIVE_CLOCK_PROOF = {
    "schemaVersion": 1,
    "algorithmId": "endfield-native-observer-v1",
    "proofComplete": True,
    "entryCreatesFreshSubGame": True,
    "gameAssemblySha256": "c24495e51b406f03b03890c4788ee618ae022c991405be5d5b8b787cb775ae89",
    "metadataSha256": "0076743397acadf03d3b0064343a963c7c88863b8160526d397e4b3efb96f02e",
    "dumpSha256": "5d64c372ecf65d867885b6547284beac22e5fda10e8fa1f4ab222c834ef681c5",
    "constants": {
        "sessionThreadSleepMs": 20,
        "maxServerBiasMs": 1000,
        "loginBiasThresholdMs": 180000,
        "initialBiasMs": 0,
        "missingFreezeKeyMs": 0,
    },
    "scope": "Independent observer of the same-version native base algorithms, not the game's hidden runtime LOGIN state.",
    "methodRvas": {
        "SubGameCounting.GetTimePassedInMs": "0x6267838",
        "BaseSubGame.GetSrvTimeDeltaInMs": "0x8141e70",
        "BaseSubGame.GetFreezeTimeMsByKey": "0x8141d78",
        "BaseSubGame.ctor": "0x40bbe30",
        "DateTimeUtils.ctor": "0x3f07750",
        "DateTimeUtils.GetCurrentTimestampByMilliseconds": "0x5b99540",
        "DateTimeUtils.SetLoginServerTimeBias": "0x3bed060",
        "DateTimeUtils.SetServerTimeBias": "0x3cd0d10",
        "HGNetBaseSession.ctor": "0x49cda40",
        "HGNetSession.HandleSpecialMessagePing": "0x3cd09a0",
    },
}


NATIVE_CLOCK_PROOF = {
    "schemaVersion": 1,
    "algorithmId": "endfield-native-observer-v1",
    "proofComplete": True,
    "entryCreatesFreshSubGame": True,
    "gameAssemblySha256": "ab00f1c41e13edf9816f142b166e3c2536d5bc7707992c39e5ed52d1cc888e2c",
    "metadataSha256": "461321d9dca6a832bce3bd2390402efc7c83c145554e97a8058bb68fbec1c8ca",
    "dumpSha256": "c8a79510667dbb8191fbef78f19753d6f77700bb38e8af1f2cb5ad71d84651f6",
    "constants": {
        "sessionThreadSleepMs": 20,
        "maxServerBiasMs": 1000,
        "loginBiasThresholdMs": 180000,
        "initialBiasMs": 0,
        "missingFreezeKeyMs": 0,
    },
    "scope": "Independent observer of same-version native base algorithms; runtime IFix hooks and hidden LOGIN state are not claimed.",
    "methodRvas": {
        "SubGameCounting.GetTimePassedInMs": "0x912429c",
        "BaseSubGame.GetSrvTimeDeltaInMs": "0xb4b6fa4",
        "BaseSubGame.GetFreezeTimeMsByKey": "0xb4b6eb8",
        "BaseSubGame.ctor": "0x6adbf70",
        "DateTimeUtils.ctor": "0x684a650",
        "DateTimeUtils.GetCurrentTimestampByMilliseconds": "0x70ad310",
        "DateTimeUtils.SetLoginServerTimeBias": "0x5b69a60",
        "DateTimeUtils.SetServerTimeBias": "0x583f760",
        "HGNetBaseSession.ctor": "0x716e460",
        "HGNetSession.HandleSpecialMessagePing": "0x583f830",
    },
}

NATIVE_CLOCK_PROOFS = {
    (proof["gameAssemblySha256"], proof["metadataSha256"]): proof
    for proof in (LEGACY_NATIVE_CLOCK_PROOF, NATIVE_CLOCK_PROOF)
}


def load_rotation_clock_spec():
    from parser_core.rotation_clock import RotationClockSpec

    assembly = os.environ.get("ENDFIELD_ROTATION_GAME_ASSEMBLY_SHA256")
    metadata = os.environ.get("ENDFIELD_ROTATION_METADATA_SHA256")
    proof = NATIVE_CLOCK_PROOFS.get((assembly, metadata))
    if proof is None:
        return None
    try:
        return RotationClockSpec.from_proof(proof, expected_game_assembly_sha256=assembly)
    except (TypeError, ValueError):
        return None
