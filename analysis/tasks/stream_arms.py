"""The arms task on streamed samples: a registered stub in v5.

The owner decided the person holds the phone in one hand, so the real arms task is one free hand at a time
(analysis/hand_metric.py), while the script lines still say both arms. That is an open team decision
(contract/README.md). No both-arms metric is guessed here: run() returns a failed result.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Dict

from analysis.session.models import TaskContext, TaskResult
from analysis.tasks import stream_base


class StreamArms:
    name = "arms"

    def definition(self) -> Dict[str, Any]:
        return stream_base.build_definition("arms", None, None)

    async def run(self, context: TaskContext, samples: AsyncIterator[Dict[str, Any]],
                  emit_observation: Callable[[Any], Any]) -> TaskResult:
        return stream_base.stub_result("arms", context)
