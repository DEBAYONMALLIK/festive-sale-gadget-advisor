"""With LIVE_PRICES off no marketplace is opened, so every price shown is a search-result estimate.
The product promise is that a number is either verified or visibly labelled as a guess - never
presented as a current price. These tests exist so that promise cannot be broken quietly."""
import asyncio

import pytest

import pipeline as P
from price_worker import Listing, Variant


def finalist(name="OnePlus 15R", price=59999):
    return P.Finalist(name=name, search_name=name, approx_price=price,
                      key_specs=["8 GB RAM"], why_it_fits="fits", source_urls=[])


def constraints(budget_max=80000, shortlist=None):
    return P.UserConstraints(
        category="phone", budget_min=0, budget_max=budget_max, currency="INR", region="India",
        priorities=[P.Priority(criterion="camera", weight=1.0)], must_haves=[], dealbreakers=[],
        use_case="buyer", user_shortlist=shortlist or [])


class TestEstimatesAreLabelled:
    def test_every_unpriced_model_is_called_an_estimate(self):
        out = P.price_digest({"quotes": [], "finalists": [finalist(), finalist("Galaxy S24", 53199)]})
        for line in out.strip().splitlines():
            assert "NOT verified" in line and "estimate" in line

    def test_the_estimate_figure_is_still_shown(self):
        out = P.price_digest({"quotes": [], "finalists": [finalist(price=59999)]})
        assert "59,999" in out, "hiding the number helps nobody; labelling it does"

    def test_a_verified_price_is_not_labelled_an_estimate(self):
        q = P.build_quote("OnePlus 15R", "OnePlus 15R", "amazon",
                          Listing(site="amazon", found=True, matched_title="OnePlus 15R",
                                  url="https://amazon.in/p/1",
                                  variants=[Variant(storage="256 GB", color="Black",
                                                    price=54999, in_stock=True)]))
        out = P.price_digest({"quotes": [q.model_dump()], "finalists": [finalist()]})
        assert "54,999" in out and "NOT verified" not in out
        assert "LOWEST" in out


class TestLivePricesDisabled:
    @pytest.fixture(autouse=True)
    def _off(self, monkeypatch):
        monkeypatch.setattr(P, "LIVE_PRICES", False)

    def _run(self, state):
        return asyncio.run(P.live_prices(state, {"configurable": {}}))

    def test_no_marketplace_is_opened(self, monkeypatch):
        def boom(*a, **kw):
            raise AssertionError("fetch_prices_for must not run when LIVE_PRICES is off")
        monkeypatch.setattr(P, "fetch_prices_for", boom)
        self._run({"constraints": constraints(), "finalists": [finalist()]})

    def test_it_returns_no_quotes_rather_than_invented_ones(self):
        out = self._run({"constraints": constraints(), "finalists": [finalist()]})
        assert out["quotes"] == []

    def test_the_log_says_the_prices_are_unverified(self):
        out = self._run({"constraints": constraints(), "finalists": [finalist()]})
        assert any("estimates, not verified" in line for line in out["log"])

    def test_finalists_survive_instead_of_being_dropped(self):
        # With no live price there is no evidence a model is over budget, so dropping one would
        # discard a candidate on a guess.
        out = self._run({"constraints": constraints(budget_max=10000), "finalists": [finalist(price=59999)]})
        assert len(out["finalists"]) == 1

    def test_the_shortlist_is_capped(self, monkeypatch):
        monkeypatch.setattr(P, "MAX_FINALISTS", 2)
        out = self._run({"constraints": constraints(),
                         "finalists": [finalist(f"Phone {i}") for i in range(6)]})
        assert len(out["finalists"]) == 2

    def test_a_model_the_shopper_named_is_kept_first(self, monkeypatch):
        monkeypatch.setattr(P, "MAX_FINALISTS", 1)
        out = self._run({"constraints": constraints(shortlist=["Galaxy S24"]),
                         "finalists": [finalist("OnePlus 15R"), finalist("Galaxy S24")]})
        assert out["finalists"][0].name == "Galaxy S24"
