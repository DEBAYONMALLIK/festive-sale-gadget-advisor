"""The price logic is plain Python on purpose - it is the part of the answer a language model must
never be able to invent - so it is the part most worth pinning down with tests."""
import pipeline as P
from price_worker import Listing, Variant


def listing(site="amazon", title="OnePlus 15R", variants=(), **kw):
    return Listing(site=site, found=kw.pop("found", True), matched_title=title,
                   url=f"https://{site}.in/p/1", variants=list(variants), **kw)


def v(price, storage="256 GB", in_stock=True, mrp=None, color="Black"):
    return Variant(storage=storage, color=color, price=price, mrp=mrp, in_stock=in_stock)


class TestCheapestInStockVariant:
    def test_picks_the_lowest_priced_in_stock_variant(self):
        q = P.build_quote("OnePlus 15R", "OnePlus 15R", "amazon",
                          listing(variants=[v(62000), v(54999), v(58000)]))
        assert q.ok and q.price == 54999

    def test_ignores_out_of_stock_variants_even_when_cheaper(self):
        q = P.build_quote("OnePlus 15R", "OnePlus 15R", "amazon",
                          listing(variants=[v(39999, in_stock=False), v(54999)]))
        assert q.ok and q.price == 54999, "a sold-out variant is not a price anyone can pay"
        assert q.in_stock_variants == 1

    def test_reports_no_price_when_nothing_is_in_stock(self):
        q = P.build_quote("OnePlus 15R", "OnePlus 15R", "amazon",
                          listing(variants=[v(54999, in_stock=False)]))
        assert not q.ok and "no in-stock variant" in q.reason

    def test_discards_an_emi_or_accessory_price_far_below_the_others(self):
        # Marketplaces show "from Rs 2,999/month" and accessory bundles next to the real price.
        # Taking the lowest number on the page would report a phone as costing 2,999 rupees.
        q = P.build_quote("OnePlus 15R", "OnePlus 15R", "amazon",
                          listing(variants=[v(2999, storage="EMI"), v(54999), v(62000)]))
        assert q.ok and q.price == 54999
        assert any("2999" in w or "EMI" in w for w in q.warnings)

    def test_prefers_more_storage_when_two_variants_cost_the_same(self):
        q = P.build_quote("OnePlus 15R", "OnePlus 15R", "amazon",
                          listing(variants=[v(54999, storage="128 GB"), v(54999, storage="256 GB")]))
        assert q.storage == "256 GB"


class TestFailureIsReportedNotGuessed:
    def test_a_captcha_is_reported_as_blocked(self):
        q = P.build_quote("OnePlus 15R", "OnePlus 15R", "amazon",
                          listing(blocked=True, note="robot check", variants=[v(54999)]))
        assert not q.ok and q.blocked and "blocked" in q.reason
        assert q.price == 0, "a blocked lookup must not carry a price"

    def test_a_missing_page_is_reported_as_not_found(self):
        q = P.build_quote("OnePlus 15R", "OnePlus 15R", "amazon", listing(found=False, note="no result"))
        assert not q.ok and "not found" in q.reason

    def test_a_different_product_is_rejected(self):
        q = P.build_quote("OnePlus 15R", "OnePlus 15R", "amazon",
                          listing(title="OnePlus Nord CE 4 Lite", variants=[v(19999)]))
        assert not q.ok, "a cheaper but different phone must never be quoted as the one asked for"


class TestCheapestAcrossSites:
    def test_returns_the_lowest_successful_quote(self):
        a = P.build_quote("X", "X", "amazon", listing(site="amazon", title="X", variants=[v(60000)]))
        f = P.build_quote("X", "X", "flipkart", listing(site="flipkart", title="X", variants=[v(57000)]))
        assert P.cheapest_quote([a, f]).site == "flipkart"

    def test_returns_none_when_every_lookup_failed(self):
        a = P.build_quote("X", "X", "amazon", listing(site="amazon", found=False))
        assert P.cheapest_quote([a]) is None

    def test_ignores_failed_quotes_when_one_succeeded(self):
        a = P.build_quote("X", "X", "amazon", listing(site="amazon", found=False))
        f = P.build_quote("X", "X", "flipkart", listing(site="flipkart", title="X", variants=[v(57000)]))
        assert P.cheapest_quote([a, f]).price == 57000
