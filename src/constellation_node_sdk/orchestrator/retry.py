from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RetryPolicy(BaseModel):
    """
    Deterministic retry policy for orchestrator step execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=3, ge=1)
    initial_delay_seconds: float = Field(default=0.25, ge=0.0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)
    max_delay_seconds: float = Field(default=5.0, ge=0.0)
    retryable_error_types: tuple[str, ...] = (
        "TimeoutError",
        "TransportAuthenticationError",
        "TransportAuthorizationError",
        "TransportValidationError",
        "TransportIntegrityError",
        "GateClientError",
    )

    @field_validator("retryable_error_types")
    @classmethod
    def validate_retryable_error_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value if item.strip())
        if len(set(normalized)) != len(normalized):
            raise ValueError("retryable_error_types must not contain duplicates")
        return normalized

    def delay_for_attempt(self, attempt: int) -> float:
        """
        Return the backoff delay for the next retry after `attempt`.
        """
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        if self.max_attempts <= 1:
            return 0.0

        delay = self.initial_delay_seconds * (self.backoff_multiplier ** (attempt - 1))
        return min(delay, self.max_delay_seconds)


def should_retry(*, attempt: int, error: Exception, policy: RetryPolicy) -> bool:
    """
    Return True when the given error should be retried under the policy.

    `attempt` is 1-indexed and represents the attempt that just failed.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")

    if attempt >= policy.max_attempts:
        return False

    error_type_names = {cls.__name__ for cls in type(error).mro()}
    retryable = set(policy.retryable_error_types)
    return bool(error_type_names & retryable)
