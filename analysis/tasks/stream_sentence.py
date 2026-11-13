"""The sentence task on streamed PCM chunks: a registered stub in v5.

No speech metric is defined yet, so none is guessed: run() returns a failed result.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Dict

from analysis.session.models import TaskContext, TaskResult
from analysis.tasks import stream_base


class StreamSentence:
    name = "sentence"

    def definition(self) -> Dict[str, Any]:
        return stream_base.build_definition("sentence", None, None)

    async def run(self, context: TaskContext, samples: AsyncIterator[Dict[str, Any]],
                  emit_observation: Callable[[Any], Any]) -> TaskResult:
        return stream_base.stub_result("sentence", context)
