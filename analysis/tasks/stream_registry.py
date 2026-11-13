"""The streamed task modules by name, for the call session."""
from __future__ import annotations

from typing import Any, Dict

from analysis.tasks.stream_arms import StreamArms
from analysis.tasks.stream_sentence import StreamSentence
from analysis.tasks.stream_smile import StreamSmile

STREAM_TASKS: Dict[str, Any] = {
    "smile": StreamSmile(),
    "arms": StreamArms(),
    "sentence": StreamSentence(),
}
