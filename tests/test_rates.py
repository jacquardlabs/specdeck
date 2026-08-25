"""The cost rate table (#16). No network, and no clock beyond a fixed date."""

from datetime import date
from importlib import resources
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specdeck.cli import app
from specdeck.judge import DEFAULT_JUDGE_MODEL
from specdeck.rates import RATES_FILE, Estimate, RateError, Rates, load_rates

runner = CliRunner()

VERIFIED = date(2026, 1, 15)

#: A table of its own, so no test here depends on what the shipped one happens to charge.
SAMPLE = """\
verified = 2026-01-15

[rates.anthropic]
"claude-opus-5" = { input = 5.0, output = 25.0 }
"claude-sonnet-5" = { input = 3.0, output = 15.0 }

[rates.openai]
"gpt-4o" = { input = 2.5, output = 10.0 }
"gpt-4o-mini" = { input = 0.15, output = 0.6 }
"""


def sample() -> Rates:
    return Rates.from_toml(SAMPLE, source="sample")


def flat(text: str) -> str:
    """Rich soft-wraps at 80 columns under CliRunner, so assert on unwrapped text."""
    return " ".join(text.split())


class TestTheTable:
    def test_the_shipped_table_loads(self) -> None:
        table = Rates.builtin()
        assert isinstance(table.verified, date)
        assert table.table
        for entries in table.table.values():
            for rate in entries.values():
                assert rate.input >= 0
                assert rate.output >= 0

    def test_the_shipped_table_is_read_out_of_the_package(self) -> None:
        # Not a path relative to this file: that works in the repo and breaks in a wheel.
        assert resources.files("specdeck").joinpath(RATES_FILE).is_file()

    def test_the_shipped_table_prices_the_default_judge_model(self) -> None:
        # The one regression that matters: the model this repo pins going unpriced would
        # make every estimate read n/a.
        assert Rates.builtin().rate_for(DEFAULT_JUDGE_MODEL) is not None

    def test_verified_is_not_in_the_future(self) -> None:
        assert Rates.builtin().verified <= date.today()

    def test_a_missing_verified_date_is_refused(self) -> None:
        with pytest.raises(RateError, match=r"somewhere\.toml"):
            Rates.from_toml(
                '[rates.anthropic]\n"x" = { input = 1, output = 2 }', source="somewhere.toml"
            )

    def test_a_malformed_rate_names_the_model(self) -> None:
        text = (
            'verified = 2026-01-15\n[rates.anthropic]\n"claude-opus-5" = { input = -1, output = 2 }'
        )
        with pytest.raises(RateError) as caught:
            Rates.from_toml(text, source="sample")
        assert "anthropic" in str(caught.value)
        assert "claude-opus-5" in str(caught.value)

    def test_a_table_with_no_rates_section_is_refused(self) -> None:
        with pytest.raises(RateError, match=r"\[rates\]"):
            Rates.from_toml("verified = 2026-01-15", source="sample")


class TestLookup:
    def test_a_bare_model_resolves_under_the_default_provider(self) -> None:
        assert sample().rate_for("claude-opus-5") is not None

    def test_a_prefixed_model_resolves_under_its_provider(self) -> None:
        rate = sample().rate_for("openai/gpt-4o")
        assert rate is not None
        assert rate.input == 2.5

    def test_a_dated_id_matches_its_family_prefix(self) -> None:
        rate = sample().rate_for("claude-sonnet-5-20260514")
        assert rate is not None
        assert rate.output == 15.0

    def test_the_longest_prefix_wins(self) -> None:
        rate = sample().rate_for("openai/gpt-4o-mini")
        assert rate is not None
        assert rate.input == 0.15

    def test_an_unknown_model_is_none_not_a_default(self) -> None:
        # Where cctx's mid-range fallback was deliberately not ported.
        assert sample().rate_for("some-new-model-9") is None

    def test_an_unknown_provider_is_none(self) -> None:
        assert sample().rate_for("mistral/large") is None

    def test_an_explicit_provider_overrides_the_model_string(self) -> None:
        # The raw-OTLP case: the span names a provider the bare model string cannot.
        assert sample().rate_for("gpt-4o", provider="openai") is not None


class TestEstimate:
    def test_an_unpriced_model_never_reads_as_zero_dollars(self) -> None:
        label = sample().estimate("some-new-model-9", input_tokens=1000, output_tokens=1000).label
        assert "some-new-model-9" in label
        assert "no rate" in label
        assert "$0.00" not in label

    def test_every_priced_label_says_estimate_and_carries_the_date(self) -> None:
        label = sample().estimate("claude-opus-5", input_tokens=1000, output_tokens=100).label
        assert "estimate" in label
        assert VERIFIED.isoformat() in label

    def test_the_arithmetic_is_per_million_tokens(self) -> None:
        priced = sample().estimate(
            "claude-sonnet-5", input_tokens=1_000_000, output_tokens=1_000_000
        )
        assert priced.usd == pytest.approx(18.0)
        assert priced.complete

    def test_summing_a_priced_and_an_unpriced_call_reports_partial(self) -> None:
        table = sample()
        total = table.estimate(
            "claude-opus-5", input_tokens=1_000_000, output_tokens=0
        ) + table.estimate("some-new-model-9", input_tokens=1000, output_tokens=1000)
        assert total.usd == pytest.approx(5.0)
        assert total.priced == 1
        assert total.unpriced == ("some-new-model-9",)
        assert not total.complete
        assert "partial" in total.label

    def test_the_zero_element_folds(self) -> None:
        table = sample()
        one = table.estimate("claude-opus-5", input_tokens=1000, output_tokens=1000)
        two = table.estimate("claude-sonnet-5", input_tokens=1000, output_tokens=1000)
        assert sum([one, two], start=Estimate.nothing(table.verified)) == one + two

    def test_nothing_priced_is_not_a_dollar_figure(self) -> None:
        assert "$" not in Estimate.nothing(VERIFIED).label

    def test_two_verified_dates_cannot_be_added(self) -> None:
        with pytest.raises(RateError, match="two dates"):
            Estimate.nothing(VERIFIED) + Estimate.nothing(date(2026, 2, 1))


class TestOverride:
    def test_a_user_table_adds_a_model_without_restating_the_builtin(self, tmp_path: Path) -> None:
        override = tmp_path / RATES_FILE
        override.write_text(
            'verified = 2026-02-01\n[rates.openai]\n"gpt-9" = { input = 1.0, output = 2.0 }\n'
        )
        table = load_rates(override, beside=tmp_path)
        assert table.rate_for("openai/gpt-9") is not None
        assert table.rate_for(DEFAULT_JUDGE_MODEL) is not None

    def test_a_user_table_overrides_one_rate_and_moves_the_date(self, tmp_path: Path) -> None:
        override = tmp_path / RATES_FILE
        override.write_text(
            f"verified = 2026-02-01\n[rates.anthropic]\n"
            f'"{DEFAULT_JUDGE_MODEL}" = {{ input = 99.0, output = 199.0 }}\n'
        )
        table = load_rates(override, beside=tmp_path)
        rate = table.rate_for(DEFAULT_JUDGE_MODEL)
        assert rate is not None
        assert rate.input == 99.0
        # The printed label has to describe the table actually in use.
        assert table.verified == date(2026, 2, 1)

    def test_a_rates_file_beside_the_card_is_found_without_a_flag(self, tmp_path: Path) -> None:
        (tmp_path / RATES_FILE).write_text(
            'verified = 2026-02-01\n[rates.openai]\n"gpt-9" = { input = 1.0, output = 2.0 }\n'
        )
        assert load_rates(None, beside=tmp_path).rate_for("openai/gpt-9") is not None

    def test_no_file_beside_the_card_leaves_the_builtin_alone(self, tmp_path: Path) -> None:
        assert load_rates(None, beside=tmp_path) == Rates.builtin()

    def test_an_explicitly_named_missing_file_is_an_error(self, tmp_path: Path) -> None:
        # Rather than silently falling back to the built-in, which would price a run
        # against a table the operator did not ask for.
        with pytest.raises(RateError, match=r"nope\.toml"):
            load_rates(tmp_path / "nope.toml", beside=tmp_path)


class TestTheCommand:
    def test_it_prints_the_table_and_the_verified_date(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["rates"])
        assert result.exit_code == 0, result.stdout
        printed = flat(result.stdout)
        assert "per million tokens" in printed
        assert Rates.builtin().verified.isoformat() in printed
        assert DEFAULT_JUDGE_MODEL in printed

    def test_it_never_claims_to_be_billing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        printed = flat(runner.invoke(app, ["rates"]).stdout)
        assert "estimates, not billing" in printed
        assert "invoice" not in printed
        assert "billed" not in printed

    def test_a_broken_override_exits_two_not_three(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.toml"
        broken.write_text('[rates.anthropic]\n"x" = { input = 1, output = 2 }\n')
        result = runner.invoke(app, ["rates", "--rates", str(broken)])
        # Exit 2, not 3: a table the user wrote is a user error, not specdeck breaking.
        assert result.exit_code == 2
        assert "error" in result.stdout
        assert "verified" in flat(result.stdout)
