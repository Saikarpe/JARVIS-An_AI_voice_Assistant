"""
Backend/Database.py's remember()/recall() — the cosine-similarity ranking
Phase 4.1's long-term memory feature is built on (Phase 6, see
ENHANCEMENT_PLAN.md's test priority list, item 4: "memory recall
ranking").

_get_embedder() is monkeypatched to a small deterministic bag-of-words
stand-in (FakeEmbedder below) rather than the real fastembed model — that
model is a ~130MB ONNX download on first use, which is slow and
network-touching in a way that has nothing to do with what's under test:
whether recall() correctly ranks/filters by similarity, not whether
BAAI/bge-small-en-v1.5 produces good embeddings.
"""

import numpy as np
import pytest

import Backend.Database as db

VOCAB = ["pizza", "dog", "birthday", "weather", "python", "coffee"]


class FakeEmbedder:
    """One-hot bag-of-words over VOCAB. Two texts that share a vocab word
    get a nonzero cosine similarity; texts sharing no vocab word score 0 —
    good enough to exercise recall()'s ranking/threshold logic without a
    real semantic model."""

    def embed(self, texts):
        for text in texts:
            words = set(text.lower().split())
            vec = np.array([1.0 if w in words else 0.0 for w in VOCAB], dtype="float32")
            yield vec


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch):
    monkeypatch.setattr(db, "_get_embedder", lambda: FakeEmbedder())
    db.clear_memories()
    yield
    db.clear_memories()


def test_recall_returns_the_most_similar_memory_first():
    db.remember("user loves pizza and pasta", kind="preference")
    db.remember("user has a pet dog named rex", kind="fact")
    db.remember("user's birthday is in june", kind="fact")

    results = db.recall("what pizza toppings does the user like")

    assert results
    assert "pizza" in results[0]


def test_recall_filters_out_unrelated_memories_below_min_score():
    db.remember("user has a pet dog named rex", kind="fact")

    # No shared vocab word with "dog" -> cosine similarity is exactly 0,
    # well under the default min_score=0.35 threshold.
    results = db.recall("coffee")

    assert results == []


def test_recall_respects_k_limit():
    db.remember("user loves pizza", kind="preference")
    db.remember("user loves pizza and coffee", kind="preference")
    db.remember("user talks about pizza a lot", kind="preference")

    results = db.recall("pizza", k=2)

    assert len(results) == 2


def test_recall_on_empty_memory_table_returns_empty_list():
    assert db.recall("anything") == []


def test_remember_persists_kind_and_importance():
    db.remember("user prefers python", kind="preference", importance=0.9)

    conn = db._get_connection()
    row = conn.execute(
        "SELECT kind, importance FROM memories WHERE content = ?",
        ("user prefers python",),
    ).fetchone()

    assert row["kind"] == "preference"
    assert row["importance"] == pytest.approx(0.9)


def test_clear_memories_empties_the_table():
    db.remember("user loves pizza")
    assert db.recall("pizza") != []

    db.clear_memories()

    assert db.recall("pizza") == []
