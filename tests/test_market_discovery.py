"""Tests for market_discovery.py — the ported LLM market-equivalence discovery.

The anthropic SDK is never imported here: MarketJudge is exercised with an
injected fake async client, and the pipeline with a duck-typed fake judge. Async
methods are driven via asyncio.run so no pytest-asyncio dependency is required.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_discovery as md


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, judgments):
        self.type = "tool_use"
        self.name = "submit_judgments"
        self.input = {"judgments": judgments}


class _FakeResponse:
    def __init__(self, blocks):
        self.content = blocks


class _FakeMessages:
    def __init__(self, outer):
        self._outer = outer

    async def create(self, **kwargs):
        self._outer.last_kwargs = kwargs
        return _FakeResponse([_FakeBlock(self._outer.judgments)])


class _FakeClient:
    """Stand-in for anthropic.AsyncAnthropic."""

    def __init__(self, judgments):
        self.judgments = judgments
        self.last_kwargs = None
        self.messages = _FakeMessages(self)


class _FakeJudge:
    """Duck-typed MarketJudge: returns equivalence for pair_ids in `equivalent_ids`."""

    def __init__(self, equivalent_ids, confidence=0.95):
        self.equivalent_ids = set(equivalent_ids)
        self.confidence = confidence
        self.calls = 0

    async def judge_batch(self, pairs):
        self.calls += 1
        return [
            md.Judgment(
                pair_id=p.pair_id,
                equivalent=p.pair_id in self.equivalent_ids,
                confidence=self.confidence,
                reasoning="fake",
            )
            for p in pairs
        ]


# ---------------------------------------------------------------------------
# Token pre-filter primitives
# ---------------------------------------------------------------------------


class TestTokenPrimitives:
    def test_token_set_drops_stopwords_and_punctuation(self):
        assert md.token_set("Will the Lakers win?") == frozenset({"lakers", "win"})

    def test_jaccard_identical_is_one(self):
        a = md.token_set("Lakers win championship")
        assert md.jaccard(a, a) == 1.0

    def test_jaccard_disjoint_is_zero(self):
        assert md.jaccard(md.token_set("apples"), md.token_set("oranges")) == 0.0

    def test_jaccard_empty_is_zero(self):
        assert md.jaccard(frozenset(), md.token_set("x y z")) == 0.0

    def test_pair_key_is_order_independent(self):
        k1 = md.pair_key("polymarket", "Will X?", "kalshi", "Will X happen?")
        k2 = md.pair_key("kalshi", "Will X happen?", "polymarket", "Will X?")
        assert k1 == k2

    def test_pair_key_is_case_insensitive(self):
        assert md.pair_key("a", "Hello", "b", "World") == md.pair_key("a", "hello", "b", "world")


# ---------------------------------------------------------------------------
# DiscoveryCache
# ---------------------------------------------------------------------------


class TestDiscoveryCache:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = md.DiscoveryCache(path)
        cache.put("k1", {"equivalent": True, "confidence": 0.9})
        cache.save()
        assert path.exists()

        reloaded = md.DiscoveryCache(path)
        assert len(reloaded) == 1
        assert reloaded.get("k1")["confidence"] == 0.9
        assert "k1" in reloaded

    def test_missing_key_returns_none(self, tmp_path):
        cache = md.DiscoveryCache(tmp_path / "none.json")
        assert cache.get("nope") is None

    def test_corrupt_file_is_tolerated(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        cache = md.DiscoveryCache(path)
        assert len(cache) == 0


# ---------------------------------------------------------------------------
# MarketJudge
# ---------------------------------------------------------------------------


class TestMarketJudge:
    def test_parses_tool_use_judgments(self):
        fake = _FakeClient([
            {"pair_id": "a|b", "equivalent": True, "confidence": 0.92, "reasoning": "same event"},
        ])
        judge = md.MarketJudge(client=fake)
        pairs = [md.CandidatePair("a|b", "polymarket", "Q1", "kalshi", "Q2")]
        out = asyncio.run(judge.judge_batch(pairs))
        assert len(out) == 1
        assert out[0].equivalent is True
        assert out[0].confidence == 0.92

    def test_prompt_cache_control_is_on_system_block(self):
        """Regression: pmarb passed cache_control as a top-level kwarg (invalid).

        It must live on the system content block instead.
        """
        fake = _FakeClient([])
        judge = md.MarketJudge(client=fake)
        asyncio.run(judge.judge_batch([md.CandidatePair("a|b", "v", "Q", "w", "Q")]))
        assert "cache_control" not in fake.last_kwargs
        system = fake.last_kwargs["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    def test_empty_pairs_short_circuits(self):
        fake = _FakeClient([])
        judge = md.MarketJudge(client=fake)
        assert asyncio.run(judge.judge_batch([])) == []
        assert fake.last_kwargs is None  # never called the API

    def test_too_many_pairs_raises(self):
        judge = md.MarketJudge(client=_FakeClient([]))
        pairs = [md.CandidatePair(f"{i}|x", "v", "Q", "w", "Q") for i in range(md.MAX_PAIRS_PER_CALL + 1)]
        try:
            asyncio.run(judge.judge_batch(pairs))
            assert False, "expected ValueError"
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# DiscoveryPipeline
# ---------------------------------------------------------------------------


def _markets():
    return {
        "polymarket": [md.MarketRef("polymarket", "P1", "Will the Lakers win the title?")],
        "kalshi": [md.MarketRef("kalshi", "K1", "Will the Lakers win the title?")],
    }


class TestDiscoveryPipeline:
    def test_prefilter_pairs_overlapping_questions(self):
        pipe = md.DiscoveryPipeline(md.DiscoveryCache("/tmp/none.json"), _FakeJudge([]), prefilter_threshold=0.1)
        cands = pipe._prefilter(_markets())
        assert len(cands) == 1
        assert cands[0].pair_id == "P1|K1"

    def test_prefilter_skips_below_threshold(self):
        markets = {
            "polymarket": [md.MarketRef("polymarket", "P1", "Bitcoin above 100k")],
            "kalshi": [md.MarketRef("kalshi", "K1", "Lakers championship roster")],
        }
        pipe = md.DiscoveryPipeline(md.DiscoveryCache("/tmp/none.json"), _FakeJudge([]), prefilter_threshold=0.3)
        assert pipe._prefilter(markets) == []

    def test_new_judgment_accepted_and_cached(self, tmp_path):
        cache = md.DiscoveryCache(tmp_path / "cache.json")
        judge = _FakeJudge(equivalent_ids={"P1|K1"}, confidence=0.95)
        pipe = md.DiscoveryPipeline(cache, judge, prefilter_threshold=0.1, accept_confidence=0.85)
        result = asyncio.run(pipe.run(_markets()))
        assert result.new_judgments == 1
        assert result.cached_hits == 0
        assert len(result.accepted) == 1
        assert (tmp_path / "cache.json").exists()
        assert judge.calls == 1

    def test_low_confidence_rejected(self, tmp_path):
        cache = md.DiscoveryCache(tmp_path / "cache.json")
        judge = _FakeJudge(equivalent_ids={"P1|K1"}, confidence=0.50)
        pipe = md.DiscoveryPipeline(cache, judge, prefilter_threshold=0.1, accept_confidence=0.85)
        result = asyncio.run(pipe.run(_markets()))
        assert result.rejected == 1
        assert result.accepted == []

    def test_cache_hit_skips_judge(self, tmp_path):
        cache = md.DiscoveryCache(tmp_path / "cache.json")
        key = md.pair_key("polymarket", "Will the Lakers win the title?", "kalshi", "Will the Lakers win the title?")
        cache.put(key, {
            "equivalent": True, "confidence": 0.93, "reasoning": "cached",
            "venue_a": "polymarket", "market_a_id": "P1", "question_a": "Will the Lakers win the title?",
            "venue_b": "kalshi", "market_b_id": "K1", "question_b": "Will the Lakers win the title?",
        })
        judge = _FakeJudge(equivalent_ids=set())
        pipe = md.DiscoveryPipeline(cache, judge, prefilter_threshold=0.1, accept_confidence=0.85)
        result = asyncio.run(pipe.run(_markets()))
        assert result.cached_hits == 1
        assert result.new_judgments == 0
        assert len(result.accepted) == 1
        assert judge.calls == 0


# ---------------------------------------------------------------------------
# Adaptors
# ---------------------------------------------------------------------------


class TestAdaptors:
    def test_polymarket_refs(self):
        refs = md.polymarket_refs([
            {"id": "123", "question": "Will X?"},
            {"condition_id": "0xabc", "title": "Will Y?"},
            {"id": "", "question": ""},  # skipped
        ])
        assert len(refs) == 2
        assert refs[0] == md.MarketRef("polymarket", "123", "Will X?")
        assert refs[1].external_id == "0xabc"

    def test_kalshi_refs(self):
        refs = md.kalshi_refs([
            {"event_ticker": "KXWORLD", "title": "Will Z?"},
            {"ticker": "KXALT", "sub_title": "Alt"},
            {"title": "no ticker"},  # skipped
        ])
        assert len(refs) == 2
        assert refs[0] == md.MarketRef("kalshi", "KXWORLD", "Will Z?")


# ---------------------------------------------------------------------------
# YAML I/O and the human-review gate
# ---------------------------------------------------------------------------


def _record(qa="Will X?", conf=0.92):
    return {
        "equivalent": True, "confidence": conf, "reasoning": "same",
        "venue_a": "polymarket", "market_a_id": "P1", "question_a": qa,
        "venue_b": "kalshi", "market_b_id": "K1", "question_b": qa,
    }


class TestYamlGate:
    def test_write_defaults_to_candidate_status(self, tmp_path):
        out = tmp_path / "cand.yaml"
        n = md.write_candidate_pairs([_record()], out)
        assert n == 1
        pairs = md.load_candidate_pairs(out)
        assert pairs[0]["status"] == "candidate"
        assert pairs[0]["venues"] == {"polymarket": "P1", "kalshi": "K1"}

    def test_verified_loader_excludes_candidates(self, tmp_path):
        out = tmp_path / "cand.yaml"
        md.write_candidate_pairs([_record(), _record(qa="Will Y?")], out)
        assert md.load_verified_pairs(out) == []  # nothing verified yet

    def test_verified_loader_returns_only_verified(self, tmp_path):
        import yaml

        out = tmp_path / "cand.yaml"
        data = {"pairs": [
            {"name": "a", "status": "verified", "question": "Q", "venues": {"polymarket": "P", "kalshi": "K"}},
            {"name": "b", "status": "candidate", "question": "Q2", "venues": {}},
        ]}
        out.write_text(yaml.safe_dump(data))
        verified = md.load_verified_pairs(out)
        assert len(verified) == 1
        assert verified[0]["name"] == "a"

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert md.load_candidate_pairs(tmp_path / "nope.yaml") == []

    def test_seed_from_pmarb_marks_candidate(self, tmp_path):
        import yaml

        src = tmp_path / "pmarb.yaml"
        src.write_text(yaml.safe_dump({"pairs": [
            {"name": "discovered-0000", "question": "Will NZ win?", "confidence": 0.92,
             "reasoning": "same", "venues": {"polymarket": "558957", "kalshi": "KXNZ"}},
        ]}))
        dest = tmp_path / "candidates.yaml"
        n = md.seed_candidates_from_pmarb(src, dest)
        assert n == 1
        loaded = md.load_candidate_pairs(dest)
        assert loaded[0]["status"] == "candidate"
        assert loaded[0]["venues"]["kalshi"] == "KXNZ"
        assert md.load_verified_pairs(dest) == []  # seeded data still needs review
