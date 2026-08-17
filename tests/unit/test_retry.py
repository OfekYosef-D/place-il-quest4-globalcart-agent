"""Tests for the transient-only LLM retry policy."""

import pytest

from app.llm.base import TransientLLMFailure
from app.llm.retry import generate_with_retry
from tests.unit.fakes import FakeProvider, final_response


def test_success_on_first_attempt_never_sleeps():
    provider = FakeProvider([final_response("done")])
    sleeps: list[float] = []

    response, attempts = generate_with_retry(
        provider, [], None, max_retries=2, backoff_seconds=0.25, sleep=sleeps.append
    )
    assert response.content == "done"
    assert attempts == 1
    assert sleeps == []


def test_transient_failures_are_retried_with_bounded_backoff():
    provider = FakeProvider(
        [TransientLLMFailure("timeout"), TransientLLMFailure("503"), final_response("recovered")]
    )
    sleeps: list[float] = []

    response, attempts = generate_with_retry(
        provider, [], None, max_retries=2, backoff_seconds=0.25, sleep=sleeps.append
    )
    assert response.content == "recovered"
    assert attempts == 3
    assert sleeps == [0.25, 0.5]


def test_exhausted_retries_reraise_last_transient_failure():
    provider = FakeProvider([TransientLLMFailure(f"failure {i}") for i in range(3)])

    with pytest.raises(TransientLLMFailure, match="failure 2"):
        generate_with_retry(
            provider, [], None, max_retries=2, backoff_seconds=0.0, sleep=lambda _: None
        )


def test_non_transient_failure_is_never_retried():
    provider = FakeProvider([ValueError("bad api key"), final_response("unreachable")])

    with pytest.raises(ValueError, match="bad api key"):
        generate_with_retry(
            provider, [], None, max_retries=2, backoff_seconds=0.0, sleep=lambda _: None
        )
    assert len(provider.calls) == 1


def test_zero_retries_means_single_attempt():
    provider = FakeProvider([TransientLLMFailure("once")])

    with pytest.raises(TransientLLMFailure):
        generate_with_retry(
            provider, [], None, max_retries=0, backoff_seconds=0.25, sleep=lambda _: None
        )
    assert len(provider.calls) == 1
