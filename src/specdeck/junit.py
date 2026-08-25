"""The cell as JUnit XML, for whatever CI renders it.

The mapping is stated rather than implied, because a JUnit document is a contract other
tools parse:

    <testsuites>   one invocation of `specdeck run`
    <testsuite>    one cell — one card x one provider x one prompt
    <testcase>     one run of that cell
    <failure>      a run whose gate did not hold, listing the wires and criteria that failed

A run and not a cell is the testcase, so a CI report shows which run broke and on what
rather than one opaque green-or-red row per card. The cost of that choice is real and is
stated on every failing row: a cell passes at k of N, so a *tolerated* failure is a red
testcase sitting beside exit 0. The failure message says so in as many words — "run 3 of 5
failed; the cell needs 4 of 5 and got 4" — and the suite's `<system-out>` repeats the
cell's own verdict, so nothing has to be inferred from the count of red rows.

Times come off `Run.measured`, the agent's own root-span duration. Not a wall clock around
the runner: over a replayed cassette that would report the replay as the agent's latency.

Model-authored text — a judge's reason, an SME's criterion — is stripped of characters XML
1.0 cannot carry before it becomes element text or an attribute. ElementTree escapes `&`,
`<` and `>` but emits a control character verbatim, which makes the document unparseable at
the far end, after every judge call has already been paid for. That is the hazard report.py
handles for rich markup, in a different serializer.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from .cell import Cell, Run
from .report import headline, judge_source
from .tier import Tier

#: Everything outside the XML 1.0 legal character set. Tab, newline and carriage return
#: are legal and are kept; the rest of C0, the surrogates and the two noncharacters are not.
_ILLEGAL = re.compile("[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def to_xml(cell: Cell) -> str:
    """One cell as a JUnit document. Pure — the caller decides where it goes."""
    # Sanitised once, here, rather than at each element that carries it: the card path
    # reaches this module from the command line and is the only untrusted attribute value.
    card = _safe(cell.card_path)
    root = ET.Element("testsuites", name="specdeck")
    suite = ET.SubElement(
        root,
        "testsuite",
        name=card,
        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    for index, run in enumerate(cell.results, start=1):
        _case(suite, cell, run, index, card)
    ET.SubElement(suite, "system-out").text = _safe("\n".join(_summary(cell)))

    elapsed = f"{sum(run.measured.duration_s for run in cell.results):.3f}"
    for element in (root, suite):
        # Counted off the tree rather than tallied alongside it. A hand-maintained counter
        # and the elements it claims to count drift, and the count is what a dashboard
        # renders — a suite reporting failures="0" over a red testcase is worse than none.
        element.set("tests", str(len(element.findall(".//testcase"))))
        element.set("failures", str(len(element.findall(".//failure"))))
        element.set("errors", "0")
        element.set("skipped", "0")
        element.set("time", elapsed)

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def _case(suite: ET.Element, cell: Cell, run: Run, index: int, card: str) -> None:
    case = ET.SubElement(
        suite,
        "testcase",
        classname=card,
        name=f"run {index} of {cell.runs}",
        time=f"{run.measured.duration_s:.3f}",
    )
    if run.passed:
        return
    # The k-of-N statistic on the row itself. Without it a reader sees a red test beside a
    # green build and has to go find out which of the two is lying; neither is.
    message = (
        f"run {index} of {cell.runs} failed; the cell needs {cell.threshold} of "
        f"{cell.runs} and got {cell.passes}"
    )
    failure = ET.SubElement(case, "failure", type="gate", message=_safe(message))
    failure.text = _safe("\n".join(_why(run)) or "the run failed with no check recorded")


def _why(run: Run) -> list[str]:
    """Every gate check this run failed, wires first, in the order they were evaluated.

    Gate tier only. A failed credit criterion is not why the run failed — credit never
    offsets and never blocks — so it belongs in the summary, not in the failure.
    """
    lines = [f"{verdict.id} — {verdict.detail}" for verdict in run.wires if not verdict.passed]
    if run.judged:
        lines += [
            f"{headline(verdict.text)} — {verdict.reason}"
            if verdict.reason
            else headline(verdict.text)
            for verdict in run.judged.verdicts
            if not verdict.passed and verdict.tier is Tier.GATE
        ]
    return lines


def _summary(cell: Cell) -> list[str]:
    """The cell's own verdict, in the two numbers the console prints and never blended."""
    lines = [
        f"gate {'PASS' if cell.passed else 'FAIL'} — {cell.passes}/{cell.runs} runs "
        f"(passes at {cell.threshold})"
    ]
    if cell.credit_mean is None:
        lines.append(f"credit n/a — no passing run to score, out of {cell.credit_total}")
    else:
        lines.append(
            f"credit {cell.credit_mean:g}/{cell.credit_total} over {cell.passes} "
            f"passing run{'' if cell.passes == 1 else 's'}"
        )
    judged = [run.judged for run in cell.results if run.judged]
    lines.append(
        f"judge {cell.judge_model} ({judge_source(judged)}), {cell.judge_calls} "
        f"call{'' if cell.judge_calls == 1 else 's'} over {cell.runs} "
        f"run{'' if cell.runs == 1 else 's'}"
    )
    # Named only when one spoke, the same rule the console follows: a cell run from
    # recorded traces had no simulated user, and naming a model that did not run is a
    # claim about the run.
    if cell.simulator_model:
        lines.append(f"simulator {cell.simulator_model}")
    return lines


def _safe(text: str) -> str:
    """Text with every character XML 1.0 cannot carry removed."""
    return _ILLEGAL.sub("", text)
