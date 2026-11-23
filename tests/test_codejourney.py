import html
import re
import sys

import pytest

import codejourney
from codejourney import CollectionSnapshot, ObjectSnapshot, TraceResult, run_viz


def test_run_viz_returns_structured_trace_for_code():
    result = run_viz("answer = 40\nanswer += 2\n")

    assert isinstance(result, TraceResult)
    line_events = [event for event in result.events if event.event == "line"]
    assert [event.line_number for event in line_events] == [1, 2]
    assert [event.source_line for event in line_events] == ["answer = 40", "answer += 2"]
    assert "answer" in line_events[1].changed_variables
    assert line_events[1].after["answer"] == 40
    assert result.events[-1].event == "return"
    assert result.events[-1].after["answer"] == 42
    assert result.error is None
    assert result.truncated is False
    assert "<!doctype html>" not in result._repr_html_().lower()


def test_source_lines_jump_to_exact_or_nearest_execution_step():
    rendered = run_viz("first = 1\nsecond = 2\nprint(second)\n")._repr_html_()

    assert 'role="link" aria-label="Jump to line 1"' in rendered
    assert 'title="Jump to nearest step for line 1"' in rendered
    assert 'class="cj-source-line cj-current"' in rendered
    assert 'class="cj-source-line"' in rendered
    assert re.search(r'class="cj-source-line[^"]*"[^>]*for="[^"]+-step-\d+"', rendered)
    assert "cursor: pointer" in rendered
    assert ".codejourney .cj-source-line:hover" in rendered


def test_run_viz_traces_function_calls_and_source_lines():
    result = run_viz("def add(a, b):\n    return a + b\n\nanswer = add(2, 3)\n")

    line_events = [event for event in result.events if event.event == "line"]
    assert any(event.line_number == 2 for event in line_events)
    assert any(event.line_number == 4 for event in line_events)
    assert line_events[-1].line_number == 2
    assert result.events[-1].after["answer"] == 5


def test_collection_values_are_snapshotted_and_mutations_are_detected():
    result = run_viz("items = [1]\nitems.append(2)\n")
    events = [event for event in result.events if event.event == "line"]

    assert events[1].after["items"] == [1]
    final_event = result.events[-1]
    assert final_event.before["items"] == [1]
    assert final_event.after["items"] == [1, 2]
    assert "items" in final_event.changed_variables


def test_dictionary_snapshots_use_structured_data():
    result = run_viz("data = {'a': 1}\ndata['b'] = 2\n")
    snapshots = [event.after["data"] for event in result.events if "data" in event.after]

    assert snapshots
    assert isinstance(snapshots[-1], CollectionSnapshot)
    assert snapshots[-1].items == (("a", 1), ("b", 2))


def test_unsupported_values_do_not_call_student_repr():
    class Unprintable:
        def __repr__(self):
            raise AssertionError("repr should not run")

    value = Unprintable()
    snapshot = codejourney._snapshot_value(value)

    assert snapshot == ObjectSnapshot("Unprintable")


def test_max_steps_truncates_execution_at_the_configured_limit():
    result = run_viz("count = 0\nwhile True:\n    count += 1\n", max_steps=5)

    assert result.truncated is True
    assert sum(event.event == "line" for event in result.events) == 5
    assert result.error is None
    assert all(
        event.exception_type != "_StepLimitReached"
        for event in result.events
        if event.event == "exception"
    )


def test_default_max_steps_is_5000():
    result = run_viz("count = 0\nwhile True:\n    count += 1\n")

    assert result.max_steps == 5000
    assert result.truncated is True


@pytest.mark.parametrize("value", [0, -1])
def test_max_steps_must_be_positive(value):
    with pytest.raises(ValueError, match="positive"):
        run_viz("x = 1", max_steps=value)


@pytest.mark.parametrize("value", [True, 1.5, "2", None])
def test_max_steps_must_be_an_integer(value):
    with pytest.raises(TypeError, match="integer"):
        run_viz("x = 1", max_steps=value)


def test_code_str_must_be_a_string():
    with pytest.raises(TypeError, match="code_str"):
        run_viz(123)


def test_syntax_error_is_returned_as_structured_data():
    result = run_viz("if True\n    pass\n")

    assert result.error is not None
    assert result.error.type_name == "SyntaxError"
    assert result.error.line_number == 1
    assert result.events == ()


def test_runtime_error_keeps_partial_trace():
    result = run_viz("value = 1\nraise ValueError('bad input')\n")

    assert result.error is not None
    assert result.error.type_name == "ValueError"
    assert "bad input" in result.error.message
    assert any(event.line_number == 1 for event in result.events)
    assert any(event.event == "exception" for event in result.events)


def test_custom_exception_stringification_is_not_executed():
    result = run_viz(
        "class UnsafeError(Exception):\n"
        "    def __str__(self):\n"
        "        raise AssertionError('must not stringify')\n"
        "raise UnsafeError('safe message')\n"
    )

    assert result.error is not None
    assert result.error.type_name == "UnsafeError"
    assert result.error.message == "safe message"


def test_previous_trace_function_is_restored(monkeypatch):
    previous_trace = lambda frame, event, arg: previous_trace
    monkeypatch.setattr(sys, "gettrace", lambda: previous_trace)
    installed = []
    monkeypatch.setattr(sys, "settrace", lambda callback: installed.append(callback))

    result = run_viz("answer = 1\n")

    assert result.error is None
    assert installed[0] is not previous_trace
    assert installed[-1] is previous_trace


def test_magic_registration_is_optional_and_registers_cell_magic(monkeypatch):
    class FakeShell:
        def __init__(self):
            self.registration = None

        def register_magic_function(self, function, magic_kind, magic_name):
            self.registration = (function, magic_kind, magic_name)

    shell = FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)
    codejourney._register_pyviz_magic()

    assert shell.registration == (codejourney.pyviz, "cell", "pyviz")


def test_cell_magic_returns_the_structured_trace():
    result = codejourney.pyviz("", "answer = 42")

    assert isinstance(result, codejourney.TraceResult)
    assert result.events[-1].after["answer"] == 42


def test_cell_magic_renders_html_without_debug_output(capsys):
    result = codejourney.pyviz("", "answer = 42")
    rendered = result._repr_html_()
    output = capsys.readouterr().out

    assert "class=\"cj-view\"" in rendered
    assert output == ""


def test_cell_magic_captures_stepwise_stdout_in_top_output_card(capsys):
    result = codejourney.pyviz("", "print('first')\nprint('second')\n")
    rendered = result._repr_html_()
    normal_output = capsys.readouterr().out

    assert normal_output == ""
    output_card = '<section class="cj-output-card" aria-label="Output">'
    assert output_card in rendered
    assert rendered.index('<div class="cj-timeline"') < rendered.index(output_card)
    assert rendered.index(output_card) < rendered.index('<div class="cj-layout">')
    assert "class=\"cj-output-state\" data-output-index=\"0\"" in rendered
    assert "first\nsecond\n" in rendered
    assert ".codejourney .cj-output-card" in rendered
    assert "background: #fff !important; color: #172033 !important" in rendered
    assert "background: #f8fafc !important; color: #172033 !important" in rendered
    assert result.events[-1].output == "first\nsecond\n"
    assert any(event.output == "first\n" for event in result.events)


def test_error_learning_note_starts_at_exception_step():
    result = run_viz("answer = 1\nraise ValueError('bad input')\n")
    rendered = result._repr_html_()
    exception_index = next(
        index
        for index, event in enumerate(result.events)
        if event.event == "exception" and event.exception_type == "ValueError"
    )
    views = re.findall(
        r'<section class="cj-view" data-index="(\d+)">(.*?)(?=<section class="cj-view"|<p class="cj-muted">Educational trace)',
        rendered,
        re.DOTALL,
    )

    assert views
    assert all(
        ("ValueError: bad input" in view) == (int(index) >= exception_index)
        for index, view in views
    )


def test_timeline_marks_final_state_as_end_and_keeps_navigation():
    rendered = run_viz("first = 1\nsecond = 2\n")._repr_html_()
    ticks = re.findall(r'<label class="cj-tick"[^>]*>(.*?)</label>', rendered)

    assert ticks[-1] == "END"
    assert "aria-label=\"Go to END\" title=\"END\"" in rendered
    assert f"END / {len(ticks)}" in rendered
    assert "aria-label=\"First step\"" in rendered
    assert "aria-label=\"Previous step\"" in rendered
    assert "aria-label=\"Next step\"" in rendered
    assert "aria-label=\"Last step\"" in rendered
    assert "⏮" in rendered
    assert "◄" in rendered
    assert "►" in rendered
    assert "⏭" in rendered
    first = rendered.index("⏮")
    previous = rendered.index("◄")
    next_arrow = rendered.index("►")
    last = rendered.index("⏭")
    assert first < previous < next_arrow < last


def test_objects_use_content_sized_wrapping_layout_and_compact_density():
    rendered = run_viz(
        "items = [1, 2]\n"
        "data = {'a': 1}\n"
    )._repr_html_()

    assert "font: 13px/1.35" in rendered
    assert "padding: 6px;" in rendered
    assert "max-height: 120px" in rendered
    assert "max-height: 360px" in rendered
    assert ".cj-objects { display: flex;" in rendered
    assert "flex-wrap: wrap" in rendered
    assert "align-items: flex-start" in rendered
    assert "gap: 8px" in rendered
    assert ".cj-object { width: max-content;" in rendered
    assert "max-width: 100%" in rendered
    assert "box-sizing: border-box" in rendered
    assert "table-layout: auto" in rendered
    assert "white-space: nowrap" in rendered
    assert ".cj-table-scroll { max-width: 100%; overflow-x: auto;" in rendered
    assert '<div class="cj-table-scroll"><div class="cj-table-inner">' in rendered


def test_fade_animation_is_limited_to_new_elements():
    rendered = run_viz("items = [1]\nprint(items)\n")._repr_html_()

    assert "@media (prefers-reduced-motion: no-preference)" in rendered
    assert "@keyframes cj-pop-fade" in rendered
    assert ".cj-object.cj-new" in rendered
    assert ".cj-diff.cj-new" in rendered
    assert ".cj-output-state.cj-new pre" in rendered
    assert ".cj-condition-card.cj-new" in rendered
    assert ".cj-warning.cj-new" in rendered
    assert ".cj-error.cj-new" in rendered
    assert "cj-view" in rendered
    assert "animation:cj-step-enter" not in rendered
    assert "animation:cj-soft-enter" not in rendered
    assert "animation:cj-pill-enter" not in rendered
    assert 'animation: cj-object-fade' not in rendered
    assert "<script>" not in rendered
    assert "ResizeObserver" not in rendered


def test_visualizer_initializes_at_step_one():
    rendered = run_viz("first = 1\nsecond = 2\n")._repr_html_()
    selectors = re.findall(r'<input class="cj-selector"[^>]*>', rendered)
    ticks = re.findall(
        r'<label class="cj-tick" role="button" for="([^"]+)"[^>]*>(.*?)</label>',
        rendered,
    )

    assert len(selectors) > 1
    assert " checked>" in selectors[0]
    assert all(" checked>" not in selector for selector in selectors[1:])
    assert ticks[0][1] == "1"
    assert ticks[-1][1] == "END"
    scope = ticks[0][0].rsplit("-step-", 1)[0]
    assert f"#{scope}-step-0:checked ~ .cj-timeline" in rendered
    assert rendered.count(":checked ~ .cj-timeline") == len(ticks)


def test_visualizer_includes_reduced_motion_safe_new_item_animation():
    rendered = run_viz(
        "items = [1, 2]\n"
        "print(items)\n"
        "if 1 == 1 and 2 == 2:\n"
        "    answer = 42\n"
    )._repr_html_()

    assert "@media (prefers-reduced-motion: no-preference)" in rendered
    assert "@keyframes cj-pop-fade" in rendered
    assert "@keyframes cj-step-enter" not in rendered
    assert "@keyframes cj-soft-enter" not in rendered
    assert "@keyframes cj-pill-enter" not in rendered
    assert "from { opacity: 0; transform: translateY(3px); }" in rendered
    assert "to { opacity: 1; transform: translateY(0); }" in rendered
    assert ".cj-object.cj-new" in rendered


def test_ast_analysis_extracts_loop_metadata_and_known_iteration_count():
    result = run_viz("total = 0\nfor value in range(2, 5):\n    total += value\n")

    assert len(result.loops) == 1
    loop = result.loops[0]
    assert loop.kind == "for"
    assert loop.line_number == 2
    assert loop.total_iterations == 3
    assert loop.header == "for value in range(2, 5)"


def test_for_loop_iterations_have_boundaries_and_beginner_labels():
    result = run_viz("total = 0\nfor value in [2, 4]:\n    total += value\n")
    iterations = [
        iteration
        for event in result.events
        for iteration in event.loop_iterations
        if event.line_number == 3
    ]
    starts = [
        boundary
        for event in result.events
        for boundary in event.loop_boundaries
        if boundary.kind == "start"
    ]
    ends = [
        boundary
        for event in result.events
        for boundary in event.loop_boundaries
        if boundary.kind == "end"
    ]

    assert [item.iteration_index for item in iterations] == [0, 1]
    assert [item.label for item in iterations] == ["Iteration 1 of 2", "Iteration 2 of 2"]
    assert [boundary.iteration_index for boundary in starts] == [0, 1]
    assert [boundary.iteration_index for boundary in ends] == [0, 1]
    assert all(
        iteration.iteration_index in (0, 1)
        for event in result.events
        for iteration in event.loop_iterations
    )


def test_while_loop_tracks_unknown_total_and_condition_outcomes():
    result = run_viz("count = 0\nwhile count < 2:\n    count += 1\n")

    loop = result.loops[0]
    assert loop.kind == "while"
    assert loop.total_iterations is None
    previews = [
        event.condition_preview
        for event in result.events
        if event.condition_preview is not None
    ]
    assert [preview.result for preview in previews] == [True, True, False]
    assert previews[0].preview == "count < 2 -> 0 < 2 -> True"
    assert previews[-1].preview == "count < 2 -> 2 < 2 -> False"


def test_if_and_elif_conditions_are_extracted_and_previewed():
    result = run_viz(
        "number = 3\n"
        "if number < 0:\n"
        "    label = 'negative'\n"
        "elif number > 2:\n"
        "    label = 'large'\n"
        "else:\n"
        "    label = 'small'\n"
    )
    previews = [
        event.condition_preview
        for event in result.events
        if event.condition_preview is not None
    ]

    assert [preview.expression for preview in previews] == ["number < 0", "number > 2"]
    assert [preview.result for preview in previews] == [False, True]
    assert previews[1].preview == "number > 2 -> 3 > 2 -> True"


def test_condition_preview_never_calls_condition_function_again():
    result = run_viz(
        "calls = 0\n"
        "def check():\n"
        "    global calls\n"
        "    calls += 1\n"
        "    return True\n"
        "if check():\n"
        "    answer = 1\n"
    )

    assert result.events[-1].after["calls"] == 1
    condition = next(event.condition_preview for event in result.events if event.condition_preview)
    assert condition.result is True
    assert "value unavailable" in condition.preview


def test_raised_condition_is_not_misreported_as_false():
    result = run_viz(
        "def check():\n"
        "    raise RuntimeError('condition failed')\n"
        "if check():\n"
        "    answer = 1\n"
    )
    condition = next(event.condition_preview for event in result.events if event.condition_preview)

    assert result.error is not None
    assert condition.result is None
    assert condition.preview == "check() (value unavailable)"


def test_condition_evaluation_omits_duplicate_expression():
    result = run_viz("if 1 == 1 and 1 == 1:\n    answer = 1\n")
    condition = next(
        event.condition_preview for event in result.events if event.condition_preview
    )
    rendered = result._repr_html_()

    assert condition.preview == "1 == 1 and 1 == 1 -> True"
    assert "1 == 1 and 1 == 1 -&gt; True" in rendered
    assert (
        "1 == 1 and 1 == 1 -&gt; 1 == 1 and 1 == 1 -&gt; True"
        not in rendered
    )


@pytest.mark.parametrize(
    ("expression", "expected_steps", "expected_statuses", "expected_values"),
    [
        (
            "1 == 2 and 1 == 1",
            ["1 == 2", "1 == 1"],
            ["evaluated", "skipped"],
            ["False", "Not evaluated"],
        ),
        (
            "1 == 1 and 2 == 2",
            ["1 == 1", "2 == 2"],
            ["evaluated", "evaluated"],
            ["True", "True"],
        ),
        (
            "1 == 1 or 2 == 2",
            ["1 == 1", "2 == 2"],
            ["evaluated", "skipped"],
            ["True", "Not evaluated"],
        ),
        (
            "1 == 2 or 2 == 2",
            ["1 == 2", "2 == 2"],
            ["evaluated", "evaluated"],
            ["False", "True"],
        ),
    ],
)
def test_boolean_condition_previews_show_short_circuit_steps(
    expression, expected_steps, expected_statuses, expected_values
):
    result = run_viz(f"if {expression}:\n    answer = 1\n")
    condition = next(
        event.condition_preview for event in result.events if event.condition_preview
    )

    assert [step.expression for step in condition.evaluations] == expected_steps
    assert [step.status for step in condition.evaluations] == expected_statuses
    assert [step.value for step in condition.evaluations] == expected_values
    rendered = result._repr_html_()
    assert "SHORT-CIRCUIT STEPS" in rendered
    assert "cj-condition-part-evaluated" in rendered
    assert "Step 1:" in rendered
    assert "box-shadow: none !important" in rendered
    assert "background: transparent !important" in rendered
    assert "border-left: 4px solid #2563eb !important" in rendered
    if expected_statuses[1] == "skipped":
        assert condition.evaluations[1].note.startswith("Short-circuited:")
        assert "cj-condition-part-skipped" in rendered
    else:
        assert "evaluates the next operand" in condition.evaluations[1].note


def test_unsupported_boolean_operand_is_not_reexecuted_for_preview():
    result = run_viz(
        "calls = 0\n"
        "def check():\n"
        "    global calls\n"
        "    calls += 1\n"
        "    return True\n"
        "if check() and 1 == 1:\n"
        "    answer = 1\n"
    )
    condition = next(
        event.condition_preview for event in result.events if event.condition_preview
    )

    assert result.events[-1].after["calls"] == 1
    assert [step.status for step in condition.evaluations] == ["unknown", "unknown"]


def test_variable_diffs_identify_added_modified_and_removed_variables():
    result = run_viz("value = 1\nvalue = 2\ndel value\n")
    diffs = [
        diff
        for event in result.events
        for diff in event.variable_diffs
        if diff.name == "value"
    ]

    assert [diff.change for diff in diffs] == ["added", "modified", "removed"]


def test_list_mutation_diff_reports_affected_indices():
    result = run_viz("items = [1, 2]\nitems[1] = 9\n")
    diffs = [
        diff
        for event in result.events
        for diff in event.variable_diffs
        if diff.name == "items" and diff.list_indices
    ]

    assert diffs[-1].list_indices == (1,)


def test_list_append_reports_new_index():
    result = run_viz("items = [1]\nitems.append(2)\n")
    diffs = [
        diff
        for event in result.events
        for diff in event.variable_diffs
        if diff.name == "items" and diff.list_indices
    ]

    assert diffs[-1].list_indices == (1,)


def test_dictionary_mutation_diff_reports_affected_keys():
    result = run_viz("data = {'a': 1}\ndata['b'] = 2\ndata['a'] = 3\n")
    diffs = [
        diff
        for event in result.events
        for diff in event.variable_diffs
        if diff.name == "data" and diff.dictionary_keys
    ]

    assert diffs[0].dictionary_keys == ("b",)
    assert diffs[1].dictionary_keys == ("a",)


def test_aliases_are_detected_by_shared_identity():
    result = run_viz("first = []\nsecond = first\n")
    aliases = [alias for event in result.events for alias in event.aliases]

    assert any(alias.name == "second" and alias.linked_to == "first" for alias in aliases)
    assert any(alias.label == "Linked to first" for alias in aliases)


def test_equal_but_separate_lists_are_not_reported_as_aliases():
    result = run_viz("first = [1]\nsecond = [1]\n")

    assert all(not event.aliases for event in result.events)


def test_list_mutation_during_iteration_warning_is_reported():
    result = run_viz("items = [1]\nfor item in items:\n    items.append(item)\n", max_steps=8)

    warning = next(
        warning
        for warning in result.warnings
        if warning.code == "list-mutated-during-iteration"
    )
    assert warning.line_number == 3


def test_deleting_list_items_while_iterating_is_warned():
    result = run_viz(
        "items = [1, 2]\n"
        "for index, item in enumerate(items):\n"
        "    del items[index]\n",
        max_steps=12,
    )

    assert any(
        warning.code == "list-mutated-during-iteration"
        for warning in result.warnings
    )


def test_break_and_return_on_first_iteration_warnings_are_observed():
    break_result = run_viz("for item in [1, 2]:\n    break\n")
    return_result = run_viz(
        "def stop():\n"
        "    for item in [1, 2]:\n"
        "        return item\n"
        "stop()\n"
    )

    assert any(warning.code == "break-first-iteration" for warning in break_result.warnings)
    assert any(warning.code == "return-first-iteration" for warning in return_result.warnings)


def test_builtin_shadowing_warning_is_detected():
    result = run_viz("list = [1, 2]\n")

    warning = next(
        warning for warning in result.warnings if warning.code == "builtin-shadowing"
    )
    assert "'list' shadows a Python built-in" in warning.message


def test_import_alias_can_shadow_a_builtin():
    result = run_viz("from math import ceil as list\n")

    assert any(warning.code == "builtin-shadowing" for warning in result.warnings)


def test_nested_loops_keep_iteration_context_for_each_loop():
    result = run_viz(
        "total = 0\n"
        "for outer in [1, 2]:\n"
        "    for inner in [3, 4]:\n"
        "        total += outer * inner\n"
    )
    body_event = next(event for event in result.events if event.line_number == 4)

    assert len(body_event.loop_iterations) == 2
    assert [item.iteration_index for item in body_event.loop_iterations] == [0, 0]
    assert body_event.loop_iterations[0].loop_id != body_event.loop_iterations[1].loop_id
    inner_groups = {
        event.loop_iterations[-1].group_id
        for event in result.events
        if event.line_number == 4 and len(event.loop_iterations) == 2
    }
    assert len(inner_groups) == 2


def test_triple_nested_loops_track_depth_and_reset_inner_counters():
    result = run_viz(
        "total = 0\n"
        "for outer in [1, 2]:\n"
        "    for middle in [3, 4]:\n"
        "        for inner in [5, 6]:\n"
        "            total += outer + middle + inner\n"
    )
    body_events = [
        event
        for event in result.events
        if event.line_number == 5 and len(event.loop_iterations) == 3
    ]

    assert body_events
    assert all(
        [item.depth for item in event.loop_iterations] == [0, 1, 2]
        for event in body_events
    )
    combinations = [
        tuple(item.iteration_index for item in event.loop_iterations)
        for event in body_events
    ]
    assert (0, 0, 0) in combinations
    assert (1, 0, 0) in combinations
    assert (1, 1, 1) in combinations


def test_deep_loop_stack_compacts_middle_levels_in_rendered_badge():
    result = run_viz(
        "total = 0\n"
        "for a in [1, 2]:\n"
        "    for b in [1, 2]:\n"
        "        for c in [1, 2]:\n"
        "            for d in [1, 2]:\n"
        "                total += a + b + c + d\n"
    )
    rendered = result._repr_html_()

    assert "cj-loop-stack" in rendered
    assert "Iteration " in rendered
    assert " → … → " in rendered
    assert 'title="L2:' in rendered


def test_aliases_do_not_append_to_iteration_badge():
    result = run_viz("a = []\nb = a\nc = b\n")
    rendered = result._repr_html_()
    badges = re.findall(r'<span class="cj-badge[^"]*">(.*?)</span>', rendered)

    assert all("Linked to" not in badge for badge in badges)
    assert "Linked to a" in rendered


def test_html_visualizer_uses_notebook_safe_controls_and_escapes_source():
    result = run_viz(
        "value = '<script>alert(1)</script>'\n"
        "items = []\n"
        "alias = items\n"
        "for i in [1, 2]:\n"
        "    value = i\n"
    )
    rendered = result._repr_html_()

    assert 'type="radio"' in rendered
    assert 'role="group" aria-label="Execution timeline"' in rendered
    assert 'class="cj-tick" role="button"' in rendered
    assert ':checked ~ .cj-controls[data-step-controls=\'0\']' in rendered
    assert "Jump to Next Change" in rendered
    assert "Prev Iteration" in rendered
    assert "Next Iteration" in rendered
    assert "Iteration" in rendered
    assert "What changed?" in rendered
    assert "cj-current" in rendered
    assert ".codejourney .cj-source {" in rendered
    assert "background: #ffffff !important" in rendered
    assert "color: #172033 !important" in rendered
    assert "color-scheme: light" in rendered
    assert "background: #fff0b3 !important" in rendered
    assert "Linked to items" in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert "<script>" not in rendered.lower()
    assert "application/javascript" not in rendered


def test_html_escapes_warning_and_error_text():
    result = run_viz("raise ValueError('<img src=x onerror=alert(1)>')\n")
    rendered = result._repr_html_()

    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered
    assert "<img src=x onerror=alert(1)>" not in rendered
    assert "Educational trace, not a debugger" not in rendered


def test_empty_source_still_renders_controls_and_empty_state():
    rendered = run_viz("")._repr_html_()

    assert "Timeline" not in rendered
    assert "No execution steps captured." in rendered


def test_html_has_variables_and_object_boxes_for_python_objects():
    result = run_viz("items = [1, 2]\nalias = items\n")
    rendered = result._repr_html_()

    pointer = re.search(
        r'<a class="cj-pointer" href="#([^"]+)"[^>]*>&rarr; object 1</a>',
        rendered,
    )
    object_box = re.search(r'<article class="cj-object[^"]*" id="([^"]+)"', rendered)
    assert pointer is not None
    assert object_box is not None
    assert pointer.group(1) == object_box.group(1)
    assert "items" in rendered
    assert "alias" in rendered
    assert '<span class="cj-object-type">list</span>' in rendered
    assert '<th scope="col">Index</th><th scope="col" class="cj-value-column">Value</th>' in rendered
    assert '<th scope="row">0</th><td class="cj-value-column">1</td>' in rendered
    assert '<th scope="row">1</th><td class="cj-value-column">2</td>' in rendered


def test_list_dictionary_and_set_objects_render_as_typed_tables():
    rendered = html.unescape(
        run_viz(
            "items = [1, print]\n"
            "data = {'a': 1, 'b': 2}\n"
            "unique = {8, 9}\n"
        )._repr_html_()
    )
    cards = re.findall(
        r'<article class="cj-object[^"]*" id="[^"]+">(.*?)</article>',
        rendered,
        re.DOTALL,
    )
    list_card = next(card for card in cards if 'cj-object-type">list<' in card)
    dict_card = next(card for card in cards if 'cj-object-type">dict<' in card)
    set_card = next(card for card in cards if 'cj-object-type">set<' in card)

    assert '<h4>Object 1</h4><span class="cj-object-type">list</span>' in list_card
    assert '<th scope="col">Index</th><th scope="col" class="cj-value-column">Value</th>' in list_card
    assert '<th scope="row">0</th><td class="cj-value-column">1</td>' in list_card
    assert '<th scope="row">1</th><td class="cj-value-column">print()</td>' in list_card
    assert '<th scope="col">Key</th><th scope="col" class="cj-value-column">Value</th>' in dict_card
    assert "<th scope=\"row\">'a'</th><td class=\"cj-value-column\">1</td>" in dict_card
    assert "<th scope=\"row\">'b'</th><td class=\"cj-value-column\">2</td>" in dict_card
    assert '<th scope="col" class="cj-value-column">Value</th>' in set_card
    assert '<td class="cj-value-column">8</td>' in set_card
    assert '<td class="cj-value-column">9</td>' in set_card
    assert "Index" not in set_card
    assert not any(bracket in set_card for bracket in "{}[]")
    assert not any(bracket in list_card for bracket in "[]")
    assert not any(bracket in dict_card for bracket in "{}")


def test_visualizer_sections_keep_light_surfaces_and_dark_text():
    rendered = run_viz("answer = 42\n")._repr_html_()

    for selector in (
        ".codejourney .cj-panel",
        ".codejourney .cj-output-card",
        ".codejourney .cj-variable",
        ".codejourney .cj-object",
        ".codejourney .cj-diff",
        ".codejourney .cj-condition-card",
    ):
        assert selector in rendered
    assert "color-scheme: light" in rendered
    assert "background: #172033" not in rendered
    assert "background: #0b1220" not in rendered
    assert ".codejourney .cj-scalar" in rendered
    assert ".codejourney .cj-warning" in rendered
    assert ".codejourney .cj-error" in rendered


def test_output_is_above_source_variables_and_outer_container_is_light_blue():
    rendered = run_viz("print('hello')\nanswer = 42\n")._repr_html_()

    output_card = rendered.index('<section class="cj-output-card" aria-label="Output">')
    source_panel = rendered.index('<section class="cj-panel"><h3>Source</h3>')
    variables_panel = rendered.index("<h3>Variables</h3>")

    assert output_card < source_panel
    assert output_card < variables_panel
    assert ".codejourney { background: #eef5ff !important;" in rendered
    assert "background: #eef5ff" in rendered


def test_object_table_value_columns_are_theme_locked():
    rendered = run_viz(
        "items = [1, 2]\n"
        "data = {'a': 3}\n"
        "unique = {4, 5}\n"
    )._repr_html_()

    assert '<th scope="col" class="cj-value-column">Value</th>' in rendered
    assert '<td class="cj-value-column">' in rendered
    assert ".codejourney .cj-object-table .cj-value-column" in rendered
    assert "background: #fff !important" in rendered
    assert "color: #172033 !important" in rendered
    assert "border: 1px solid #cbd5e1 !important" in rendered


def test_collection_values_render_vertically_with_type_appropriate_labels():
    rendered = run_viz(
        "items = [1, 2, 3, 14, print]\n"
        "unique = {8, 9}\n"
        "data = {'a': 1, 'b': 2}\n"
        "items.append(15)\n"
        "unique.add(10)\n"
        "data['c'] = 3\n"
    )._repr_html_()
    formatted = html.unescape(rendered)

    assert "0 -> 1" in formatted
    assert "1 -> 2" in formatted
    assert "2 -> 3" in formatted
    assert "3 -> 14" in formatted
    assert "4 -> print()" in formatted
    assert "'a' -> 1" in formatted
    assert "'b' -> 2" in formatted
    assert "'c' -> 3" in formatted
    assert "{\n  8\n  9\n}" in formatted
    assert "Before: [\n  0 -> 1" in formatted
    assert "After:  [\n  0 -> 1" in formatted
    assert "Before: {\n  8\n  9\n}" in formatted
    assert "Before: {\n  'a' -> 1" in formatted
    assert '{"type": "set"' not in formatted
    assert '{"type": "builtin_function_or_method"}' not in formatted


def test_readme_lists_current_team_members():
    from pathlib import Path

    readme = Path(__file__).resolve().parents[1] / "README.md"
    contents = readme.read_text(encoding="utf-8")
    team = contents.split("## Team", 1)[1].split("## ", 1)[0]

    assert "Eric Yiu" in team
    assert "Yuen Cheng" in team
    assert "Jason Lai" in team
    assert "placeholder" not in team.lower()
