"""The cost rate table (#16). No network, and no clock beyond a fixed date."""

from datetime import date
from importlib import resources
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specdeck.cli import app
from specdeck.judge import DEFAULT_JUDGE_MODEL
from specdeck.rates import RATES_FILE, Estimate, ModelRate, RateError, Rates, load_rates

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


#: Every way a hand-written table has been seen to go wrong. Each of these but the first
#: used to escape RateError and exit 1 on a traceback, and one test over one malformed
#: shape read as proof of a contract it did not cover.
BROKEN = {
    "no verified date": '[rates.anthropic]\n"x" = { input = 1, output = 2 }\n',
    "not toml": "verified = [\n",
    "a scalar provider": 'verified = 2026-01-15\n[rates]\nanthropic = "nope"\n',
    "a scalar rate": 'verified = 2026-01-15\n[rates.anthropic]\n"x" = 5\n',
    "a negative rate": 'verified = 2026-01-15\n[rates.anthropic]\n"x" = { input = -1 }\n',
    "no rates section": "verified = 2026-01-15\n",
}


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

    def test_a_provider_that_is_not_a_table_is_refused_by_name(self) -> None:
        # `anthropic = "nope"` used to reach `.items()` and raise AttributeError, which
        # left the CLI exiting 3 on a file the user wrote.
        with pytest.raises(RateError, match=r"\[rates\.anthropic\]"):
            Rates.from_toml('verified = 2026-01-15\n[rates]\nanthropic = "nope"', source="sample")

    def test_an_empty_provider_section_prices_nothing_and_is_dropped(self) -> None:
        # The shape you get by commenting out the last entry under a heading. It prices
        # nothing, the same as an absent section, so no reader downstream has to ask.
        table = Rates.from_toml("verified = 2026-01-15\n[rates.mistral]\n", source="sample")
        assert table.table == {}
        assert table.rate_for("mistral/large") is None


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

    def test_an_unlisted_sibling_does_not_inherit_the_family_price(self) -> None:
        # The shipped table builds the trap itself: `claude-opus-4` is the retired $15/$75
        # family and `claude-opus-4-5` is $5/$25, so a bare prefix test would price the
        # next unlisted Opus at three times the tier — a substituted rate (#16 b).
        table = Rates.builtin()
        assert table.rate_for("claude-opus-4-9") is None
        assert table.rate_for("claude-opus-4-9-20261001") is None
        assert table.rate_for("claude-sonnet-42") is None

    def test_a_typo_is_not_priced_as_the_family_it_almost_names(self) -> None:
        assert Rates.builtin().rate_for("claude-opus-45") is None

    def test_a_dated_id_still_prices_after_the_boundary_rule(self) -> None:
        rate = Rates.builtin().rate_for("claude-opus-4-1-20250805")
        assert rate is not None
        assert (rate.input, rate.output) == (15.0, 75.0)

    def test_a_suffix_after_the_date_still_prices(self) -> None:
        # Bedrock and Vertex hang a revision on the dated id; the date is the boundary.
        assert Rates.builtin().rate_for("claude-sonnet-4-5-20250929-v1:0") is not None

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

    def test_a_user_table_overrides_one_rate(self, tmp_path: Path) -> None:
        override = tmp_path / RATES_FILE
        override.write_text(
            f"verified = 2026-02-01\n[rates.anthropic]\n"
            f'"{DEFAULT_JUDGE_MODEL}" = {{ input = 99.0, output = 199.0 }}\n'
        )
        table = load_rates(override, beside=tmp_path)
        rate = table.rate_for(DEFAULT_JUDGE_MODEL)
        assert rate is not None
        assert rate.input == 99.0

    def test_an_older_override_dates_the_merged_table(self, tmp_path: Path) -> None:
        override = tmp_path / RATES_FILE
        override.write_text(
            'verified = 2026-02-01\n[rates.openai]\n"gpt-9" = { input = 1.0, output = 2.0 }\n'
        )
        assert load_rates(override, beside=tmp_path).verified == date(2026, 2, 1)

    def test_a_newer_override_does_not_re_date_the_builtin_rows(self, tmp_path: Path) -> None:
        # An override that adds one model restates nothing about the fifteen Anthropic
        # rows, so its date must not stamp them with a day nobody checked them.
        override = tmp_path / RATES_FILE
        override.write_text(
            'verified = 2027-01-01\n[rates.openai]\n"gpt-9" = { input = 1.0, output = 2.0 }\n'
        )
        assert load_rates(override, beside=tmp_path).verified == Rates.builtin().verified

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

    def test_a_directory_named_as_the_table_is_a_rate_error(self, tmp_path: Path) -> None:
        # `exists()` is true for a directory, so this used to raise IsADirectoryError.
        with pytest.raises(RateError):
            load_rates(tmp_path, beside=tmp_path)

    def test_a_file_that_is_not_text_is_a_rate_error(self, tmp_path: Path) -> None:
        # UnicodeDecodeError is a ValueError, not an OSError — its own case, not a freebie.
        binary = tmp_path / "binary.toml"
        binary.write_bytes(b"\xff\xfe\x00rates")
        with pytest.raises(RateError, match=r"binary\.toml"):
            load_rates(binary, beside=tmp_path)


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

    @pytest.mark.parametrize("shape", sorted(BROKEN))
    def test_every_malformed_shape_exits_two(self, shape: str, tmp_path: Path) -> None:
        broken = tmp_path / "broken.toml"
        broken.write_text(BROKEN[shape])
        result = runner.invoke(app, ["rates", "--rates", str(broken)])
        assert result.exit_code == 2, result.stdout
        assert "error" in flat(result.stdout)

    def test_a_directory_named_as_the_table_exits_two(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["rates", "--rates", str(tmp_path)])
        assert result.exit_code == 2, result.stdout

    def test_an_empty_provider_section_still_renders(self, tmp_path: Path) -> None:
        # `max()` over an empty section used to die mid-table, after the header had
        # already printed: half a table and a traceback.
        override = tmp_path / "empty-section.toml"
        override.write_text("verified = 2026-01-15\n[rates.mistral]\n")
        result = runner.invoke(app, ["rates", "--rates", str(override)])
        assert result.exit_code == 0, result.stdout
        assert DEFAULT_JUDGE_MODEL in flat(result.stdout)

    def test_a_sub_cent_rate_prints_as_itself(self, tmp_path: Path) -> None:
        # Two decimals rendered 0.004 as 0.00 — a model that costs money, printed free,
        # in the one command whose whole job is showing the rate the user typed.
        override = tmp_path / RATES_FILE
        override.write_text(
            'verified = 2026-01-15\n[rates.openai]\n"tiny" = { input = 0.004, output = 0.02 }\n'
        )
        printed = flat(runner.invoke(app, ["rates", "--rates", str(override)]).stdout)
        assert "0.0040 in" in printed
        assert "0.0200 out" in printed

    def test_a_bracket_in_the_message_survives_rendering(self, tmp_path: Path) -> None:
        # Rich reads `[rates.anthropic]` as a style tag, so the interpolated message lost
        # the part that says where to look.
        broken = tmp_path / "broken.toml"
        broken.write_text('verified = 2026-01-15\n[rates]\nanthropic = "nope"\n')
        printed = flat(runner.invoke(app, ["rates", "--rates", str(broken)]).stdout)
        assert "[rates.anthropic]" in printed


class TestDatedReleases:
    """Both shapes a vendor writes a snapshot date in, and nothing looser."""

    TABLE = Rates(
        verified=date(2026, 8, 25),
        table={
            "anthropic": {"claude-sonnet-5": ModelRate(input=2.0, output=10.0)},
            "openai": {"gpt-5-mini": ModelRate(input=0.25, output=2.0)},
        },
    )

    def test_an_anthropic_dated_id_prices_as_its_family(self) -> None:
        assert self.TABLE.rate_for("claude-sonnet-5-20260514") is not None

    def test_an_openai_dated_id_prices_as_its_family(self) -> None:
        """`-2025-08-07`, which the original rule did not recognise.

        OpenAI replies name a dated snapshot for a request that said `gpt-5-mini`, so a
        matrix column declared one model and ran another. The cap refused it, which is the
        cap doing its job; the rule was what needed widening.
        """
        assert self.TABLE.rate_for("openai/gpt-5-mini-2025-08-07") is not None

    def test_a_different_family_that_merely_extends_the_string_is_still_unpriced(
        self,
    ) -> None:
        """The property the widening must not break: no substituted rates."""
        assert self.TABLE.rate_for("openai/gpt-5-mini-turbo") is None
        assert self.TABLE.rate_for("openai/gpt-5-turbo-2025-08-07") is None
