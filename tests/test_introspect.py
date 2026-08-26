"""Reading an agent definition, and the depth it honestly reached."""

from __future__ import annotations

import random

from specdeck.agent import AgentDescription
from specdeck.introspect import Depth, Introspection, bounding_tools, cycles, introspect

from .fake_agent import (
    BareAgent,
    BrokenDescribeAgent,
    DictDescribeAgent,
    FakeAgent,
    refusing_agent,
)
from .fake_graph import (
    BrokenCompiled,
    FakeCompiled,
    FakeGraph,
    FakeToolNode,
    ShapelessCompiled,
    acyclic_graph,
    nodeless_graph,
    refund_graph,
    tuple_graph,
)


class Declared:
    """An adapter that states its own graph, cycles included."""

    def __init__(self, description: AgentDescription) -> None:
        self.description = description

    def describe(self) -> AgentDescription:
        return self.description


class TestCycles:
    def test_an_empty_edge_list_has_no_cycles(self) -> None:
        assert cycles([]) == []

    def test_a_pure_dag_has_no_cycles(self) -> None:
        assert cycles([("a", "b"), ("b", "c")]) == []

    def test_a_self_loop_is_a_cycle_of_one(self) -> None:
        assert cycles([("a", "a")]) == [["a"]]

    def test_a_node_with_no_self_loop_is_never_a_cycle_of_one(self) -> None:
        # The SCC of an isolated node is itself; only the self-edge makes it a loop.
        assert cycles([("a", "b")]) == []

    def test_a_two_node_loop_is_one_cycle(self) -> None:
        assert cycles([("a", "b"), ("b", "a")]) == [["a", "b"]]

    def test_two_disjoint_loops_are_two_cycles(self) -> None:
        assert cycles([("a", "b"), ("b", "a"), ("c", "d"), ("d", "c")]) == [
            ["a", "b"],
            ["c", "d"],
        ]

    def test_a_figure_eight_reports_as_one_cycle_not_two(self) -> None:
        """SCC, not elementary circuits. Recorded behaviour, not a surprise.

        Two loops sharing a node report as the single component that contains them. It
        under-reports the count and never invents a cycle, which is the safe direction
        for an ERROR-severity rule — and one wire naming any node in the component
        satisfies the obligation either way.
        """
        assert cycles([("a", "b"), ("b", "a"), ("b", "c"), ("c", "b")]) == [["a", "b", "c"]]

    def test_the_result_does_not_depend_on_the_order_the_edges_arrived_in(self) -> None:
        edges = [("a", "b"), ("b", "a"), ("c", "d"), ("d", "c"), ("d", "e")]
        shuffled = list(edges)
        random.Random(7).shuffle(shuffled)
        assert cycles(shuffled) == cycles(edges) == [["a", "b"], ["c", "d"]]

    def test_a_long_chain_does_not_recurse(self) -> None:
        # Iterative Tarjan. The recursive form dies on a graph this deep.
        edges = [(str(n), str(n + 1)) for n in range(3000)]
        assert cycles(edges) == []


class TestDepthIsWhatCameBack:
    def test_an_adapter_declaring_only_tools_reports_tools(self) -> None:
        found = introspect(FakeAgent([], tools=["pay_invoice"]))
        assert found.depth is Depth.TOOLS
        assert found.source == "describe()"
        assert found.description.tools == ["pay_invoice"]

    def test_a_raw_sdk_loop_reports_none_rather_than_guessing(self) -> None:
        found = introspect(BareAgent())
        assert found.depth is Depth.NONE
        assert found.source == "none"

    def test_an_object_nothing_can_read_reports_none_and_keeps_the_reference(self) -> None:
        found = introspect(object(), reference="pkg.mod:thing")
        assert found.depth is Depth.NONE
        assert found.reference == "pkg.mod:thing"
        assert found.note

    def test_an_adapter_declaring_edges_reports_topology(self) -> None:
        found = introspect(Declared(AgentDescription(tools=["a"], edges=[("x", "y")])))
        assert found.depth is Depth.TOPOLOGY

    def test_a_graph_object_that_yields_no_edges_does_not_claim_topology(self) -> None:
        """Depth is computed from what came back, never from what was asked.

        A probe that recognised a graph and read nothing out of it must not report the
        richest tier — that is the silent degradation the tier exists to prevent.
        """
        graph = FakeGraph({"tools": FakeToolNode(["calculate"])}, [])
        found = introspect(FakeCompiled(graph))
        assert found.depth is Depth.TOOLS
        assert found.source == "langgraph"


class TestTheLangGraphProbe:
    def test_a_compiled_graph_reports_topology_with_its_tools_edges_and_hitl(self) -> None:
        found = introspect(refund_graph(), reference="tests.fake_graph:refund_graph")
        assert found.source == "langgraph"
        assert found.depth is Depth.TOPOLOGY
        assert found.description.tools == ["get_invoice", "pay_invoice"]
        assert found.description.hitl_points == ["escalate"]

    def test_the_start_and_end_nodes_are_never_edge_endpoints(self) -> None:
        found = introspect(refund_graph())
        endpoints = {name for edge in found.description.edges for name in edge}
        assert "__start__" not in endpoints
        assert "__end__" not in endpoints
        assert found.description.edges == [
            ("agent", "escalate"),
            ("agent", "tools"),
            ("tools", "agent"),
        ]

    def test_the_cycle_is_derived_from_the_edges(self) -> None:
        assert introspect(refund_graph()).description.cycles == [["agent", "tools"]]

    def test_which_node_bound_which_tool_survives_the_flattening(self) -> None:
        """`tools` is a union and loses the mapping the cycle obligation needs: a cycle
        names nodes, a wire names a tool, and `node_tools` is where the two meet."""
        found = introspect(refund_graph())
        assert found.description.node_tools == {
            "tools": ["get_invoice", "pay_invoice"]
        }
        assert bounding_tools(found.description, ["agent", "tools"]) == {
            "pay_invoice",
            "get_invoice",
        }

    def test_a_cycle_through_nodes_that_bind_nothing_has_no_bounding_tool(self) -> None:
        found = introspect(nodeless_graph())
        assert bounding_tools(found.description, ["a", "b"]) == set()

    def test_a_node_that_is_itself_a_tool_bounds_its_own_cycle(self) -> None:
        # The raw-SDK shape: `describe()` names a loop over the tool, and there is no node.
        declared = AgentDescription(tools=["do_thing"], cycles=[["do_thing"]])
        assert bounding_tools(declared, ["do_thing"]) == {"do_thing"}

    def test_edges_given_as_plain_tuples_read_the_same(self) -> None:
        found = introspect(tuple_graph())
        assert found.depth is Depth.TOPOLOGY
        assert found.description.cycles == [["agent", "tools"]]
        assert found.description.tools == ["search_direct_flight"]

    def test_a_graph_with_no_interrupts_still_reaches_topology_and_says_what_it_missed(
        self,
    ) -> None:
        found = introspect(tuple_graph())
        assert found.depth is Depth.TOPOLOGY
        assert found.description.hitl_points == []
        assert "interrupt_before" in found.note

    def test_a_graph_with_no_readable_nodes_says_tool_names_were_not_visible(self) -> None:
        found = introspect(nodeless_graph())
        assert found.depth is Depth.TOPOLOGY
        assert found.description.tools == []
        assert "tool binding" in found.note

    def test_a_get_graph_that_raises_degrades_instead_of_crashing_lint(self) -> None:
        found = introspect(BrokenCompiled())
        assert found.depth is Depth.NONE
        assert found.source == "none"

    def test_a_get_graph_answering_with_the_wrong_shape_degrades_too(self) -> None:
        found = introspect(ShapelessCompiled())
        assert found.depth is Depth.NONE

    def test_an_acyclic_graph_reports_topology_with_no_cycles(self) -> None:
        found = introspect(acyclic_graph())
        assert found.depth is Depth.TOPOLOGY
        assert found.description.cycles == []


class TestDeclaredCyclesAreTheAuthors:
    def test_a_declared_cycle_list_is_taken_verbatim(self) -> None:
        """An author who wrote down their own circuits meant those, not the SCC over them.

        The figure-eight above collapses to one component; an author naming both loops
        must not have that replaced by a derivation.
        """
        declared = AgentDescription(
            edges=[("a", "b"), ("b", "a"), ("b", "c"), ("c", "b")],
            cycles=[["a", "b"], ["b", "c"]],
        )
        found = introspect(Declared(declared))
        assert found.description.cycles == [["a", "b"], ["b", "c"]]

    def test_an_undeclared_cycle_list_is_derived_from_the_edges(self) -> None:
        found = introspect(Declared(AgentDescription(edges=[("a", "b"), ("b", "a")])))
        assert found.description.cycles == [["a", "b"]]

    def test_a_describable_with_no_edges_gets_no_invented_cycles(self) -> None:
        assert introspect(refusing_agent()).description.cycles == []


class TestADescribeThatMisbehaves:
    """A probe never crashes lint, and a user's broken definition is not specdeck breaking."""

    def test_a_describe_that_raises_degrades_and_says_what_it_raised(self) -> None:
        found = introspect(BrokenDescribeAgent(), reference="m:a")
        assert found.depth is Depth.NONE
        assert found.source == "describe()"
        assert "RuntimeError" in found.note and "request time" in found.note

    def test_a_describe_answering_with_a_dict_degrades_rather_than_being_trusted(self) -> None:
        # `runtime_checkable` checks attribute presence, so this object is Describable.
        found = introspect(DictDescribeAgent(), reference="m:a")
        assert found.depth is Depth.NONE
        assert found.description == AgentDescription()
        assert "dict" in found.note


class TestTheReferenceIsRecorded:
    def test_every_outcome_carries_the_reference_it_was_asked_about(self) -> None:
        for target in (refund_graph(), refusing_agent(), object()):
            assert introspect(target, reference="a:b").reference == "a:b"

    def test_an_introspection_round_trips_as_json(self) -> None:
        # It hangs off `lint.Result` and off the coverage report, both of which serialise.
        found = introspect(refund_graph())
        assert Introspection.model_validate_json(found.model_dump_json()) == found
