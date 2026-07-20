from __future__ import annotations

from constellation_node_sdk.orchestrator.retry import RetryPolicy, should_retry


class GateClientError(Exception):
    pass


class NonRetryableError(Exception):
    pass


def test_retry_policy_delay_is_capped() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        initial_delay_seconds=0.5,
        backoff_multiplier=2.0,
        max_delay_seconds=1.25,
    )

    assert policy.delay_for_attempt(1) == 0.5
    assert policy.delay_for_attempt(2) == 1.0
    assert policy.delay_for_attempt(3) == 1.25
    assert policy.delay_for_attempt(4) == 1.25


def test_should_retry_returns_true_for_retryable_error_before_limit() -> None:
    policy = RetryPolicy(
        max_attempts=3,
        retryable_error_types=("GateClientError", "TimeoutError"),
    )

    assert (
        should_retry(
            attempt=1,
            error=GateClientError("temporary failure"),
            policy=policy,
        )
        is True
    )


def test_should_retry_returns_false_for_non_retryable_error_or_limit() -> None:
    policy = RetryPolicy(
        max_attempts=2,
        retryable_error_types=("GateClientError",),
    )

    assert (
        should_retry(
            attempt=1,
            error=NonRetryableError("hard failure"),
            policy=policy,
        )
        is False
    )

    assert (
        should_retry(
            attempt=2,
            error=GateClientError("retry budget exhausted"),
            policy=policy,
        )
        is False
    )
