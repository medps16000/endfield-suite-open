# Endfield Logs Suite

Endfield Logs Suite contains the source code for the ZMDLogs website and the
unified Windows client used to capture, parse, display, and upload Endfield
battle records.

This is an independent community project. It is not affiliated with, endorsed
by, or sponsored by the game's publisher or developer.

## Repository layout

- `endfield-logs/apps/web`: Next.js website.
- `endfield-logs/apps/api`: FastAPI service and SQLAlchemy database models.
- `endfield-logs/apps/uploader`: desktop uploader UI.
- `endfield-logs/packages`: shared parser and upload-domain packages.
- `endfield-pcap`: packet capture service, parser bridge, overlay, and updater.

## What is intentionally not included

The public repository does not contain:

- private keys, production environment files, deployment credentials, or
  release-signing material;
- production databases, user logs, packet captures, email addresses, UIDs, or
  other user data;
- extracted game tables, game artwork, or mirrored third-party web bundles;
- private production operations, incident records, or anti-abuse rules.

Runtime resources must be supplied locally. See the `README.md` files inside
`endfield-logs/data`, `endfield-pcap/data`, `endfield-pcap/jsondata`, and
`endfield-pcap/secrets`.

## Quick start

### Website

```powershell
cd endfield-logs
corepack pnpm install --frozen-lockfile
corepack pnpm --dir apps/web dev
```

The development website expects the API at `http://localhost:8000` by default.

### API

```powershell
cd endfield-logs/apps/api
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Development defaults to a local SQLite database. Copy `.env.example` to `.env`
only when you need to override those defaults. Never commit `.env` files.

### Unified client

```powershell
cd endfield-pcap
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m endfield_pcap serve --rsa-key-txt C:\path\to\your\key.pem
```

Private key material is never embedded in the open-source tree. Set
`ENDFIELD_RSA_KEY_FILE` or pass `--rsa-key-txt` with a key you are authorized to
use. Optional generated metadata can be provided through
`ENDFIELD_LOGS_DATA_ROOT`.

## Security model

Client-side hashes and local seals detect accidental corruption; they do not
prove that an upload came from a trusted client. A production deployment must
validate authorization, battle invariants, replay protection, and abuse signals
on the server. Never place a server trust secret in a distributed client.

## License

Source code in this repository is available under the MIT License. Game names,
trademarks, and externally supplied runtime assets remain the property of their
respective owners and are not licensed by this repository.


## Current source snapshot

The source includes the unified client's shared runtime roles, content-addressed
client/runtime/game-data updates, bounded overlay diagnostics and renderer
recovery, and native overlay settings in the main client's sidebar. Users author
their own HTML pages; the HTML data API remains read-only. Rotation editing and
its guidance strip are currently disabled. External-browser transport is deferred.

The public checkout intentionally starts with empty game-resource adapters. The
web/API source can start without distributing game artwork or extracted tables;
resource-backed features need operator-supplied data. Source-only tests explicitly
skip checks that require those external resources, native reverse-engineering
artifacts, or private captured samples. A skipped integration test is not evidence
that the corresponding live-game behavior was verified in this checkout.

Local paths, deployment details and captured user records are excluded or replaced
with examples. Public dependency security upgrades are preserved independently
from private release packaging. See `SECURITY.md` and `THIRD_PARTY_NOTICES.md`.
