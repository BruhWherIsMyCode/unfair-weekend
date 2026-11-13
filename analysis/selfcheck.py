"""Start-up self-check of the measuring (v11): can this Python really measure a face and a hand?

    python -m analysis.selfcheck [--claude subscription|api]

Checks that mediapipe, cv2 and numpy import and that both model files are present, prints one line per item and then
"measuring ready" or the fix command; exit code 1 when measuring is not ready. With `--claude subscription` it also
checks that `claude_agent_sdk` imports, with `--claude api` that `anthropic` imports (and names the pip package to
install); a missing Claude package is reported on its own line and also gives exit code 1.

`check()` is the same as a function: it returns the plain problems (an empty list = ready) and prints nothing. The
backend calls it at start-up to choose between the real analysis and the scripted call. No network, no camera.
"""
from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import Any, Callable, List, NamedTuple, Optional, Sequence, Tuple

from analysis import config

FIX_PACKAGES = "uv pip install -r analysis/requirements.txt"
FIX_MODELS = "python -m analysis.get_models"
# (module to import, pip package to name in the problem)
MEASURING_MODULES = (("mediapipe", "mediapipe"), ("cv2", "opencv-python"), ("numpy", "numpy"))
CLAUDE_MODULES = {"subscription": ("claude_agent_sdk", "claude-agent-sdk"), "api": ("anthropic", "anthropic")}


class Item(NamedTuple):
    label: str
    ok: bool
    problem: str  # plain problem with its fix, used when not ok
    fix: str
    group: str  # "measuring" or "claude"


def _items(claude: Optional[str], import_module: Callable[[str], Any],
           model_paths: Sequence[Tuple[str, Path]]) -> List[Item]:
    items: List[Item] = []
    for module, package in MEASURING_MODULES:
        try:
            import_module(module)
            ok = True
        except Exception:  # a missing or broken install: both mean the same for measuring
            ok = False
        fix = "%s (package %s)" % (FIX_PACKAGES, package)
        items.append(Item("%s importable" % module, ok, "%s cannot be imported. Fix: %s" % (module, fix), fix,
                          "measuring"))
    for name, path in model_paths:
        items.append(Item("%s model file present" % name, Path(path).exists(),
                          "the %s model file is missing (%s). Fix: %s" % (name, path, FIX_MODELS), FIX_MODELS,
                          "measuring"))
    if claude is not None:
        if claude not in CLAUDE_MODULES:
            raise ValueError("claude must be subscription or api, got %r" % (claude,))
        module, package = CLAUDE_MODULES[claude]
        try:
            import_module(module)
            ok = True
        except Exception:
            ok = False
        fix = "uv pip install %s" % package
        items.append(Item("%s importable (Claude %s)" % (module, claude), ok,
                          "%s cannot be imported. Fix: %s" % (module, fix), fix, "claude"))
    return items


def _model_paths() -> List[Tuple[str, Path]]:
    return [("face", config.MODEL_PATH), ("hand", config.HAND_MODEL_PATH)]


def check(claude: Optional[str] = None, import_module: Callable[[str], Any] = importlib.import_module,
          model_paths: Optional[Sequence[Tuple[str, Path]]] = None) -> List[str]:
    """The plain problems that stop measuring (and, with `claude`, that Claude). Empty = ready."""
    paths = _model_paths() if model_paths is None else model_paths
    return [i.problem for i in _items(claude, import_module, paths) if not i.ok]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.selfcheck", description=__doc__.split("\n")[0])
    parser.add_argument("--claude", choices=sorted(CLAUDE_MODULES), default=None,
                        help="also check the packages Claude needs (subscription: claude_agent_sdk, api: anthropic)")
    args = parser.parse_args(argv)
    items = _items(args.claude, importlib.import_module, _model_paths())
    for i in items:
        print("%-46s %s" % (i.label, "ok" if i.ok else "MISSING"))
    bad = [i for i in items if not i.ok]
    if not any(i.group == "measuring" for i in bad):
        print("measuring ready")
    else:
        print("measuring NOT ready. Fix:")
        for fix in dict.fromkeys(i.fix for i in bad if i.group == "measuring"):
            print("  " + fix)
    for fix in dict.fromkeys(i.fix for i in bad if i.group == "claude"):
        print("Claude NOT ready. Fix:\n  " + fix)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
