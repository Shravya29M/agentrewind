"""The greedy span-alignment walk in `_diff_children` is the part of the diff
most likely to mislead: when two runs take different shapes, which side gets
reported as the insertion decides whether the first divergence points at the
real cause. These tests pin that behaviour down.
"""

import agentrewind as al
from agentrewind.diff import Divergence, diff_traces, format_divergences


def build(steps, root="agent"):
    """Build a trace from a list of (name, kind, input, output) tuples."""
    with al.trace(root) as t:
        for name, kind, inp, out in steps:
            with al.span(name, kind=kind, input=inp) as s:
                s.output = out
    return t


def step(name, kind="tool", inp=None, out=None):
    return (name, kind, inp or {}, out or {})


def kinds(divs):
    return [d.kind for d in divs]


def paths(divs):
    return [d.path for d in divs]


# --------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------


def test_a_span_inserted_in_the_right_run_is_reported_as_right_only():
    left = build([step("a"), step("c")])
    right = build([step("a"), step("b"), step("c")])
    divs = diff_traces(left, right)
    assert kinds(divs) == ["structure"]
    assert divs[0].detail == "only in right run"
    assert divs[0].path.endswith("tool:b")


def test_a_span_dropped_from_the_right_run_is_reported_as_left_only():
    left = build([step("a"), step("b"), step("c")])
    right = build([step("a"), step("c")])
    divs = diff_traces(left, right)
    assert kinds(divs) == ["structure"]
    assert divs[0].detail == "only in left run"
    assert divs[0].path.endswith("tool:b")


def test_a_swapped_span_is_reported_as_a_positional_difference():
    """Neither label appears later on the other side, so the runs genuinely
    diverge at this position rather than one having inserted a step."""
    left = build([step("a"), step("b")])
    right = build([step("a"), step("z")])
    divs = diff_traces(left, right)
    assert divs[0].detail == "different span at same position"
    assert "tool:b vs tool:z" in divs[0].path


def test_trailing_spans_in_the_left_run_are_reported():
    left = build([step("a"), step("b"), step("c")])
    right = build([step("a")])
    divs = diff_traces(left, right)
    assert [d.detail for d in divs] == ["only in left run"] * 2


def test_trailing_spans_in_the_right_run_are_reported():
    left = build([step("a")])
    right = build([step("a"), step("b"), step("c")])
    divs = diff_traces(left, right)
    assert [d.detail for d in divs] == ["only in right run"] * 2


def test_same_name_different_kind_does_not_align():
    left = build([step("fetch", kind="tool")])
    right = build([step("fetch", kind="llm")])
    divs = diff_traces(left, right)
    assert divs[0].kind == "structure"


def test_the_first_divergence_is_the_earliest_one():
    left = build([step("a", out={"v": 1}), step("b", out={"v": 2})])
    right = build([step("a", out={"v": 9}), step("b", out={"v": 8})])
    divs = diff_traces(left, right)
    assert divs[0].path.endswith("tool:a")
    assert divs[0].left == {"v": 1}
    assert divs[0].right == {"v": 9}


def test_structure_divergence_carries_the_input_of_the_side_it_came_from():
    left = build([step("a")])
    right = build([step("a"), step("b", inp={"q": "extra"})])
    divs = diff_traces(left, right)
    assert divs[0].right == {"q": "extra"}
    assert divs[0].left is None


# --------------------------------------------------------------------------
# field-level divergence
# --------------------------------------------------------------------------


def test_input_divergence_is_reported_separately_from_output():
    left = build([step("a", inp={"q": "x"}, out={"v": 1})])
    right = build([step("a", inp={"q": "y"}, out={"v": 2})])
    assert kinds(diff_traces(left, right)) == ["input", "output"]


def test_key_order_in_inputs_is_not_a_divergence():
    left = build([step("a", inp={"x": 1, "y": 2})])
    right = build([step("a", inp={"y": 2, "x": 1})])
    assert diff_traces(left, right) == []


def test_a_failed_span_diverges_from_a_successful_one():
    with al.trace("agent") as left:
        with al.span("a", kind="tool", input={}):
            pass
    with al.trace("agent") as right:
        try:
            with al.span("a", kind="tool", input={}):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
    divs = diff_traces(left, right)
    assert "status" in kinds(divs)


def test_nested_children_are_walked_under_their_parent_path():
    with al.trace("agent") as left:
        with al.span("outer", kind="span", input={}):
            with al.span("inner", kind="llm", input={}) as s:
                s.output = {"v": 1}
    with al.trace("agent") as right:
        with al.span("outer", kind="span", input={}):
            with al.span("inner", kind="llm", input={}) as s:
                s.output = {"v": 2}
    divs = diff_traces(left, right)
    assert divs[0].path == "/span:outer/llm:inner"


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------


def test_format_reports_the_divergence_count():
    left = build([step("a", out={"v": 1})])
    right = build([step("a", out={"v": 2})])
    text = format_divergences(diff_traces(left, right))
    assert text.startswith("1 divergence(s)")


def test_format_truncates_long_values():
    divs = [Divergence("output", "/a", "outputs differ", left={"v": "x" * 500}, right=None)]
    text = format_divergences(divs, max_value_len=40)
    assert "…" in text
    assert "x" * 500 not in text


def test_format_omits_a_side_that_has_no_value():
    divs = [Divergence("structure", "/a", "only in left run", left={"v": 1}, right=None)]
    text = format_divergences(divs)
    assert "left " in text
    assert "right:" not in text


def test_format_numbers_each_divergence():
    left = build([step("a", out={"v": 1}), step("b", out={"v": 1})])
    right = build([step("a", out={"v": 2}), step("b", out={"v": 2})])
    text = format_divergences(diff_traces(left, right))
    assert "1. [output]" in text
    assert "2. [output]" in text
