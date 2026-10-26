"""Frozen preview entry; separate from the capture/uploader launcher."""
import json
import os
from pathlib import Path
import sys
import traceback

if __name__ == "__main__":
    try:
        from endfield_pcap.overlay_preview import main
        raise SystemExit(main())
    except Exception:
        report = {"ok": False, "error": traceback.format_exc()}
        if "--result" in sys.argv and sys.argv.index("--result") + 1 < len(sys.argv):
            output = Path(sys.argv[sys.argv.index("--result") + 1])
        else:
            output = Path(os.environ.get("TEMP") or Path(sys.executable).parent) / "endfield-overlay-preview-error.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if sys.stderr:
            sys.stderr.write(report["error"])
        raise SystemExit(1)
