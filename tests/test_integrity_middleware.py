"""
Integration tests for chain-hashing integrity injection in the middleware.

When ``AuditConfig.enable_integrity=True`` and a ``chain_state`` is provided,
``_safe_emit`` injects an ``integrity`` block into every event **before** it
reaches the sink.
"""

from __future__ import annotations

from bh_fastapi_audit import MemorySink
from bh_fastapi_audit._chain_state import ChainState
from tests.conftest import audit_config, build_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_integrity_app(
    *,
    algorithm: str = "sha256",
    enable_integrity: bool = True,
):
    chain = ChainState()
    sink = MemorySink()
    config = audit_config(
        enable_integrity=enable_integrity,
        chain_state=chain,
        hash_algorithm=algorithm,
    )
    client, sink, mw = build_app(config, sink)
    return client, sink, mw, chain


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIntegrityInjection:
    def test_event_gets_integrity_block(self):
        client, sink, _mw, _chain = _build_integrity_app()
        client.get("/items/42")

        assert len(sink.events) == 1
        event = sink.events[0]
        assert "integrity" in event
        assert "event_hash" in event["integrity"]
        assert event["integrity"]["hash_alg"] == "sha256"

    def test_first_event_no_prev_hash(self):
        client, sink, _mw, _chain = _build_integrity_app()
        client.get("/items/42")

        assert "prev_event_hash" not in sink.events[0]["integrity"]

    def test_chain_links_across_requests(self):
        client, sink, _mw, _chain = _build_integrity_app()
        client.get("/items/1")
        client.get("/items/2")
        client.get("/items/3")

        assert len(sink.events) == 3
        assert "prev_event_hash" not in sink.events[0]["integrity"]
        assert (
            sink.events[1]["integrity"]["prev_event_hash"]
            == sink.events[0]["integrity"]["event_hash"]
        )
        assert (
            sink.events[2]["integrity"]["prev_event_hash"]
            == sink.events[1]["integrity"]["event_hash"]
        )

    def test_custom_algorithm(self):
        client, sink, _mw, _chain = _build_integrity_app(algorithm="sha512")
        client.get("/items/42")

        integrity = sink.events[0]["integrity"]
        assert integrity["hash_alg"] == "sha512"
        assert len(integrity["event_hash"]) == 128

    def test_integrity_counter_incremented(self):
        client, _sink, mw, _chain = _build_integrity_app()
        client.get("/items/42")
        client.get("/items/43")

        snap = mw.stats.snapshot()
        assert snap["integrity_events_total"] == 2
        assert snap["chain_gaps_total"] == 0

    def test_chain_state_updated(self):
        client, sink, _mw, chain = _build_integrity_app()
        client.get("/items/42")

        assert chain.last_hash == sink.events[0]["integrity"]["event_hash"]

    def test_disabled_integrity_no_block(self):
        client, sink, _mw, _chain = _build_integrity_app(enable_integrity=False)
        client.get("/items/42")

        assert len(sink.events) == 1
        assert "integrity" not in sink.events[0]

    def test_no_chain_state_no_block(self):
        """enable_integrity=True but chain_state=None → no integrity."""
        sink = MemorySink()
        config = audit_config(enable_integrity=True, chain_state=None)
        client, sink, _mw = build_app(config, sink)
        client.get("/items/42")

        assert len(sink.events) == 1
        assert "integrity" not in sink.events[0]

    def test_chain_gap_on_state_failure(self):
        """If chain_state.last_hash raises, event still emits (gap counter up)."""
        client, sink, mw, chain = _build_integrity_app()

        class BrokenChain:
            @property
            def last_hash(self):
                raise RuntimeError("boom")

            def advance(self, h):
                pass

        # Monkey-patch the config's chain_state for this request
        object.__setattr__(mw.config, "chain_state", BrokenChain())
        client.get("/items/42")

        assert len(sink.events) == 1
        assert "integrity" not in sink.events[0]
        assert mw.stats.snapshot()["chain_gaps_total"] == 1

    def test_integrity_events_counter_zero_when_disabled(self):
        client, _sink, mw, _chain = _build_integrity_app(enable_integrity=False)
        client.get("/items/42")

        assert mw.stats.snapshot()["integrity_events_total"] == 0
