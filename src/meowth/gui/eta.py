"""Estimate translation time from recent completed work and elapsed wall time."""

import math
import time
from collections import deque


def format_duration(seconds: float) -> str:
    seconds = max(0, math.ceil(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class TranslationETA:
    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.reset()

    def reset(self):
        self.current = self.total = 0
        self.deadline = None
        self.wait_until = 0.0
        self.samples = deque(maxlen=30)

    def update(self, current: int, total: int):
        now = self.clock()
        if total != self.total or current < self.current:
            self.reset()
        current = min(max(0, current), max(0, total))
        if self.samples and current == self.current:
            return
        self.current, self.total = current, total
        # Collapse bursts of glossary/cache results, which don't predict API speed.
        if self.samples and now - self.samples[-1][0] < 0.2:
            self.samples[-1] = (now, current)
        else:
            self.samples.append((now, current))
        while len(self.samples) > 2 and self.samples[1][0] < now - 60:
            self.samples.popleft()
        elapsed = now - self.samples[0][0]
        completed = current - self.samples[0][1]
        if total > 0 and current == total:
            self.deadline = now
        elif elapsed >= 1 and completed > 0:
            self.deadline = now + (total - current) * elapsed / completed
            self.deadline += max(0.0, self.wait_until - now)

    def add_wait(self, seconds: float):
        """Include a known API pause without summing overlapping worker waits."""
        now = self.clock()
        end = now + max(0.0, seconds)
        extra = max(0.0, end - max(now, self.wait_until))
        self.wait_until = max(self.wait_until, end)
        if self.deadline is not None:
            self.deadline += extra

    def remaining(self) -> float | None:
        if self.total > 0 and self.current == self.total:
            return 0.0
        if self.deadline is None or self.deadline <= self.clock():
            # An overdue prediction is not a completed translation.
            return None
        return self.deadline - self.clock()
