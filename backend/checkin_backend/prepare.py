"""Explicit operator command to prepare fixed English voice and optional video assets."""

import argparse
import asyncio
import logging

from .avatar import ClipGateway
from .logging_config import LoggingSettings, configure_logging, log_event
from .people import Directory
from .provider_io import ProviderError
from .speech import SpeechGateway, load_script, render_line

log = logging.getLogger(__name__)


async def prepare(*, video: bool, only_line: str | None) -> int:
    person = Directory.from_environment().all()[0]
    script = load_script()
    line_ids = [only_line] if only_line else [line for line in script if line != "sentence"]
    if any(line not in script or line == "sentence" for line in line_ids):
        print("Choose a spoken line ID from the English script.")
        return 1
    speech = SpeechGateway.from_environment()
    clips = ClipGateway.from_environment()
    failed = 0
    try:
        for line_id in line_ids:
            try:
                line = await speech.prepare(render_line(script, line_id, person.name), require_voice=True)
                if video:
                    await clips.prepare(line.audio)
                print(f"{line_id}: voice ready" + (", video ready" if video else ""))
                log_event(log, logging.INFO, "assets.line_prepared", line=line_id, video=video)
            except (ProviderError, OSError) as exc:
                message = str(exc) if isinstance(exc, ProviderError) else "Could not read or write the local cache"
                print(f"{line_id}: {message}")
                log_event(log, logging.ERROR, "assets.line_failed", line=line_id, exc_info=True)
                failed += 1
                # A configuration/provider failure should not fan out into paid retries.
                break
    finally:
        await clips.aclose()
        await speech.aclose()
    return int(failed > 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", action="store_true", help="Also generate Simli MP4s (uses provider credits)")
    parser.add_argument("--only-line", help="Prepare just one line, e.g. greet")
    args = parser.parse_args()
    configure_logging(LoggingSettings.from_environment())
    raise SystemExit(asyncio.run(prepare(video=args.video, only_line=args.only_line)))


if __name__ == "__main__":
    main()
