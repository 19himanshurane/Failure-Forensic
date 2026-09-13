"""Record real responses once, replay them forever.

This is the "cassette" pattern from HTTP testing. It buys two things:

  cost   -> you pay for a response once, then reuse it indefinitely.
  truth  -> Phase 5 asks "is this known failure still failing?". With a live
            model, a passing re-run might just be a different sample. Replay
            makes the model side of the pipeline constant, so a change in
            behaviour can only have come from a change in *your* code.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import LLMClient, LLMRequest, LLMResponse


class CassetteMiss(KeyError):
    """No recording exists for this request and recording is disabled."""


class ReplayClient:
    def __init__(self, cassette_dir: str | Path, inner: LLMClient | None = None,
                 record: bool = False, model: str = "replay") -> None:
        # The model name is part of the cache key, so recording and replaying
        # must agree on it. Taking it from the inner client when recording, and
        # from config when replaying, keeps the two runs consistent.
        self.model = getattr(inner, "model", None) or model
        self.dir = Path(cassette_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.inner = inner        # the live client, used only on a miss
        self.record = record
        self.hits = 0
        self.misses = 0

    def _path(self, request: LLMRequest) -> Path:
        return self.dir / f"{request.tag}-{request.cache_key()}.json"

    def complete(self, request: LLMRequest) -> LLMResponse:
        request = request.model_copy(update={"model": self.model})
        path = self._path(request)

        if path.exists():
            self.hits += 1
            stored = json.loads(path.read_text(encoding="utf-8"))
            # Mark the source as replay so a span never claims a cached answer
            # was a live call. Traces must not lie about their own provenance.
            return LLMResponse(**{**stored["response"], "source": "replay"})

        self.misses += 1
        if not (self.record and self.inner):
            # A miss is usually one of two things: nothing recorded yet, or a
            # changed prompt/model. Saying which cassettes DO exist for this step
            # turns a puzzling failure into an obvious one.
            existing = sorted(p.name for p in self.dir.glob(f"{request.tag}-*.json"))
            hint = (
                f"{len(existing)} cassette(s) exist for this step: {existing[:3]}"
                if existing else "no cassettes exist for this step at all"
            )
            raise CassetteMiss(
                f"no cassette for {request.tag}/{request.cache_key()} "
                f"(model={request.model!r}) in {self.dir}. {hint}. "
                "Either the prompt/model changed, or you need to record: "
                "set FF_LLM_MODE=replay FF_LLM_RECORD=1 with a live provider."
            )

        response = self.inner.complete(request)
        path.write_text(
            json.dumps(
                {"request": request.model_dump(), "response": response.model_dump()},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return response
