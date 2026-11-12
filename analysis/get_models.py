"""Download the MediaPipe Face Landmarker model file (once).

Run from the repo root:  python -m analysis.get_models
"""
from __future__ import annotations

import urllib.request

from analysis import config


def main() -> int:
    path = config.MODEL_PATH
    if path.exists():
        print("Model already present: %s" % path)
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    print("Downloading %s" % config.MODEL_URL)
    try:
        urllib.request.urlretrieve(config.MODEL_URL, str(tmp))
    except Exception as exc:  # network errors vary; report plainly
        print("Download failed: %s" % exc)
        if tmp.exists():
            tmp.unlink()
        return 1
    tmp.replace(path)
    print("Saved to %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
