"""The matrix file: parsing and the product. Pure, offline, no card and no cassettes."""

from __future__ import annotations

from pathlib import Path

import pytest

from specdeck.baseline import DEFAULT_CELL
from specdeck.matrix import Axis, Column, MatrixError, cell_key, columns, load_matrix

TWO_BY_TWO = """
[[provider]]
name = "sonnet"
model = "claude-sonnet-5"
config = { endpoint = "a", shared = "from-provider" }

[[provider]]
name = "opus"
model = "claude-opus-5"

[[prompt]]
name = "terse"
config = { system_prompt_path = "prompts/terse.md" }

[[prompt]]
name = "verbose"
config = { shared = "from-prompt" }
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "matrix.toml"
    path.write_text(text)
    return path


class TestTheProduct:
    def test_two_axes_yield_the_cartesian_product_in_declared_order(self, tmp_path: Path) -> None:
        found = columns(load_matrix(write(tmp_path, TWO_BY_TWO)))
        assert [column.name for column in found] == [
            "sonnet/terse",
            "sonnet/verbose",
            "opus/terse",
            "opus/verbose",
        ]

    def test_a_column_knows_which_model_to_be_priced_at(self, tmp_path: Path) -> None:
        found = {
            column.name: column.model
            for column in columns(load_matrix(write(tmp_path, TWO_BY_TWO)))
        }
        assert found["sonnet/terse"] == "claude-sonnet-5"
        assert found["opus/verbose"] == "claude-opus-5"

    def test_a_prompt_may_override_the_providers_model(self, tmp_path: Path) -> None:
        text = """
[[provider]]
name = "p"
model = "claude-sonnet-5"

[[prompt]]
name = "cheap"
model = "claude-haiku-4-5"
"""
        assert columns(load_matrix(write(tmp_path, text)))[0].model == "claude-haiku-4-5"

    def test_one_empty_axis_degenerates_to_the_other_rather_than_to_nothing(
        self, tmp_path: Path
    ) -> None:
        text = """
[[provider]]
name = "sonnet"
model = "claude-sonnet-5"

[[provider]]
name = "opus"
model = "claude-opus-5"
"""
        found = columns(load_matrix(write(tmp_path, text)))
        assert [column.name for column in found] == ["sonnet", "opus"]
        assert [column.prompt for column in found] == ["", ""]


class TestTheConfig:
    def test_the_prompt_wins_over_the_provider_key_by_key(self, tmp_path: Path) -> None:
        found = {
            column.name: column.config
            for column in columns(load_matrix(write(tmp_path, TWO_BY_TWO)))
        }
        assert found["sonnet/verbose"] == {"endpoint": "a", "shared": "from-prompt"}
        assert found["sonnet/terse"] == {
            "endpoint": "a",
            "shared": "from-provider",
            "system_prompt_path": "prompts/terse.md",
        }

    def test_neither_source_table_is_mutated_by_the_merge(self, tmp_path: Path) -> None:
        matrix = load_matrix(write(tmp_path, TWO_BY_TWO))
        columns(matrix)
        assert matrix.providers[0].config == {"endpoint": "a", "shared": "from-provider"}
        assert matrix.prompts[1].config == {"shared": "from-prompt"}

    def test_specdeck_never_reads_a_key_inside_config(self, tmp_path: Path) -> None:
        # The prompts are the user's own files and the adapter's business. A parser that
        # validated a path inside `config` would refuse a column for a file it has no
        # standing to know about.
        text = """
[[provider]]
name = "p"
model = "claude-sonnet-5"
config = { system_prompt_path = "/nowhere/at/all.md", temperature = 0.2 }
"""
        assert columns(load_matrix(write(tmp_path, text)))[0].config == {
            "system_prompt_path": "/nowhere/at/all.md",
            "temperature": 0.2,
        }


class TestTheBudget:
    def test_a_declared_cap_is_read(self, tmp_path: Path) -> None:
        text = '[budget]\nusd = 2.5\n\n[[provider]]\nname = "p"\nmodel = "claude-sonnet-5"\n'
        assert load_matrix(write(tmp_path, text)).budget_usd == 2.5

    def test_no_budget_section_is_no_cap(self, tmp_path: Path) -> None:
        text = '[[provider]]\nname = "p"\nmodel = "claude-sonnet-5"\n'
        assert load_matrix(write(tmp_path, text)).budget_usd is None

    @pytest.mark.parametrize("value", ["0", "-1", '"lots"', "true"])
    def test_a_cap_that_is_not_a_positive_number_is_refused(
        self, tmp_path: Path, value: str
    ) -> None:
        text = f'[budget]\nusd = {value}\n\n[[provider]]\nname = "p"\nmodel = "m"\n'
        with pytest.raises(MatrixError, match=r"positive number"):
            load_matrix(write(tmp_path, text))


class TestRefusals:
    """Every malformed shape is a MatrixError, so the CLI routes it to exit 2 rather than
    letting a TOMLDecodeError or a ValidationError out as "specdeck itself broke"."""

    def test_a_provider_with_no_model_is_refused_by_name(self, tmp_path: Path) -> None:
        with pytest.raises(MatrixError, match=r"\[\[provider\]\] #1 \(sonnet\) has no `model`"):
            load_matrix(write(tmp_path, '[[provider]]\nname = "sonnet"\n'))

    def test_a_prompt_needs_no_model_of_its_own(self, tmp_path: Path) -> None:
        text = '[[provider]]\nname = "p"\nmodel = "m"\n\n[[prompt]]\nname = "terse"\n'
        assert columns(load_matrix(write(tmp_path, text)))[0].model == "m"

    def test_a_prompt_only_matrix_needs_its_own_models(self, tmp_path: Path) -> None:
        with pytest.raises(MatrixError, match=r"needs its own `model`"):
            load_matrix(write(tmp_path, '[[prompt]]\nname = "terse"\n'))

    def test_duplicate_names_on_one_axis_are_refused(self, tmp_path: Path) -> None:
        # Column names key the report grid and the baseline's cell slot; two entries
        # sharing one would overwrite each other in both.
        text = '[[provider]]\nname = "p"\nmodel = "m"\n\n[[provider]]\nname = "p"\nmodel = "n"\n'
        with pytest.raises(MatrixError, match=r"two \[\[provider\]\] entries named p"):
            load_matrix(write(tmp_path, text))

    def test_a_name_carrying_a_slash_is_refused(self, tmp_path: Path) -> None:
        text = '[[provider]]\nname = "a/b"\nmodel = "m"\n'
        with pytest.raises(MatrixError, match=r"cannot contain"):
            load_matrix(write(tmp_path, text))

    def test_an_entry_with_no_name_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(MatrixError, match=r"has no `name`"):
            load_matrix(write(tmp_path, '[[provider]]\nmodel = "m"\n'))

    def test_a_file_with_neither_axis_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(MatrixError, match=r"no columns to run"):
            load_matrix(write(tmp_path, "[budget]\nusd = 1\n"))

    def test_malformed_toml_is_a_matrix_error_not_a_decode_error(self, tmp_path: Path) -> None:
        with pytest.raises(MatrixError):
            load_matrix(write(tmp_path, "[[provider]\nname = "))

    def test_a_missing_file_is_a_matrix_error(self, tmp_path: Path) -> None:
        with pytest.raises(MatrixError, match=r"cannot read the matrix"):
            load_matrix(tmp_path / "nope.toml")

    def test_a_config_that_is_not_a_table_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(MatrixError, match=r"`config` is str"):
            load_matrix(write(tmp_path, '[[provider]]\nname = "p"\nmodel = "m"\nconfig = "x"\n'))

    def test_an_axis_that_is_not_a_list_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(MatrixError, match=r"not a list of entries"):
            load_matrix(write(tmp_path, 'provider = "sonnet"\n'))


class TestTheCellKey:
    """One derivation, the way `lock_key` is one — the wave-3 handoff's requirement."""

    def test_a_column_keys_the_baseline_by_its_own_name(self) -> None:
        column = Column(
            name="sonnet/terse", provider="sonnet", prompt="terse", model="m", config={}
        )
        assert cell_key(column) == "sonnet/terse"

    def test_the_single_cell_path_keeps_the_slot_wave_three_held_open(self) -> None:
        # A `spec.baseline.toml` already committed must need no migration.
        assert cell_key(None) == DEFAULT_CELL

    def test_an_axis_declares_no_model_by_default(self) -> None:
        assert Axis(name="terse").model is None
