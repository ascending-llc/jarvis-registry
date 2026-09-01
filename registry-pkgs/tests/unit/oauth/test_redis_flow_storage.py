"""Unit tests for atomic Redis OAuth flow consumption."""

from unittest.mock import ANY, Mock

from registry_pkgs.oauth.redis_flow_storage import RedisFlowStorage
from registry_pkgs.oauth.schemas import OAuthFlow


def test_consume_flow_reads_and_deletes_in_one_transaction() -> None:
    redis = Mock()
    redis.eval.return_value = ["flow_id", "flow-1"]
    storage = RedisFlowStorage(redis, redis_key_prefix="jarvis-registry")
    expected_flow = Mock(spec=OAuthFlow)
    storage._deserialize_flow = Mock(return_value=expected_flow)

    result = storage.consume_flow("flow-1", "expected-state")

    assert result is expected_flow
    redis.eval.assert_called_once_with(
        ANY,
        1,
        storage._make_key("flow-1"),
        "expected-state",
        "pending",
    )
    storage._deserialize_flow.assert_called_once_with({"flow_id": "flow-1"})


def test_consume_flow_returns_none_when_another_request_consumed_it_first() -> None:
    redis = Mock()
    redis.eval.return_value = []
    storage = RedisFlowStorage(redis, redis_key_prefix="jarvis-registry")

    assert storage.consume_flow("flow-1", "expected-state") is None


def test_consume_flow_returns_none_when_transaction_fails() -> None:
    redis = Mock()
    redis.eval.side_effect = RuntimeError("Redis unavailable")
    storage = RedisFlowStorage(redis, redis_key_prefix="jarvis-registry")

    assert storage.consume_flow("flow-1", "expected-state") is None
