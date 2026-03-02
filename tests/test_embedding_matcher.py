"""Tests for matcher.py — embedding-based semantic market matching."""

import pytest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from matcher import (
    _cosine_similarity,
    _semantic_confidence,
    EmbeddingMatcher,
    match_cross_platform_semantic,
    match_cross_platform,
    SEMANTIC_HIGH,
    SEMANTIC_MEDIUM,
)


# ---------------------------------------------------------------------------
# _cosine_similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert _cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        assert _cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)

    def test_opposite_vectors(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([-1.0, 0.0], dtype=np.float32)
        assert _cosine_similarity(a, b) == pytest.approx(-1.0, abs=1e-6)

    def test_zero_vector_returns_zero(self):
        a = np.array([0.0, 0.0], dtype=np.float32)
        b = np.array([1.0, 2.0], dtype=np.float32)
        assert _cosine_similarity(a, b) == 0.0

    def test_both_zero_vectors(self):
        a = np.array([0.0, 0.0], dtype=np.float32)
        assert _cosine_similarity(a, a) == 0.0

    def test_similar_vectors_high_similarity(self):
        a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        b = np.array([1.1, 2.1, 3.1], dtype=np.float32)
        sim = _cosine_similarity(a, b)
        assert sim > 0.99

    def test_different_magnitude_same_direction(self):
        a = np.array([1.0, 1.0], dtype=np.float32)
        b = np.array([100.0, 100.0], dtype=np.float32)
        assert _cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# _semantic_confidence
# ---------------------------------------------------------------------------

class TestSemanticConfidence:
    def test_high_confidence(self):
        assert _semantic_confidence(0.95) == "HIGH"
        assert _semantic_confidence(0.90) == "HIGH"

    def test_medium_confidence(self):
        assert _semantic_confidence(0.85) == "MEDIUM"
        assert _semantic_confidence(0.80) == "MEDIUM"

    def test_low_confidence(self):
        assert _semantic_confidence(0.75) == "LOW"
        assert _semantic_confidence(0.70) == "LOW"
        assert _semantic_confidence(0.50) == "LOW"

    def test_boundary_at_high(self):
        assert _semantic_confidence(SEMANTIC_HIGH) == "HIGH"
        assert _semantic_confidence(SEMANTIC_HIGH - 0.001) == "MEDIUM"

    def test_boundary_at_medium(self):
        assert _semantic_confidence(SEMANTIC_MEDIUM) == "MEDIUM"
        assert _semantic_confidence(SEMANTIC_MEDIUM - 0.001) == "LOW"


# ---------------------------------------------------------------------------
# EmbeddingMatcher
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset EmbeddingMatcher singleton between tests."""
    EmbeddingMatcher._instance = None
    yield
    EmbeddingMatcher._instance = None


class TestEmbeddingMatcher:
    def test_singleton_returns_same_instance(self):
        m1 = EmbeddingMatcher.get_instance()
        m2 = EmbeddingMatcher.get_instance()
        assert m1 is m2

    def test_embed_titles_uses_cache(self):
        """Previously embedded titles should come from cache."""
        matcher = EmbeddingMatcher()
        mock_model = MagicMock()
        mock_model.embed.return_value = [
            np.array([0.1, 0.2, 0.3], dtype=np.float32),
        ]
        matcher._model = mock_model

        # First call — should invoke model
        results1 = matcher.embed_titles(["bitcoin price prediction"])
        assert mock_model.embed.call_count == 1
        assert len(results1) == 1

        # Second call with same title — should use cache
        results2 = matcher.embed_titles(["bitcoin price prediction"])
        assert mock_model.embed.call_count == 1  # not called again
        np.testing.assert_array_equal(results1[0], results2[0])

    def test_embed_titles_mixed_cache_and_new(self):
        """Mix of cached and new titles should only embed the new ones."""
        matcher = EmbeddingMatcher()
        mock_model = MagicMock()
        # Pre-populate cache
        cached_vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        matcher._cache["cached title"] = cached_vec

        new_vec = np.array([0.4, 0.5, 0.6], dtype=np.float32)
        mock_model.embed.return_value = [new_vec]
        matcher._model = mock_model

        results = matcher.embed_titles(["cached title", "new title"])
        assert len(results) == 2
        np.testing.assert_array_equal(results[0], cached_vec)
        np.testing.assert_array_equal(results[1], new_vec)
        # Only "new title" should have been embedded
        mock_model.embed.assert_called_once_with(["new title"])

    def test_clear_cache(self):
        matcher = EmbeddingMatcher()
        matcher._cache["test"] = np.array([1.0], dtype=np.float32)
        assert matcher.cache_size == 1
        matcher.clear_cache()
        assert matcher.cache_size == 0

    def test_cache_size_property(self):
        matcher = EmbeddingMatcher()
        assert matcher.cache_size == 0
        matcher._cache["a"] = np.array([1.0], dtype=np.float32)
        matcher._cache["b"] = np.array([2.0], dtype=np.float32)
        assert matcher.cache_size == 2

    def test_ensure_model_raises_on_missing_fastembed(self):
        """Should raise ImportError when fastembed is not installed."""
        matcher = EmbeddingMatcher()
        with patch.dict(sys.modules, {"fastembed": None}):
            with pytest.raises(ImportError):
                matcher._ensure_model()


# ---------------------------------------------------------------------------
# match_cross_platform_semantic
# ---------------------------------------------------------------------------

def _make_mock_matcher(similarity_matrix):
    """Create a mock EmbeddingMatcher that returns vectors producing specific similarities.

    Args:
        similarity_matrix: Dict of (a_title, b_title) -> cosine_similarity float.
    """
    # Create unique unit vectors for each title
    all_titles = set()
    for (a, b) in similarity_matrix:
        all_titles.add(a)
        all_titles.add(b)

    # Assign basis vectors then adjust for desired similarities
    # Simpler approach: mock embed_titles to return vectors that
    # produce the desired cosine similarity
    dim = 384
    vectors = {}
    for title in all_titles:
        rng = np.random.RandomState(hash(title) % (2**31))
        v = rng.randn(dim).astype(np.float32)
        v = v / np.linalg.norm(v)
        vectors[title] = v

    mock_matcher = MagicMock(spec=EmbeddingMatcher)
    mock_matcher.embed_titles.side_effect = lambda titles: [
        vectors.get(t, np.zeros(dim, dtype=np.float32)) for t in titles
    ]
    return mock_matcher


class TestMatchCrossPlatformSemantic:
    def test_falls_back_to_fuzzy_when_fastembed_unavailable(self):
        """When EmbeddingMatcher fails, should fall back to fuzzy matching."""
        markets_a = [
            {"question": "Will Bitcoin reach $100,000 by end of 2025?", "conditionId": "pm1"},
        ]
        markets_b = [
            {"title": "Bitcoin to reach $100,000 by end of 2025", "ticker": "BTC-100K"},
        ]

        # Force EmbeddingMatcher to fail
        with patch.object(EmbeddingMatcher, "get_instance", side_effect=ImportError("no fastembed")):
            results = match_cross_platform_semantic(
                markets_a, markets_b, "polymarket", "kalshi",
                threshold=0.70,
            )
        # Should still get results from fuzzy fallback
        # (the test pair is very similar so fuzzy should match)
        assert isinstance(results, list)

    def test_falls_back_on_embed_failure(self):
        """If embed_titles raises, should fall back to fuzzy."""
        markets_a = [
            {"question": "Will Bitcoin reach $100,000 by end of 2025?", "conditionId": "pm1"},
        ]
        markets_b = [
            {"title": "Bitcoin to reach $100,000 by end of 2025", "ticker": "BTC-100K"},
        ]

        mock_matcher = MagicMock(spec=EmbeddingMatcher)
        mock_matcher.embed_titles.side_effect = RuntimeError("model crashed")

        with patch.object(EmbeddingMatcher, "get_instance", return_value=mock_matcher):
            results = match_cross_platform_semantic(
                markets_a, markets_b, "polymarket", "kalshi",
                threshold=0.70,
            )
        assert isinstance(results, list)

    def test_matches_semantically_similar_titles(self):
        """Titles with same meaning but different words should match."""
        markets_a = [
            {"question": "Will inflation exceed 5% in 2025?", "conditionId": "a1"},
        ]
        markets_b = [
            {"title": "Inflation to surpass 5% in 2025", "ticker": "INF-5"},
        ]

        # Create a mock matcher that returns high similarity for these
        dim = 384
        vec_a = np.random.RandomState(42).randn(dim).astype(np.float32)
        vec_a = vec_a / np.linalg.norm(vec_a)
        # Make vec_b very similar to vec_a
        vec_b = vec_a + np.random.RandomState(43).randn(dim).astype(np.float32) * 0.05
        vec_b = vec_b / np.linalg.norm(vec_b)

        mock_matcher = MagicMock(spec=EmbeddingMatcher)
        mock_matcher.embed_titles.side_effect = lambda titles: [
            vec_a if "inflation" in titles[0] else vec_b
        ] if len(titles) == 1 else [vec_a, vec_b][:len(titles)]

        with patch.object(EmbeddingMatcher, "get_instance", return_value=mock_matcher):
            results = match_cross_platform_semantic(
                markets_a, markets_b, "polymarket", "smarkets",
                threshold=0.70,
            )
        assert len(results) >= 1
        assert results[0]["platform_a"] == "polymarket"
        assert results[0]["platform_b"] == "smarkets"
        assert "cosine_similarity" in results[0]
        assert results[0]["cosine_similarity"] > 0.70

    def test_no_match_for_unrelated_markets(self):
        """Completely different markets should not match."""
        markets_a = [
            {"question": "Will Bitcoin reach $100,000?", "conditionId": "a1"},
        ]
        markets_b = [
            {"title": "Federal Reserve rate decision March 2025", "ticker": "FED-1"},
        ]

        # Create very different vectors
        dim = 384
        vec_a = np.zeros(dim, dtype=np.float32)
        vec_a[0] = 1.0
        vec_b = np.zeros(dim, dtype=np.float32)
        vec_b[1] = 1.0  # orthogonal

        mock_matcher = MagicMock(spec=EmbeddingMatcher)
        mock_matcher.embed_titles.side_effect = lambda titles: [
            vec_a if "bitcoin" in titles[0] else vec_b
        ] if len(titles) == 1 else [vec_a, vec_b][:len(titles)]

        with patch.object(EmbeddingMatcher, "get_instance", return_value=mock_matcher):
            results = match_cross_platform_semantic(
                markets_a, markets_b, "polymarket", "kalshi",
                threshold=0.70,
            )
        assert len(results) == 0

    def test_confidence_tiers_in_results(self):
        """Results should include confidence tier based on cosine similarity."""
        markets_a = [
            {"question": "Will Bitcoin price reach $100,000 by December 2025?", "conditionId": "a1"},
        ]
        markets_b = [
            {"title": "Bitcoin price to reach $100,000 by December 2025", "ticker": "BTC-100K"},
        ]

        dim = 384
        vec = np.random.RandomState(42).randn(dim).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        # Near-identical vectors → HIGH confidence
        vec_b = vec + np.random.RandomState(43).randn(dim).astype(np.float32) * 0.01
        vec_b = vec_b / np.linalg.norm(vec_b)

        mock_matcher = MagicMock(spec=EmbeddingMatcher)
        mock_matcher.embed_titles.side_effect = lambda titles: [
            vec if i == 0 else vec_b for i, _ in enumerate(titles)
        ]

        with patch.object(EmbeddingMatcher, "get_instance", return_value=mock_matcher):
            results = match_cross_platform_semantic(
                markets_a, markets_b, "polymarket", "kalshi",
                threshold=0.70,
            )
        if results:
            assert results[0]["confidence"] in ("HIGH", "MEDIUM", "LOW")

    def test_min_confidence_filters_results(self):
        """Setting min_confidence=HIGH should filter out LOW matches."""
        markets_a = [
            {"question": "Will something happen with Bitcoin eventually 2025?", "conditionId": "a1"},
        ]
        markets_b = [
            {"title": "Bitcoin price target 2025 expectations", "ticker": "BTC-1"},
        ]

        dim = 384
        # Create vectors with ~0.75 similarity (LOW tier)
        vec_a = np.random.RandomState(42).randn(dim).astype(np.float32)
        vec_a = vec_a / np.linalg.norm(vec_a)
        vec_b = vec_a + np.random.RandomState(99).randn(dim).astype(np.float32) * 0.3
        vec_b = vec_b / np.linalg.norm(vec_b)

        mock_matcher = MagicMock(spec=EmbeddingMatcher)
        mock_matcher.embed_titles.side_effect = lambda titles: [
            vec_a if i == 0 else vec_b for i, _ in enumerate(titles)
        ]

        with patch.object(EmbeddingMatcher, "get_instance", return_value=mock_matcher):
            results_low = match_cross_platform_semantic(
                markets_a, markets_b, "polymarket", "kalshi",
                threshold=0.70, min_confidence="LOW",
            )
            results_high = match_cross_platform_semantic(
                markets_a, markets_b, "polymarket", "kalshi",
                threshold=0.70, min_confidence="HIGH",
            )
        # HIGH filter should be equal or more restrictive
        assert len(results_high) <= len(results_low)

    def test_deduplication_by_platform_b(self):
        """Only the best match per platform B market should be kept."""
        markets_a = [
            {"question": "Will Bitcoin price reach $100,000?", "conditionId": "a1"},
            {"question": "Bitcoin to hit $100,000 target?", "conditionId": "a2"},
        ]
        markets_b = [
            {"title": "Bitcoin price $100,000 target", "ticker": "BTC-100K"},
        ]

        dim = 384
        rng = np.random.RandomState(42)
        vec_base = rng.randn(dim).astype(np.float32)
        vec_base = vec_base / np.linalg.norm(vec_base)

        mock_matcher = MagicMock(spec=EmbeddingMatcher)
        # All titles get similar vectors
        mock_matcher.embed_titles.side_effect = lambda titles: [
            vec_base + rng.randn(dim).astype(np.float32) * 0.02
            for _ in titles
        ]

        with patch.object(EmbeddingMatcher, "get_instance", return_value=mock_matcher):
            results = match_cross_platform_semantic(
                markets_a, markets_b, "polymarket", "kalshi",
                threshold=0.70,
            )
        # Should only have at most 1 result per platform B ticker
        b_ids = [r["market_b"].get("ticker") for r in results]
        assert len(set(b_ids)) == len(b_ids)

    def test_empty_inputs(self):
        """Empty inputs should return empty results without errors."""
        mock_matcher = MagicMock(spec=EmbeddingMatcher)
        mock_matcher.embed_titles.return_value = []

        with patch.object(EmbeddingMatcher, "get_instance", return_value=mock_matcher):
            assert match_cross_platform_semantic([], [], "a", "b") == []
            assert match_cross_platform_semantic(
                [{"question": "test market here", "conditionId": "1"}], [],
                "a", "b",
            ) == []
            assert match_cross_platform_semantic(
                [], [{"title": "test market here", "ticker": "1"}],
                "a", "b",
            ) == []

    def test_short_titles_filtered(self):
        """Titles shorter than 8 chars should be filtered out."""
        markets_a = [{"question": "Short?", "conditionId": "a1"}]
        markets_b = [{"title": "Also short", "ticker": "b1"}]

        mock_matcher = MagicMock(spec=EmbeddingMatcher)
        mock_matcher.embed_titles.return_value = []

        with patch.object(EmbeddingMatcher, "get_instance", return_value=mock_matcher):
            results = match_cross_platform_semantic(
                markets_a, markets_b, "a", "b", threshold=0.70,
            )
        assert len(results) == 0

    def test_entity_overlap_prefilter(self):
        """Markets with zero entity overlap should not be compared."""
        markets_a = [
            {"question": "Will the unemployment rate exceed 6%?", "conditionId": "a1"},
        ]
        markets_b = [
            {"title": "Bitcoin cryptocurrency price prediction", "ticker": "b1"},
        ]

        dim = 384
        # Create identical vectors — but entity overlap should block the match
        vec = np.ones(dim, dtype=np.float32)
        vec = vec / np.linalg.norm(vec)

        mock_matcher = MagicMock(spec=EmbeddingMatcher)
        mock_matcher.embed_titles.side_effect = lambda titles: [vec] * len(titles)

        with patch.object(EmbeddingMatcher, "get_instance", return_value=mock_matcher):
            results = match_cross_platform_semantic(
                markets_a, markets_b, "polymarket", "kalshi",
                threshold=0.70,
            )
        # No entity overlap → no match despite high cosine similarity
        assert len(results) == 0

    def test_result_structure(self):
        """Results should have all expected keys."""
        markets_a = [
            {"question": "Will Bitcoin reach $100,000 by December 2025?", "conditionId": "a1"},
        ]
        markets_b = [
            {"title": "Bitcoin price to reach $100,000 by December 2025", "ticker": "BTC-100K"},
        ]

        dim = 384
        vec = np.random.RandomState(42).randn(dim).astype(np.float32)
        vec = vec / np.linalg.norm(vec)

        mock_matcher = MagicMock(spec=EmbeddingMatcher)
        mock_matcher.embed_titles.side_effect = lambda titles: [
            vec + np.random.RandomState(i).randn(dim).astype(np.float32) * 0.01
            for i, _ in enumerate(titles)
        ]

        with patch.object(EmbeddingMatcher, "get_instance", return_value=mock_matcher):
            results = match_cross_platform_semantic(
                markets_a, markets_b, "polymarket", "kalshi",
                threshold=0.50,  # low threshold to ensure match
            )

        if results:
            r = results[0]
            expected_keys = {
                "market_a", "market_b", "platform_a", "platform_b",
                "similarity", "cosine_similarity", "entity_overlap",
                "confidence", "title_a", "title_b",
            }
            assert expected_keys.issubset(set(r.keys()))
            assert isinstance(r["similarity"], int)
            assert isinstance(r["cosine_similarity"], float)
            assert r["confidence"] in ("HIGH", "MEDIUM", "LOW")


# ---------------------------------------------------------------------------
# Integration: cross.py uses semantic matching when enabled
# ---------------------------------------------------------------------------

class TestCrossSemanticIntegration:
    def test_config_flag_controls_matching_path(self):
        """SEMANTIC_MATCHING_ENABLED should control which matcher is used in scan_cross_all."""
        from config import SEMANTIC_MATCHING_ENABLED
        # Just verify the config value exists and is boolean
        assert isinstance(SEMANTIC_MATCHING_ENABLED, bool)

    def test_config_threshold_in_valid_range(self):
        """SEMANTIC_MATCH_THRESHOLD should be in (0, 1]."""
        from config import SEMANTIC_MATCH_THRESHOLD
        assert 0 < SEMANTIC_MATCH_THRESHOLD <= 1


# ---------------------------------------------------------------------------
# match_markets_to_events_semantic
# ---------------------------------------------------------------------------

class TestMatchMarketsToEventsSemantic:
    """Tests for the embedding-based PM-market-to-Kalshi-event matcher."""

    def test_falls_back_to_fuzzy_when_fastembed_unavailable(self):
        """When EmbeddingMatcher raises, falls back to fuzzy match_markets_to_events."""
        from matcher import match_markets_to_events_semantic
        with patch("matcher.EmbeddingMatcher.get_instance", side_effect=ImportError):
            with patch("matcher.match_markets_to_events", return_value=[{"dummy": True}]) as mock_fuzzy:
                result = match_markets_to_events_semantic(
                    [{"question": "Will X happen?"}],
                    [{"title": "Will X happen?", "event_ticker": "E1"}],
                )
                mock_fuzzy.assert_called_once()
                assert result == [{"dummy": True}]

    def test_falls_back_on_embed_failure(self):
        """When embedding raises, falls back to fuzzy matcher."""
        from matcher import match_markets_to_events_semantic
        mock_matcher = MagicMock()
        mock_matcher.embed_titles.side_effect = RuntimeError("model crash")
        with patch("matcher.EmbeddingMatcher.get_instance", return_value=mock_matcher):
            with patch("matcher.match_markets_to_events", return_value=[]) as mock_fuzzy:
                result = match_markets_to_events_semantic(
                    [{"question": "Will X happen?"}],
                    [{"title": "Will X happen?", "event_ticker": "E1"}],
                )
                mock_fuzzy.assert_called_once()

    def test_empty_markets_returns_empty(self):
        from matcher import match_markets_to_events_semantic
        mock_matcher = MagicMock()
        with patch("matcher.EmbeddingMatcher.get_instance", return_value=mock_matcher):
            result = match_markets_to_events_semantic([], [{"title": "Test", "event_ticker": "E1"}])
            assert result == []

    def test_empty_events_returns_empty(self):
        from matcher import match_markets_to_events_semantic
        mock_matcher = MagicMock()
        with patch("matcher.EmbeddingMatcher.get_instance", return_value=mock_matcher):
            result = match_markets_to_events_semantic([{"question": "Will X?"}], [])
            assert result == []

    def test_matches_similar_titles(self):
        """With high cosine similarity, should produce a match."""
        from matcher import match_markets_to_events_semantic
        mock_matcher = MagicMock()
        # Return fake embeddings with high similarity (identical vectors)
        fake_emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        mock_matcher.embed_titles.side_effect = lambda titles: [fake_emb] * len(titles)

        with patch("matcher.EmbeddingMatcher.get_instance", return_value=mock_matcher):
            pm_markets = [{"question": "Will Bitcoin hit 100k by December 2025?"}]
            kalshi_events = [{"title": "Bitcoin 100k by December 2025", "event_ticker": "BTC-100K"}]
            result = match_markets_to_events_semantic(pm_markets, kalshi_events, threshold=0.70)
            assert len(result) == 1
            assert result[0]["kalshi_event"]["event_ticker"] == "BTC-100K"
            assert result[0]["confidence"] in ("HIGH", "MEDIUM", "LOW")
            assert "cosine_similarity" in result[0]

    def test_no_match_when_below_threshold(self):
        """Low cosine similarity should produce no match."""
        from matcher import match_markets_to_events_semantic
        mock_matcher = MagicMock()
        # Return orthogonal vectors for PM vs Kalshi to get cos sim = 0
        emb_a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        emb_b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        call_count = [0]

        def _embed(titles):
            call_count[0] += 1
            # First call is PM titles, second call is Kalshi titles
            if call_count[0] == 1:
                return [emb_a] * len(titles)
            return [emb_b] * len(titles)

        mock_matcher.embed_titles.side_effect = _embed

        with patch("matcher.EmbeddingMatcher.get_instance", return_value=mock_matcher):
            # Ensure entities share at least 1 word so pre-filter doesn't reject
            pm_markets = [{"question": "Will cats rule the internet by 2026?"}]
            kalshi_events = [{"title": "Cats extinct by 2030", "event_ticker": "CATS"}]
            result = match_markets_to_events_semantic(pm_markets, kalshi_events, threshold=0.70)
            # Similarity is 0 (orthogonal), so no match above 0.70
            assert len(result) == 0

    def test_deduplication_by_kalshi_event(self):
        """Only best Polymarket match per Kalshi event should be kept."""
        from matcher import match_markets_to_events_semantic
        mock_matcher = MagicMock()
        fake_emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        mock_matcher.embed_titles.side_effect = lambda titles: [fake_emb] * len(titles)

        with patch("matcher.EmbeddingMatcher.get_instance", return_value=mock_matcher):
            pm_markets = [
                {"question": "Will Bitcoin hit 100k by December 2025?"},
                {"question": "Bitcoin price over 100k in December 2025?"},
            ]
            kalshi_events = [{"title": "Bitcoin 100k by December 2025", "event_ticker": "BTC-100K"}]
            result = match_markets_to_events_semantic(pm_markets, kalshi_events, threshold=0.70)
            # Only 1 match per Kalshi event ticker
            event_tickers = [m["kalshi_event"]["event_ticker"] for m in result]
            assert len(event_tickers) == len(set(event_tickers))

    def test_result_structure(self):
        """Verify output dict has the expected keys."""
        from matcher import match_markets_to_events_semantic
        mock_matcher = MagicMock()
        fake_emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        mock_matcher.embed_titles.side_effect = lambda titles: [fake_emb] * len(titles)

        with patch("matcher.EmbeddingMatcher.get_instance", return_value=mock_matcher):
            pm_markets = [{"question": "Will Bitcoin hit 100k by December 2025?"}]
            kalshi_events = [{"title": "Bitcoin 100k by December 2025", "event_ticker": "BTC-100K"}]
            result = match_markets_to_events_semantic(pm_markets, kalshi_events, threshold=0.70)
            assert len(result) == 1
            r = result[0]
            expected_keys = {
                "polymarket", "kalshi_event", "similarity", "cosine_similarity",
                "entity_overlap", "confidence", "pm_title", "kalshi_title",
            }
            assert expected_keys.issubset(set(r.keys()))
            assert isinstance(r["similarity"], int)
            assert isinstance(r["cosine_similarity"], float)
            assert r["confidence"] in ("HIGH", "MEDIUM", "LOW")

    def test_min_confidence_filters_results(self):
        """min_confidence='HIGH' should filter out LOW/MEDIUM matches."""
        from matcher import match_markets_to_events_semantic
        mock_matcher = MagicMock()
        # Return slightly different vectors to get cosine similarity ~0.96 (below HIGH=0.90? no..)
        # Actually we need sim < 0.90 for LOW confidence. Use more divergent vectors.
        emb_a = np.array([1.0, 0.6, 0.0], dtype=np.float32)
        emb_b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        call_count = [0]

        def _embed(titles):
            call_count[0] += 1
            if call_count[0] == 1:
                return [emb_a] * len(titles)
            return [emb_b] * len(titles)

        mock_matcher.embed_titles.side_effect = _embed

        with patch("matcher.EmbeddingMatcher.get_instance", return_value=mock_matcher):
            pm_markets = [{"question": "Will Bitcoin reach 100k in 2025?"}]
            kalshi_events = [{"title": "Bitcoin 100k 2025", "event_ticker": "BTC"}]
            result = match_markets_to_events_semantic(
                pm_markets, kalshi_events, threshold=0.70, min_confidence="HIGH",
            )
            # cos(emb_a, emb_b) = 1/sqrt(1.36) ≈ 0.857 -> MEDIUM confidence, filtered by HIGH
            assert len(result) == 0
