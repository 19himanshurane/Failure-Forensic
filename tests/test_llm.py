"""Tests for the model seam: parsing tolerance, cache keys, mock honesty, replay."""

import json
from datetime import datetime

import pytest

from forensics.llm import (
    CassetteMiss, LLMRequest, MalformedResponse, MockLLM, ReplayClient, extract_json,
)
from forensics.models import Document, ExtractionResult


def req(tag="extraction", user="hello", **kw):
    return LLMRequest(tag=tag, system="sys", user=user, **kw)


# -- extract_json: tolerate sloppiness, reject nonsense ----------------------

def test_plain_json_parses():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_markdown_fenced_json_parses():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_preamble_and_trailing_prose_are_stripped():
    text = 'Sure! Here you go:\n{"a": 1}\nHope that helps.'
    assert extract_json(text) == {"a": 1}


def test_response_with_no_json_raises():
    with pytest.raises(MalformedResponse):
        extract_json("I'm sorry, I can't help with that.")


def test_json_array_is_rejected_because_we_asked_for_an_object():
    with pytest.raises(MalformedResponse):
        extract_json("[1, 2, 3]")


# -- cache keys: the property replay depends on -----------------------------

def test_identical_requests_share_a_cache_key():
    assert req().cache_key() == req().cache_key()


def test_changing_the_prompt_changes_the_cache_key():
    """A prompt edit must miss the cache, or Phase 5 would replay stale answers."""
    assert req(user="a").cache_key() != req(user="b").cache_key()


# -- the mock is mediocre but never fabricates evidence ---------------------

TEXT = """INVOICE 2201
Bill to: Globex Ltd
Amount due: $4,500.00 and €300 handling.
Payment terms: net 30. Due 2026-04-01.
"""


def test_mock_output_parses_into_the_real_model():
    """The mock must satisfy the same schema as a live model, or it tests nothing."""
    raw = MockLLM().complete(req(user=TEXT)).text
    result = ExtractionResult(**extract_json(raw))
    assert result.confidence >= 1


def test_every_mock_entity_is_grounded_in_the_source():
    doc = Document(doc_id="d", source_name="i.txt", raw_text=TEXT, ingested_at=datetime(2026, 1, 1))
    result = ExtractionResult(**extract_json(MockLLM().complete(req(user=TEXT)).text))
    assert result.ungrounded(doc) == ()


def test_mock_finds_the_mixed_currencies():
    result = ExtractionResult(**extract_json(MockLLM().complete(req(user=TEXT)).text))
    assert result.currencies() == {"USD", "EUR"}


def test_mock_is_deterministic():
    a = MockLLM().complete(req(user=TEXT)).text
    b = MockLLM().complete(req(user=TEXT)).text
    assert a == b


# -- chaos mode: known failures, so the Phase 3 detectors can be tested -----

def test_chaos_hallucinate_produces_a_detectable_ungrounded_entity():
    doc = Document(doc_id="d", source_name="i.txt", raw_text=TEXT, ingested_at=datetime(2026, 1, 1))
    raw = MockLLM(chaos={"hallucinate"}).complete(req(user=TEXT)).text
    result = ExtractionResult(**extract_json(raw))
    assert [e.value for e in result.ungrounded(doc)] == ["John Smith"]


def test_chaos_bad_json_is_unparseable():
    raw = MockLLM(chaos={"bad_json"}).complete(req(user=TEXT)).text
    with pytest.raises(MalformedResponse):
        extract_json(raw)


# -- replay ------------------------------------------------------------------

def test_replay_miss_without_recording_raises(tmp_path):
    with pytest.raises(CassetteMiss):
        ReplayClient(tmp_path).complete(req())


def test_replaying_with_a_different_model_is_a_miss(tmp_path):
    """A cassette recorded from one model must not be served for another."""
    ReplayClient(tmp_path, inner=MockLLM(), record=True).complete(req(user=TEXT))
    with pytest.raises(CassetteMiss, match="llama"):
        ReplayClient(tmp_path, model="llama-3.3-70b").complete(req(user=TEXT))


def test_replay_records_then_serves_from_disk(tmp_path):
    inner = MockLLM()
    recorder = ReplayClient(tmp_path, inner=inner, record=True)

    first = recorder.complete(req(user=TEXT))
    assert first.source == "mock" and recorder.misses == 1

    # Same model name as the recording: the model is part of the cache key.
    player = ReplayClient(tmp_path, model="mock")   # no inner client at all
    second = player.complete(req(user=TEXT))

    assert player.hits == 1
    assert second.text == first.text
    assert second.source == "replay"          # a trace must not claim it was live
