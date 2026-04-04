"""
Tests for _chain_state.py — ChainState (in-memory) and DynamoDBChainState.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from moto import mock_aws

from bh_fastapi_audit._chain_state import ChainState, DynamoDBChainState

# ---------------------------------------------------------------------------
# ChainState (in-memory, thread-safe)
# ---------------------------------------------------------------------------


class TestChainState:
    def test_initial_last_hash_is_none(self):
        cs = ChainState()
        assert cs.last_hash is None

    def test_advance_returns_none_for_first_event(self):
        cs = ChainState()
        prev = cs.advance("hash_a")
        assert prev is None

    def test_advance_returns_previous_hash(self):
        cs = ChainState()
        cs.advance("hash_a")
        prev = cs.advance("hash_b")
        assert prev == "hash_a"

    def test_last_hash_reflects_latest(self):
        cs = ChainState()
        cs.advance("hash_a")
        cs.advance("hash_b")
        assert cs.last_hash == "hash_b"

    def test_chain_of_three(self):
        cs = ChainState()
        assert cs.advance("h1") is None
        assert cs.advance("h2") == "h1"
        assert cs.advance("h3") == "h2"
        assert cs.last_hash == "h3"

    def test_thread_safety(self):
        """Concurrent advances should not lose updates."""
        cs = ChainState()
        results: list[str | None] = []
        lock = threading.Lock()

        def worker(n: int) -> None:
            for i in range(100):
                prev = cs.advance(f"t{n}_h{i}")
                with lock:
                    results.append(prev)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 400
        none_count = sum(1 for r in results if r is None)
        assert none_count == 1  # only the very first advance returns None


# ---------------------------------------------------------------------------
# DynamoDBChainState (moto-backed)
# ---------------------------------------------------------------------------

_TABLE_NAME = "bh_test_chain_state"
_SERVICE = "test-svc"


@pytest.fixture()
def chain_state():
    """Create a DynamoDBChainState backed by moto."""
    with mock_aws():
        cs = DynamoDBChainState(
            table_name=_TABLE_NAME,
            service_name=_SERVICE,
            region="us-east-1",
            create_table=True,
        )
        yield cs


class TestDynamoDBChainState:
    def test_initial_last_hash_is_none(self, chain_state):
        assert chain_state.last_hash is None

    def test_advance_first_event(self, chain_state):
        prev = chain_state.advance("hash_a")
        assert prev is None
        assert chain_state.last_hash == "hash_a"

    def test_advance_second_event(self, chain_state):
        chain_state.advance("hash_a")
        prev = chain_state.advance("hash_b")
        assert prev == "hash_a"
        assert chain_state.last_hash == "hash_b"

    def test_conditional_write_retry(self, chain_state):
        """Simulate a conditional check failure then succeed on retry."""
        chain_state.advance("hash_a")

        call_count = {"n": 0}
        original_update = chain_state._table.update_item

        def flaky_update(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                from botocore.exceptions import ClientError

                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": ""}},
                    "UpdateItem",
                )
            return original_update(**kwargs)

        with patch.object(chain_state._table, "update_item", side_effect=flaky_update):
            prev = chain_state.advance("hash_b")

        assert prev == "hash_a"

    def test_exhausted_retries_returns_none(self, chain_state):
        """When all retries fail, advance returns None (unchained)."""
        chain_state.advance("hash_a")

        from botocore.exceptions import ClientError

        error = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": ""}},
            "UpdateItem",
        )

        with patch.object(chain_state._table, "update_item", side_effect=error):
            prev = chain_state.advance("hash_b")

        assert prev is None

    def test_create_table_idempotent(self, chain_state):
        """Calling with create_table=True on an existing table doesn't error."""
        with mock_aws():
            DynamoDBChainState(
                table_name=_TABLE_NAME,
                service_name=_SERVICE,
                region="us-east-1",
                create_table=True,
            )
