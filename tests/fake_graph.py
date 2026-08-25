"""Duck-typed stand-ins for a compiled LangGraph graph.

Written to the accessor chain `specdeck.introspect._from_langgraph` documents, and **not
verified against a real langgraph install**. langgraph is not on the dependency allowlist
and is not being added — not even to the dev group, which holds pytest and ruff and
nothing else — so there is no `importorskip` escape hatch and no interop test here.

What that buys and what it does not: the tests prove the probe reads *this* shape, not
that LangGraph emits it. The probe is written so a shape mismatch degrades to a lower
depth rather than raising, and the mismatch then shows up in a report as an honest
`tools` or `none` tier rather than as a clean bill of health.

These are real objects, not mocks, the same way `tests/fake_agent.FakeAgent` implements
the adapter protocol rather than mocking it.
"""

from __future__ import annotations


class FakeEdge:
    """One edge, in the `.source` / `.target` shape `Graph.edges` yields."""

    def __init__(self, source: str, target: str) -> None:
        self.source = source
        self.target = target


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeToolNode:
    """A node carrying a tool binding, keyed the way `ToolNode` keys it."""

    def __init__(self, tools: list[str]) -> None:
        self.tools_by_name = {name: FakeTool(name) for name in tools}


class FakeToolListNode:
    """The other shape a tool-bearing node takes: a plain list of named tools."""

    def __init__(self, tools: list[str]) -> None:
        self.tools = [FakeTool(name) for name in tools]


class FakePlainNode:
    """A node that binds nothing — a router, a chat step, a hand-off."""


class FakeGraph:
    def __init__(self, nodes: dict[str, object], edges: list[object]) -> None:
        self.nodes = nodes
        self.edges = edges


class FakeCompiled:
    """What `StateGraph.compile()` returns, as far as introspection is concerned."""

    def __init__(
        self,
        graph: FakeGraph,
        *,
        interrupt_before: list[str] | None = None,
        interrupt_after: list[str] | None = None,
    ) -> None:
        self._graph = graph
        if interrupt_before is not None:
            self.interrupt_before = interrupt_before
        if interrupt_after is not None:
            self.interrupt_after = interrupt_after

    def get_graph(self) -> FakeGraph:
        return self._graph


class BrokenCompiled:
    """A graph object whose `get_graph()` raises. A probe never crashes lint."""

    def get_graph(self) -> FakeGraph:
        raise RuntimeError("the graph is not compiled")


class ShapelessCompiled:
    """`get_graph()` answers with something carrying neither nodes nor edges."""

    def get_graph(self) -> object:
        return object()


def refund_graph() -> FakeCompiled:
    """A refund agent: an `agent -> tools -> agent` loop, plus a hand-off to a human.

    A zero-argument factory, which is what `--agent-def module:attribute` resolves. The
    cycle is real (`agent` and `tools` reach each other), two tools are bound, and
    `escalate` is a HITL point declared through `interrupt_before`.
    """
    nodes: dict[str, object] = {
        "__start__": FakePlainNode(),
        "agent": FakePlainNode(),
        "tools": FakeToolNode(["get_reservation_details", "cancel_reservation"]),
        "escalate": FakePlainNode(),
        "__end__": FakePlainNode(),
    }
    edges = [
        FakeEdge("__start__", "agent"),
        FakeEdge("agent", "tools"),
        FakeEdge("tools", "agent"),
        FakeEdge("agent", "escalate"),
        FakeEdge("escalate", "__end__"),
    ]
    return FakeCompiled(FakeGraph(nodes, edges), interrupt_before=["escalate"])


def tuple_graph() -> FakeCompiled:
    """The same loop with edges as plain 2-tuples and no HITL declaration."""
    nodes: dict[str, object] = {
        "agent": FakePlainNode(),
        "tools": FakeToolListNode(["search_direct_flight"]),
    }
    return FakeCompiled(FakeGraph(nodes, [("agent", "tools"), ("tools", "agent")]))


def acyclic_graph() -> FakeCompiled:
    """Tools bound, hand-offs declared, and nothing that loops."""
    nodes: dict[str, object] = {
        "intake": FakePlainNode(),
        "tools": FakeToolNode(["get_user_details"]),
        "handoff": FakePlainNode(),
    }
    edges = [
        FakeEdge("__start__", "intake"),
        FakeEdge("intake", "tools"),
        FakeEdge("tools", "handoff"),
        FakeEdge("handoff", "__end__"),
    ]
    return FakeCompiled(FakeGraph(nodes, edges))


def nodeless_graph() -> FakeCompiled:
    """Edges but no readable node mapping — topology without tool names."""
    return FakeCompiled(FakeGraph({}, [FakeEdge("a", "b"), FakeEdge("b", "a")]))
