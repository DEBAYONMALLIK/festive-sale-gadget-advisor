"""Model-name matching decides whether the shopper's own phone survives the shortlist, and whether
two scouts naming the same product get merged. Over-matching silently swaps a generation."""
import pytest

import pipeline as P


def f(name, search=None, price=50000):
    return P.Finalist(name=name, search_name=search or name, approx_price=price,
                      key_specs=[], why_it_fits="", source_urls=[])


class TestStripBrackets:
    def test_removes_a_parenthesised_suffix(self):
        assert P._strip_brackets("Galaxy S24 (256 GB, Violet)") == "Galaxy S24"

    def test_removes_square_brackets(self):
        assert P._strip_brackets("iPhone 17 [Pro Max]") == "iPhone 17"

    def test_leaves_a_clean_name_alone(self):
        assert P._strip_brackets("OnePlus 15R") == "OnePlus 15R"


class TestSameModel:
    def test_storage_and_colour_do_not_make_a_different_phone_once_brackets_are_stripped(self):
        clean = P._strip_brackets("Galaxy S24 (256 GB, Violet)")
        assert P.same_model(clean, "Samsung Galaxy S24")

    def test_the_brand_name_is_optional(self):
        assert P.same_model("Samsung Galaxy Tab S10 Lite", "Galaxy Tab S10 Lite")

    def test_different_generations_are_not_the_same_phone(self):
        assert not P.same_model("Galaxy S23", "Galaxy S24")

    def test_a_pro_tier_is_not_the_base_model(self):
        assert not P.same_model("iPhone 17", "iPhone 17 Pro")

    def test_fe_and_lite_tiers_stay_distinct(self):
        assert not P.same_model("Galaxy Tab S9 FE", "Galaxy Tab S10 Lite")


class TestTheShopperSOwnModelIsNeverLost:
    def test_a_named_model_missing_from_the_shortlist_is_added(self):
        out = P.add_user_models(["Nothing Phone 3"], [f("OnePlus 15R")])
        assert "Nothing Phone 3" in [x.name for x in out]

    def test_a_named_model_already_present_is_not_duplicated(self):
        # search_name is specified as brand + model with no brackets, which is what makes this work.
        out = P.add_user_models(["Samsung Galaxy S24"],
                                [f("Galaxy S24 (256 GB, Violet)", search="Samsung Galaxy S24")])
        assert len(out) == 1

    @pytest.mark.xfail(strict=True, reason=(
        "model_key does not strip bracketed text, so '256' and 'violet' end up in the key and the "
        "match fails. Harmless while the filter agent obeys 'search_name = brand + model, no "
        "brackets', but when it does not, the shopper's own phone is silently added twice. Fixing "
        "it means stripping brackets inside model_key."))
    def test_a_bracketed_search_name_still_matches(self):
        out = P.add_user_models(["Samsung Galaxy S24"], [f("Galaxy S24 (256 GB, Violet)")])
        assert len(out) == 1

    def test_is_user_model_recognises_a_variant_spelling(self):
        assert P.is_user_model(["Samsung Galaxy S24"],
                               f("Galaxy S24 (256 GB)", search="Samsung Galaxy S24"))

    def test_is_user_model_rejects_a_different_model(self):
        assert not P.is_user_model(["Samsung Galaxy S24"], f("OnePlus 15R"))

    def test_nothing_is_added_when_the_shopper_named_nothing(self):
        out = P.add_user_models([], [f("OnePlus 15R")])
        assert len(out) == 1
