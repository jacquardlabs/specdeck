"""Reading an agent definition, and saying how much of it was legible.

Introspection depth varies by framework: a declared graph gives full topology, a raw-SDK
loop gives tools only, and an object we cannot read at all gives nothing. `Depth` is that
fact as data, and every consumer prints it — the lint header, the coverage report — because
an obligation check that silently degrades is worse than one that reports its own
blindness. Depth is computed from what came back, never from what was asked: a probe that
finds a graph object and reads no edges out of it honestly reports TOOLS.

The payload is `agent.AgentDescription`, unchanged. There is no second graph model in the
project, and #20's path denominator reads the same `edges` list the obligations do.

`PROBES` is the seam. LangGraph ships; OpenAI SDK hand-offs, MCP configs and Claude Code
subagent files are later probes appended to that tuple, and nothing else moves.

Pure: no importlib, no filesystem, no console. The CLI resolves `module:attribute` and
hands the object here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .agent import AgentDescription, Describable

#: Nodes LangGraph adds to every graph. They are the graph's entry and exit, not anything
#: a card could reference or a run could hit, so they are filtered out of both the node
#: list and the edge endpoints rather than reported as unreferenced hand-offs forever.
STRUCTURAL = frozenset({"__start__", "__end__"})


class Depth(StrEnum):
    """How much of the agent definition was legible.

    Deliberately not `tier.Tier`, which is gate-versus-credit and a different concept.
    The words are docs/card-format.md's own — "a declared graph gives full topology, a
    raw-SDK loop gives tools only" — so the lint header and the coverage report print the
    same vocabulary the spec uses.
    """

    NONE = "none"
    TOOLS = "tools"
    TOPOLOGY = "topology"


class Introspection(BaseModel):
    """What one probe read, and how far it got.

    A model rather than a bare `AgentDescription` because the depth and the source are
    what a report has to state. `note` names a facet the probe could not see at all —
    blindness inside a depth, which the depth alone cannot express.
    """

    source: str = "none"
    reference: str = ""
    depth: Depth = Depth.NONE
    description: AgentDescription = Field(default_factory=AgentDescription)
    note: str = ""


def introspect(target: object, *, reference: str = "") -> Introspection:
    """The first probe that recognises `target` wins; otherwise, nothing was legible."""
    for probe in PROBES:
        found = probe(target)
        if found is not None:
            return found.model_copy(update={"reference": reference})
    return Introspection(
        reference=reference,
        note="the object satisfies no introspector here — it declares no describe() and "
        "is not shaped like a compiled graph",
    )


def cycles(edges: list[tuple[str, str]]) -> list[list[str]]:
    """Cycles in a directed edge list, as sorted node lists.

    Tarjan's strongly connected components, iterative so a deep graph cannot blow the
    stack. **Not Johnson's elementary circuits**: two loops sharing a node report as one
    cycle rather than two. That under-reports the count and never invents a cycle, which
    is the conservative direction for a rule whose severity is ERROR — and the obligation
    is satisfied per component either way.
    """
    successors: dict[str, list[str]] = {}
    for source, target in edges:
        successors.setdefault(source, []).append(target)
        successors.setdefault(target, [])

    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[list[str]] = []
    counter = 0

    for root in sorted(successors):
        if root in index_of:
            continue
        # (node, position in that node's successor list). The explicit frame is what makes
        # this iterative; the recursive form is four lines shorter and recurses per node.
        work: list[tuple[str, int]] = [(root, 0)]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, position = work[-1]
            if position < len(successors[node]):
                work[-1] = (node, position + 1)
                child = successors[node][position]
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index_of[child])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index_of[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                # A single node is a cycle only when it loops back to itself.
                if len(component) > 1 or (node, node) in edges:
                    found.append(sorted(component))
    return sorted(found)


def _depth(description: AgentDescription) -> Depth:
    """From what came back, never from what was asked."""
    if description.edges:
        return Depth.TOPOLOGY
    return Depth.TOOLS if description.tools else Depth.NONE


def _from_describe(target: object) -> Introspection | None:
    """Any object implementing the optional half of the adapter protocol.

    `isinstance(target, Describable)` is the whole check — agent.py says so.
    """
    if not isinstance(target, Describable):
        return None
    description = target.describe()
    if not description.cycles and description.edges:
        # A declared cycle list is the author's claim about their own graph and is taken
        # as it stands. Deriving one over data they already stated would silently replace
        # an elementary circuit they named with the SCC that contains it.
        description = description.model_copy(update={"cycles": cycles(description.edges)})
    return Introspection(
        source="describe()", depth=_depth(description), description=description, note=""
    )


def _from_langgraph(target: object) -> Introspection | None:
    """A compiled LangGraph graph, read by duck typing.

    langgraph is not a dependency and is not being added, so nothing here imports it and
    nothing verifies this against a real install. The accessor chain is therefore stated
    rather than assumed, and every step is guarded: a shape mismatch returns None or
    degrades to a lower depth, and never raises out of lint.

    Assumed shape:
      - `target.get_graph()` returns an object carrying `.nodes` (a mapping of name to
        node) and `.edges` (objects with `.source`/`.target`, or plain 2-tuples);
      - a tool node is any node whose value — or its `.data`, or its `.runnable` — carries
        `tools_by_name` (a mapping) or `tools` (objects with `.name`);
      - HITL points are the node names in `interrupt_before` / `interrupt_after` on the
        compiled object, and nothing else is inferred. LangGraph's newer `interrupt()`
        inside a node body is invisible to static reading, so a graph using it reaches
        TOPOLOGY with no HITL points and `note` says the attributes were absent.
      - `__start__` and `__end__` are dropped from the nodes and from edge endpoints.
    """
    getter = getattr(target, "get_graph", None)
    if not callable(getter):
        return None
    try:
        graph = getter()
    except Exception:
        # A probe never crashes lint. An object that answers `get_graph()` with an error
        # is one we cannot read, which is a depth to report and not a failure to raise.
        return None
    nodes = _mapping(getattr(graph, "nodes", None))
    edges = _edges(getattr(graph, "edges", None))
    if nodes is None and not edges:
        return None

    tools = sorted({name for node in (nodes or {}).values() for name in _tools_of(node)})
    hitl = sorted(
        {
            str(name)
            for attribute in ("interrupt_before", "interrupt_after")
            for name in _sequence(getattr(target, attribute, None))
            if str(name) not in STRUCTURAL
        }
    )
    description = AgentDescription(tools=tools, edges=edges, cycles=cycles(edges), hitl_points=hitl)
    notes = []
    if not tools:
        notes.append("no node exposes a tool binding, so tool names were not visible")
    if not hitl:
        notes.append(
            "no interrupt_before/interrupt_after declared, so HITL points were not visible"
        )
    return Introspection(
        source="langgraph",
        depth=_depth(description),
        description=description,
        note="; ".join(notes),
    )


def _mapping(value: object) -> dict[str, Any] | None:
    if value is None or not hasattr(value, "items"):
        return None
    try:
        return {str(key): item for key, item in value.items()}  # type: ignore[union-attr]
    except Exception:
        return None


def _sequence(value: object) -> list[Any]:
    if value is None or isinstance(value, str) or not isinstance(value, Iterable):
        return []
    try:
        return list(value)
    except Exception:
        return []


def _edges(value: object) -> list[tuple[str, str]]:
    """Edge endpoints, structural nodes dropped, deduped and ordered."""
    found: list[tuple[str, str]] = []
    for edge in _sequence(value):
        source = getattr(edge, "source", None)
        target = getattr(edge, "target", None)
        if source is None and target is None:
            pair = _sequence(edge)
            if len(pair) != 2:
                continue
            source, target = pair
        if source is None or target is None:
            continue
        pair_of = (str(source), str(target))
        if STRUCTURAL & set(pair_of) or pair_of in found:
            continue
        found.append(pair_of)
    return sorted(found)


def _tools_of(node: object) -> list[str]:
    """Tool names bound to one graph node, through whichever wrapper carries them."""
    for candidate in (node, getattr(node, "data", None), getattr(node, "runnable", None)):
        if candidate is None:
            continue
        by_name = _mapping(getattr(candidate, "tools_by_name", None))
        if by_name:
            return sorted(by_name)
        named = [
            str(tool.name)
            for tool in _sequence(getattr(candidate, "tools", None))
            if getattr(tool, "name", None) is not None
        ]
        if named:
            return sorted(set(named))
    return []


#: The pluggable seam. Order matters only in that the first match wins, and `describe()`
#: comes first because an author who wrote one meant it to be read.
PROBES: tuple[Callable[[object], Introspection | None], ...] = (_from_describe, _from_langgraph)
