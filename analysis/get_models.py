"""Download the MediaPipe model files (once): Face Landmarker and Hand Landmarker.

Run from the repo root:  python -m analysis.get_models
One failed download does not stop the other. Exit code is non-zero if any failed.
"""
from __future__ import annotations

import urllib.request

from analysis import config


def _fetch(name: str, path, url: str) -> bool:
    if path.exists():
        print("%s model already present: %s" % (name, path))
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    print("Downloading %s model from %s" % (name, url))
    try:
        urllib.request.urlretrieve(url, str(tmp))
    except Exception as exc:  # network errors vary; report plainly
        print("%s model download failed: %s" % (name, exc))
        if tmp.exists():
            tmp.unlink()
        return False
    tmp.replace(path)
    print("Saved to %s" % path)
    return True


def main() -> int:
    results = [
        _fetch("Face", config.MODEL_PATH, config.MODEL_URL),
        _fetch("Hand", config.HAND_MODEL_PATH, config.HAND_MODEL_URL),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
