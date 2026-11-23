"""Notebook-safe, CSS-only rendering for CodeJourney trace data."""

from __future__ import annotations

import html
import uuid
from typing import Any, Optional

from . import CollectionSnapshot, ObjectSnapshot, TraceEvent, TraceResult


def _value_text(value: Any, depth: int = 0) -> str:
    if isinstance(value, ObjectSnapshot):
        return f"{value.display_name}()" if value.display_name else value.type_name
    if isinstance(value, CollectionSnapshot):
        if value.type_name == "dict":
            entries = value.items
            opening, closing = "{", "}"
            return _mapping_text(entries, opening, closing, depth)
        if value.type_name in {"set", "frozenset"}:
            if not value.items:
                return "set()" if value.type_name == "set" else "frozenset()"
            body = _sequence_text(value.items, "{", "}", depth, indexed=False)
            return body if value.type_name == "set" else f"frozenset({body})"
        return value.type_name
    if type(value) in (list, tuple):
        opening, closing = ("[", "]") if type(value) is list else ("(", ")")
        if not value and type(value) is tuple:
            return "()"
        if not value:
            return "[]"
        return _sequence_text(value, opening, closing, depth, indexed=True)
    if type(value) is dict:
        return _mapping_text(tuple(value.items()), "{", "}", depth)
    if type(value) is str:
        return repr(value)
    return repr(value)


def _sequence_text(
    items: Any,
    opening: str,
    closing: str,
    depth: int,
    indexed: bool,
) -> str:
    if not items:
        return opening + closing
    indent = "  " * (depth + 1)
    lines = [opening]
    for index, item in enumerate(items):
        rendered = _value_text(item, depth + 1).splitlines()
        label = f"{index} -> " if indexed else ""
        lines.append(indent + label + rendered[0])
        lines.extend(rendered[1:])
    lines.append("  " * depth + closing)
    return "\n".join(lines)


def _mapping_text(items: Any, opening: str, closing: str, depth: int) -> str:
    if not items:
        return opening + closing
    indent = "  " * (depth + 1)
    lines = [opening]
    for key, value in items:
        rendered = _value_text(value, depth + 1).splitlines()
        lines.append(indent + _value_text(key, depth + 1).splitlines()[0] + " -> " + rendered[0])
        lines.extend(rendered[1:])
    lines.append("  " * depth + closing)
    return "\n".join(lines)


def _object_type(value: Any) -> str:
    if isinstance(value, (CollectionSnapshot, ObjectSnapshot)):
        return value.type_name
    return type(value).__name__


def _collection_table(value: Any) -> Optional[str]:
    if type(value) in (list, tuple):
        headings = ("Index", "Value")
        rows = tuple(enumerate(value))
        has_row_heading = True
    elif type(value) is dict:
        headings = ("Key", "Value")
        rows = tuple(value.items())
        has_row_heading = True
    elif type(value) in (set, frozenset):
        headings = ("Value",)
        rows = tuple((item,) for item in value)
        has_row_heading = False
    elif isinstance(value, CollectionSnapshot) and value.type_name == "dict":
        headings = ("Key", "Value")
        rows = value.items
        has_row_heading = True
    elif isinstance(value, CollectionSnapshot) and value.type_name in {"set", "frozenset"}:
        headings = ("Value",)
        rows = tuple((item,) for item in value.items)
        has_row_heading = False
    else:
        return None

    header_html = "".join(
        (
            '<th scope="col" class="cj-value-column">'
            f"{heading}</th>"
            if heading == "Value"
            else f'<th scope="col">{heading}</th>'
        )
        for heading in headings
    )
    row_html = []
    for row in rows:
        cells = (
            f'<th scope="row">{html.escape(_value_text(row[0]))}</th>'
            if has_row_heading
            else ""
        )
        values = row[1:] if has_row_heading else row
        cells += "".join(
            f'<td class="cj-value-column">{_object_value_html(item)}</td>'
            for item in values
        )
        row_html.append(f"<tr>{cells}</tr>")
    table_html = (
        '<table class="cj-object-table"><thead><tr>'
        + header_html
        + "</tr></thead><tbody>"
        + "".join(row_html)
        + "</tbody></table>"
    )
    return (
        '<div class="cj-table-scroll"><div class="cj-table-inner">'
        + table_html
        + "</div></div>"
    )


def _object_value_html(value: Any) -> str:
    nested_table = _collection_table(value)
    if nested_table is not None:
        return nested_table
    return html.escape(_value_text(value))


def _is_object(value: Any) -> bool:
    return type(value) in (list, tuple, dict, set, frozenset) or isinstance(
        value, (CollectionSnapshot, ObjectSnapshot)
    )


def _variable_objects(
    event: TraceEvent,
    scope: str,
    previous_event: Optional["TraceEvent"] = None,
) -> tuple[str, str]:
    aliases = {alias.name: alias.linked_to for alias in event.aliases}
    previous_aliases = (
        {alias.name: alias.linked_to for alias in previous_event.aliases}
        if previous_event is not None
        else {}
    )

    def previous_canonical_names() -> set[str]:
        names = set()
        for name in (previous_event.after if previous_event is not None else {}):
            seen = set()
            canonical = name
            while canonical in previous_aliases and canonical not in seen:
                seen.add(canonical)
                canonical = previous_aliases[canonical]
            names.add(canonical)
        return names

    previous_objects = previous_canonical_names()

    def canonical_name(name: str) -> str:
        seen = set()
        while name in aliases and name not in seen:
            seen.add(name)
            name = aliases[name]
        return name

    object_ids = {}
    object_values = {}
    variable_rows = []
    for name, value in event.after.items():
        safe_name = html.escape(name)
        if _is_object(value):
            canonical = canonical_name(name)
            object_id = object_ids.get(canonical)
            if object_id is None:
                object_id = f"{scope}-object-{len(object_ids) + 1}"
                object_ids[canonical] = object_id
                object_values[canonical] = value
            pointer_label = object_id.rsplit("-object-", 1)[-1]
            alias_label = next(
                (alias.label for alias in event.aliases if alias.name == name),
                None,
            )
            variable_rows.append(
                '<div class="cj-variable">'
                f'<span class="cj-variable-name">{safe_name}</span>'
                f'<a class="cj-pointer" href="#{object_id}" aria-label="View object for {safe_name}">'
                f'&rarr; object {pointer_label}</a>'
                + (
                    f'<span class="cj-alias">{html.escape(alias_label)}</span>'
                    if alias_label is not None
                    else ""
                )
                + "</div>"
            )
        else:
            variable_rows.append(
                '<div class="cj-variable">'
                f'<span class="cj-variable-name">{safe_name}</span>'
                f'<code class="cj-scalar">= {html.escape(_value_text(value))}</code>'
                "</div>"
            )

    if not variable_rows:
        variables_html = '<p class="cj-empty">No variables in this state.</p>'
        objects_html = '<p class="cj-empty">No objects in this state.</p>'
        return variables_html, objects_html

    objects_html = []
    for canonical, value in object_values.items():
        object_id = object_ids[canonical]
        object_number = object_id.rsplit("-object-", 1)[-1]
        table_html = _collection_table(value)
        contents_html = (
            table_html
            if table_html is not None
            else f"<pre>{html.escape(_value_text(value))}</pre>"
        )
        new_class = " cj-new" if canonical not in previous_objects else ""
        objects_html.append(
            f'<article class="cj-object{new_class}" id="{object_id}">'
            '<header class="cj-object-heading">'
            f"<h4>Object {object_number}</h4>"
            f'<span class="cj-object-type">{html.escape(_object_type(value))}</span>'
            "</header>"
            f"{contents_html}"
            "</article>"
        )
    if not objects_html:
        objects_html.append('<p class="cj-empty">No objects in this state.</p>')
    return "\n".join(variable_rows), "\n".join(objects_html)


def _diffs_html(
    event: TraceEvent,
    previous_event: Optional["TraceEvent"] = None,
) -> str:
    if not event.variable_diffs:
        return '<p class="cj-empty">No variable changes at this step.</p>'
    cards = []
    previous_diffs = {
        (
            diff.name,
            diff.change,
            _value_text(diff.before),
            _value_text(diff.after),
        )
        for diff in (previous_event.variable_diffs if previous_event is not None else ())
    }
    for diff in event.variable_diffs:
        detail = (
            f"Before: {_value_text(diff.before)}\n"
            f"After:  {_value_text(diff.after)}"
        )
        if diff.list_indices:
            detail += "\nChanged list indices: " + ", ".join(
                str(index) for index in diff.list_indices
            )
        if diff.dictionary_keys:
            detail += "\nChanged dictionary keys: " + _value_text(
                diff.dictionary_keys
            )
        fingerprint = (
            diff.name,
            diff.change,
            _value_text(diff.before),
            _value_text(diff.after),
        )
        new_class = " cj-new" if fingerprint not in previous_diffs else ""
        cards.append(
            f'<article class="cj-diff{new_class}">'
            f"<strong>{html.escape(diff.name)} · {html.escape(diff.change)}</strong>"
            f"<pre>{html.escape(detail)}</pre>"
            "</article>"
        )
    return "\n".join(cards)


def _loop_stack_html(event: TraceEvent) -> str:
    if not event.loop_iterations:
        return ""

    parts = []
    for iteration in event.loop_iterations:
        total = (
            str(iteration.total_iterations)
            if iteration.total_iterations is not None
            else "?"
        )
        parts.append(
            f"L{iteration.line_number}: "
            f"{iteration.iteration_index + 1}/{total}"
        )

    full_path = " → ".join(parts)
    compact_path = (
        full_path
        if len(parts) <= 3
        else " → ".join((parts[0], "…", parts[-1]))
    )

    return (
        '<span class="cj-badge cj-loop-stack" '
        f'title="{html.escape(full_path, quote=True)}">'
        f"Iteration {html.escape(compact_path)}</span>"
    )


def _view_html(
    result: TraceResult,
    event: TraceEvent,
    index: int,
    scope: str,
    show_error: bool,
    events: tuple["TraceEvent", ...],
    previous_event: Optional["TraceEvent"] = None,
    error_index: Optional[int] = None,
) -> str:
    source_lines = result.source.splitlines()
    line_markup_parts = []
    for number, line in enumerate(source_lines, start=1):
        target_index = next(
            (
                candidate
                for candidate, candidate_event in enumerate(events)
                if candidate_event.line_number == number
            ),
            None,
        )
        if target_index is None:
            target_index = min(
                range(len(events)),
                key=lambda candidate: abs(events[candidate].line_number - number),
                default=None,
            )
        target_attribute = (
            f' for="{scope}-step-{target_index}"'
            if target_index is not None
            else ""
        )
        line_markup_parts.append(
            f'<label class="cj-source-line{" cj-current" if number == event.line_number else ""}"'
            f'{target_attribute} role="link" aria-label="Jump to line {number}"'
            f' title="Jump to nearest step for line {number}">'
            f'<span class="cj-line-number">{number:>3}</span> '
            f"{html.escape(line)}</label>"
        )
    line_markup = "\n".join(line_markup_parts)
    if not line_markup:
        line_markup = '<span class="cj-empty">No source lines.</span>'

    variables_html, objects_html = _variable_objects(
        event,
        f"{scope}-{index}",
        previous_event,
    )
    iteration_text = (
        event.loop_iterations[-1].label
        if event.loop_iterations
        else "No loop iteration selected"
    )
    boundary = next(
        (item for item in event.loop_boundaries if item.kind == "start"),
        None,
    )
    if boundary is not None:
        total = boundary.total_iterations if boundary.total_iterations is not None else "?"
        iteration_text = f"Iteration {boundary.iteration_index + 1} of {total} · iteration begins"

    condition_html = ""

    if event.condition_preview is not None:
        condition = event.condition_preview
        previous_condition = (
            previous_event.condition_preview
            if previous_event is not None
            else None
        )
        condition_fingerprint = (
            condition.expression,
            condition.preview,
            condition.result,
        )
        previous_fingerprint = (
            (
                previous_condition.expression,
                previous_condition.preview,
                previous_condition.result,
            )
            if previous_condition is not None
            else None
        )
        new_class = " cj-new" if condition_fingerprint != previous_fingerprint else ""

        expression = html.escape(condition.expression)
        preview = html.escape(condition.preview)

        if condition.result is True:
            result_text = "✓ TRUE"
            result_class = "cj-condition-true"
        elif condition.result is False:
            result_text = "✕ FALSE"
            result_class = "cj-condition-false"
        else:
            result_text = "? UNKNOWN"
            result_class = "cj-condition-unknown"

        steps_html = ""
        if condition.evaluations:
            steps_html = (
                '<div class="cj-condition-steps" aria-label="Short-circuit evaluation steps">'
                '<div class="cj-condition-heading">SHORT-CIRCUIT STEPS</div>'
                + "".join(
                    '<div class="cj-condition-part cj-condition-part-'
                    + html.escape(step.status)
                    + '"><code>'
                    + html.escape(step.expression)
                    + '</code><strong>'
                    + html.escape(step.value)
                    + '</strong><span>Step '
                    + str(step_number)
                    + ': '
                    + html.escape(step.note)
                    + '</span></div>'
                    for step_number, step in enumerate(condition.evaluations, start=1)
                )
                + "</div>"
            )

        condition_html = (
            f'<div class="cj-condition-card{new_class}">'
            '<div class="cj-condition-heading">CONDITION</div>'
            f'<div class="cj-condition-expression">{expression}</div>'
            '<div class="cj-condition-heading">EVALUATION</div>'
            f'<div class="cj-condition-preview">{preview}</div>'
            + steps_html
            + f'<div class="cj-condition-result {result_class}">'
            + result_text
            + "</div></div>"
        )

    previous_warnings = (
        {
            (warning.message, warning.line_number)
            for warning in result.warnings
            if warning.line_number == previous_event.line_number
        }
        if previous_event is not None
        else set()
    )
    warning_html = "".join(
        f'<li class="cj-warning{(" cj-new" if (warning.message, warning.line_number) not in previous_warnings else "")}">'
        + html.escape(warning.message)
        + (
            f' <span class="cj-muted">(line {warning.line_number})</span>'
            if warning.line_number is not None
            else ""
        )
        + "</li>"
        for warning in result.warnings
    )

    error_html = ""
    if result.error is not None and show_error:
        error_html = (
            f'<li class="cj-error{(" cj-new" if error_index == index else "")}">'
            + html.escape(result.error.type_name)
            + ": "
            + html.escape(result.error.message)
            + "</li>"
        )

    if result.truncated:
        warning_html += (
            '<li class="cj-warning">Trace stopped after '
            + str(result.max_steps)
            + " steps. The displayed execution is partial.</li>"
        )

    return f"""<section class="cj-view" data-index="{index}">
  <div class="cj-state">{_loop_stack_html(event)}{condition_html}</div>
  <div class="cj-layout">
    <section class="cj-panel"><h3>Source</h3><pre class="cj-source"><code>{line_markup}</code></pre></section>
    <section class="cj-panel">
      <h3>Variables</h3><div class="cj-variables">{variables_html}</div>
      <h3>Objects</h3><div class="cj-objects">{objects_html}</div>
      <h3>What changed?</h3><div class="cj-diffs">{_diffs_html(event, previous_event)}</div>
      <h3>Learning notes</h3><ul>{warning_html}{error_html}</ul>
    </section>
  </div>
</section>"""



def render_html(result: TraceResult) -> str:
    """Render a self-contained, notebook-safe interactive HTML view.

    The timeline uses radio inputs and labels so it remains interactive in
    notebook renderers that intentionally disable JavaScript in HTML output.
    """
    scope = f"cj-{uuid.uuid4().hex}"
    events = tuple(
        event
        for event in result.events
        if event.line_number is not None and event.line_number > 0
    )
    initial_index = 0 if events else -1

    error_index = None
    if result.error is not None:
        exception_indices = [
            index
            for index, event in enumerate(events)
            if event.event == "exception"
        ]
        matching_errors = [
            index
            for index in exception_indices
            if events[index].exception_type == result.error.type_name
            and events[index].line_number == result.error.line_number
        ]
        matching_type = [
            index
            for index in exception_indices
            if events[index].exception_type == result.error.type_name
        ]
        if matching_errors:
            error_index = matching_errors[-1]
        elif matching_type:
            error_index = matching_type[-1]
        elif exception_indices:
            error_index = exception_indices[-1]

    controls = []
    timeline = []
    views = []
    selectors = []
    output_states = []
    count = len(events)
    for index, event in enumerate(events):
        selector_id = f"{scope}-step-{index}"
        selectors.append(
            f'<input class="cj-selector" type="radio" name="{scope}-steps" '
            f'id="{selector_id}"{ " checked" if index == initial_index else ""}>'
        )

        control_labels = []
        targets = {
            "prev": index - 1 if index > 0 else None,
            "next": index + 1 if index + 1 < count else None,
        }
        next_change = next(
            (
                candidate
                for candidate in range(index + 1, count)
                if events[candidate].variable_diffs
            ),
            None,
        )
        previous_iteration = next(
            (
                candidate
                for candidate in range(index - 1, -1, -1)
                if any(
                    boundary.kind == "start"
                    for boundary in events[candidate].loop_boundaries
                )
            ),
            None,
        )
        next_iteration = next(
            (
                candidate
                for candidate in range(index + 1, count)
                if any(
                    boundary.kind == "start"
                    for boundary in events[candidate].loop_boundaries
                )
            ),
            None,
        )
        targets.update(
            {
                "change": next_change,
                "prev-iteration": previous_iteration,
                "next-iteration": next_iteration,
            }
        )

        def navigation_button(symbol: str, accessible_label: str, target: Optional[int]) -> str:
            if target is None:
                return (
                    '<span class="cj-button cj-disabled" aria-disabled="true" '
                    f'aria-label="{accessible_label}" title="{accessible_label}">{symbol}</span>'
                )
            return (
                '<label class="cj-button" role="button" '
                f'aria-label="{accessible_label}" title="{accessible_label}" '
                f'for="{scope}-step-{target}">{symbol}</label>'
            )

        control_labels.append(
            navigation_button("⏮", "First step", 0 if index > 0 else None)
        )
        for action, label in (
            ("prev", "Prev"),
            ("next", "Next"),
            ("prev-iteration", "Prev Iteration"),
            ("next-iteration", "Next Iteration"),
            ("change", "Jump to Next Change"),
        ):
            target = targets[action]
            if action == "prev":
                label = '<span aria-hidden="true">◄</span>'
                aria_label = 'aria-label="Previous step" title="Previous step"'
            elif action == "next":
                label = '<span aria-hidden="true">►</span>'
                aria_label = 'aria-label="Next step" title="Next step"'
            else:
                aria_label = f'aria-label="{label}" title="{label}"'
            if target is None:
                control_labels.append(
                    f'<span class="cj-button cj-disabled" aria-disabled="true" {aria_label}>{label}</span>'
                )
            else:
                control_labels.append(
                    f'<label class="cj-button" role="button" {aria_label} for="{scope}-step-{target}">'
                    f"{label}</label>"
                )
        control_labels.append(
            navigation_button("⏭", "Last step", count - 1 if index < count - 1 else None)
        )
        controls.append(
            f'<div class="cj-controls" data-step-controls="{index}">'
            + "".join(control_labels)
            + f'<span class="cj-counter">'
            + ("END" if index == count - 1 else str(index + 1))
            + f' / {count}</span></div>'
        )
        tick_label = "END" if index == count - 1 else str(index + 1)
        timeline.append(
            f'<label class="cj-tick" role="button" for="{selector_id}" '
            f'data-step-index="{index}" '
            f'aria-label="Go to {tick_label}" title="{tick_label}">'
            f"{tick_label}</label>"
        )
        output_states.append(
            f'<div class="cj-output-state{(" cj-new" if event.output and (index == 0 or event.output != events[index - 1].output) else "")}" '
            f'data-output-index="{index}">'
            + (
                f"<pre>{html.escape(event.output)}</pre>"
                if event.output
                else '<p class="cj-output-empty">No output at this step.</p>'
            )
            + "</div>"
        )
        views.append(
            _view_html(
                result,
                event,
                index,
                scope,
                error_index is not None and index >= error_index,
                events,
                events[index - 1] if index > 0 else None,
                error_index,
            )
        )

    rules = []
    for index in range(count):
        selector_id = f"{scope}-step-{index}"
        rules.append(
            f"#{selector_id}:checked ~ .cj-controls[data-step-controls='{index}']"
            "{display:flex}"
        )
        rules.append(
            f"#{selector_id}:checked ~ .cj-output-card "
            f".cj-output-state[data-output-index='{index}']{{display:block}}"
        )
        rules.append(
            f"#{selector_id}:checked ~ .cj-views "
            f".cj-view[data-index='{index}']{{display:block}}"
        )
        rules.append(
            f"#{selector_id}:checked ~ .cj-timeline label[for='{selector_id}']"
            "{background:#315fa8 !important;color:#fff !important;"
            "border-color:#315fa8 !important}"
        )

    if not events:
        selector_html = ""
        empty_state_html = '<p class="cj-empty">No execution steps captured.</p>'
        controls_html = ""
        timeline_html = ""
        views_html = ""
        output_states_html = '<p class="cj-output-empty">No output captured.</p>'
    else:
        selector_html = "".join(selectors)
        empty_state_html = ""
        controls_html = "".join(controls)
        output_states_html = "".join(output_states)
        timeline_html = (
            '<div class="cj-timeline" role="group" aria-label="Execution timeline">'
            + "".join(timeline)
            + "</div>"
        )
        views_html = '<div class="cj-views">' + "".join(views) + "</div>"
    output_card_html = (
        '<section class="cj-output-card" aria-label="Output">'
        "<h3>Output</h3>"
        + output_states_html
        + (
            '<template class="cj-output-cumulative-final">'
            + html.escape(events[-1].output)
            + "</template>"
            if events
            else ""
        )
        + "</section>"
    )

    return f"""<div class="codejourney {scope}">
<style>
.codejourney {{ font: 13px/1.35 system-ui, sans-serif; color: #172033; background: #eef5ff; color-scheme: light; border: 1px solid #d5dbe6; border-radius: 8px; padding: 6px; }}
.codejourney * {{ box-sizing: border-box; }}
.cj-selector {{ position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }}
.cj-output-card {{ margin: 0 0 4px; padding: 5px 7px; border: 1px solid #cbd5e1; border-radius: 7px; background: #fff; color: #172033; color-scheme: light; }}
.cj-output-card h3 {{ margin: 0 0 3px; color: #172033; font-size: 13px; }}
.cj-output-state {{ display: none; }}
.cj-output-card pre {{ max-height: 120px; overflow: auto; margin: 0; padding: 3px 5px; border: 1px solid #dbe2ea; border-radius: 5px; background: #f8fafc !important; color: #172033 !important; white-space: pre-wrap; overflow-wrap: anywhere; }}
.cj-output-empty {{ margin: 0; color: #475569; }}
.cj-controls {{ display: none; flex-wrap: wrap; align-items: center; gap: 3px; margin-bottom: 3px; }}
.cj-button {{ border: 1px solid #aeb8c8; background: #f7f9fc; border-radius: 6px; padding: 2px 7px; cursor: pointer; color: inherit; font-size: 12px; user-select: none; -webkit-user-select: none; }}
.cj-button:hover {{ background: #e9eff8; }}
.cj-disabled, .cj-button[aria-disabled="true"], .cj-button:disabled {{ opacity: .18; background: transparent; border-color: transparent; cursor: default; pointer-events: none; user-select: none; -webkit-user-select: none; }}
.cj-counter {{ margin-left: auto; color: #657187; font-size: 12px; }}
.cj-timeline {{ display: flex; gap: 2px; overflow-x: auto; padding: 2px 0 4px; }}
.cj-tick {{ flex: 0 0 auto; min-width: 24px; padding: 1px 5px; border-radius: 999px; background: #e8edf5; color: #34445c; text-align: center; cursor: pointer; font-size: 12px; }}
.cj-view {{ display: none; }}
.cj-layout {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(260px, .8fr); gap: 5px; }}
.cj-panel {{ min-width: 0; border: 1px solid #e1e5ec; border-radius: 7px; padding: 5px; }}
.cj-panel h3 {{ font-size: 13px; margin: 0 0 3px; }}
.cj-panel h3:not(:first-child) {{ margin-top: 5px; }}
.codejourney .cj-source {{ max-height: 360px; overflow: auto; margin: 0; background: #ffffff !important; color: #172033 !important; color-scheme: light; border-radius: 5px; padding: 3px; white-space: pre; }}
.codejourney .cj-source code, .codejourney .cj-source-line {{ color: #172033 !important; }}
.codejourney .cj-source-line {{ display: block; min-height: 1.45em; padding: 1px 3px; background-color: #ffffff; cursor: pointer; border-radius: 3px; user-select: none; -webkit-user-select: none; }}
.codejourney .cj-source-line:hover {{ background: #edf4ff; outline: 1px solid #b7c6df; }}
.codejourney .cj-source-line:focus-visible {{ outline: 2px solid #315fa8; }}
.codejourney .cj-source-line.cj-current {{ background: #fff0b3 !important; color: #172033 !important; outline: 1px solid #c49a18; }}
.codejourney .cj-line-number {{ user-select: none; color: #5e6b7b !important; }}
.cj-state {{ margin: 0 0 3px; }}
.cj-badge {{ display: inline-block; background: #e8efff; border-radius: 999px; padding: 2px 7px; font-size: 12px; }}
.cj-loop-stack {{ max-width: 100%; flex-wrap: wrap; overflow-wrap: anywhere; }}

.cj-condition-card {{
    margin: 3px 0;
    padding: 5px;
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    gap: 3px;
}}

.cj-condition-heading {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    color: #64748b;
}}

.cj-condition-expression {{
    font-family: monospace;
    font-size: 14px;
    font-weight: 600;
    color: #1e293b;
    margin-bottom: 4px;
}}

.cj-condition-preview {{
    font-family: monospace;
    font-size: 12px;
    color: #475569;
    background: #e2e8f0;
    padding: 5px 8px;
    border-radius: 6px;
}}

.cj-condition-result {{
    align-self: flex-start;
    padding: 4px 8px;
    border-radius: 6px;
    font-weight: 700;
    font-size: 12px;
}}

.cj-condition-true {{
    background: #dcfce7;
    color: #166534;
}}

.cj-condition-false {{
    background: #fee2e2;
    color: #991b1b;
}}

.cj-condition-unknown {{
    background: #e2e8f0;
    color: #475569;
}}

.codejourney .cj-condition-steps {{ display: flex; flex-direction: column; gap: 3px; color-scheme: light; }}
.codejourney .cj-condition-part {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 5px; padding: 5px 7px; border-radius: 5px; border: 1px solid #cbd5e1 !important; background: #ffffff !important; color: #1e293b !important; color-scheme: light; box-shadow: none !important; }}
.codejourney .cj-condition-part code, .codejourney .cj-condition-part strong, .codejourney .cj-condition-part span {{ background: transparent !important; border: 0 !important; box-shadow: none !important; color: #1e293b !important; text-shadow: none !important; }}
.codejourney .cj-condition-part code {{ overflow-wrap: anywhere; }}
.codejourney .cj-condition-part strong {{ font-family: monospace; }}
.codejourney .cj-condition-part span {{ grid-column: 1 / -1; color: #475569 !important; font-size: 12px; }}
.codejourney .cj-condition-part-evaluated {{ background: #eff6ff !important; border-color: #60a5fa !important; border-left: 4px solid #2563eb !important; }}
.codejourney .cj-condition-part-skipped {{ background: #f1f5f9 !important; border-left: 4px solid #64748b !important; color: #334155 !important; }}
.codejourney .cj-condition-part-unknown {{ background: #fffbeb !important; border-left: 4px solid #d97706 !important; border-color: #fbbf24 !important; }}

.cj-variables {{ display: flex; flex-direction: column; gap: 2px; }}
.cj-variable {{ display: flex; align-items: center; gap: 5px; flex-wrap: wrap; padding: 4px 7px; background: #edf4ff; border: 1px solid #cfddf4; border-radius: 5px; }}
.cj-variable-name {{ font-weight: 650; }}
.cj-pointer {{ color: #315fa8; font-weight: 700; text-decoration: none; }}
.cj-pointer:hover {{ text-decoration: underline; }}
.cj-alias {{ color: #657187; font-size: 12px; }}
.cj-scalar {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
.cj-objects {{ display: flex; flex-wrap: wrap; align-items: flex-start; gap: 8px; }}
.cj-object {{ width: max-content; max-width: 100%; box-sizing: border-box; min-width: 0; border: 1px solid #bdc9da; border-radius: 6px; padding: 3px 4px; background: #fff; margin: 0; scroll-margin: 5px; }}
.cj-table-scroll {{ max-width: 100%; overflow-x: auto; }}
.cj-table-inner {{ min-width: max-content; max-width: 100%; }}
.cj-object-heading {{ display: flex; align-items: center; gap: 5px; margin: 0 0 3px; }}
.cj-object h4 {{ font-size: 12px; color: #53647c; margin: 0; }}
.cj-object-type {{ display: inline-block; border: 1px solid #cbd5e1; border-radius: 999px; padding: 1px 7px; background: #f1f5f9; color: #334155; font-size: 11px; }}
.cj-object pre {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }}
.cj-object-table {{ width: max-content; max-width: 100%; border-collapse: collapse; table-layout: auto; background: #fff; color: #172033; }}
.cj-object-table th, .cj-object-table td {{ border: 1px solid #cbd5e1; padding: 2px 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
.cj-object-table th {{ white-space: nowrap; }}
.cj-object-table thead th {{ background: #f1f5f9; color: #334155; font-weight: 650; padding-left: 6px; padding-right: 6px; }}
.cj-object-table tbody th {{ width: 1%; white-space: nowrap; background: #f8fafc; color: #334155; font-weight: 550; }}
.cj-object-table td {{ color: #172033; }}
.cj-diff {{ border-left: 3px solid #6688c7; background: #f7f9fd; padding: 4px 6px; margin: 4px 0; border-radius: 4px; overflow-wrap: anywhere; }}
.cj-diff pre {{ margin: 4px 0 0; white-space: pre-wrap; }}
.cj-warning {{ color: #684800; background: #fff6d8; border-radius: 5px; padding: 6px 8px; margin: 5px 0; }}
.cj-error {{ color: #8d2020; background: #fde8e8; border-radius: 5px; padding: 6px 8px; margin: 5px 0; }}
.cj-muted, .cj-empty {{ color: #657187; font-size: 12px; }}
.codejourney .cj-panel {{ background: #fff !important; color: #172033 !important; color-scheme: light; border-color: #d5dbe6 !important; }}
.codejourney .cj-panel h3 {{ color: #172033 !important; }}
.codejourney .cj-condition-card {{ background: #f8fafc !important; color: #172033 !important; color-scheme: light; border-color: #cbd5e1 !important; }}
.codejourney .cj-condition-heading {{ color: #475569 !important; }}
.codejourney .cj-condition-expression {{ color: #1e293b !important; }}
.codejourney .cj-condition-preview {{ background: #e2e8f0 !important; color: #334155 !important; }}
.codejourney .cj-condition-true {{ background: #dcfce7 !important; color: #166534 !important; }}
.codejourney .cj-condition-false {{ background: #fee2e2 !important; color: #991b1b !important; }}
.codejourney .cj-condition-unknown {{ background: #e2e8f0 !important; color: #475569 !important; }}
.codejourney .cj-output-card {{ background: #fff !important; color: #172033 !important; border-color: #cbd5e1 !important; color-scheme: light; }}
.codejourney .cj-output-card h3 {{ color: #172033 !important; }}
.codejourney .cj-output-card pre {{ background: #f8fafc !important; color: #172033 !important; border: 1px solid #dbe2ea !important; color-scheme: light; }}
.codejourney .cj-output-empty {{ color: #475569 !important; }}
.codejourney .cj-badge {{ background: #e8efff !important; color: #172033 !important; }}
.codejourney .cj-tick {{ background: #e8edf5 !important; color: #172033 !important; }}
.codejourney .cj-variable {{ background: #edf4ff !important; color: #172033 !important; border: 1px solid #cfddf4 !important; color-scheme: light; }}
.codejourney .cj-variable-name, .codejourney .cj-scalar {{ color: #172033 !important; }}
.codejourney .cj-scalar {{ background: #f8fafc !important; border: 1px solid #dbe2ea !important; border-radius: 4px; padding: 2px 5px; color-scheme: light; }}
.codejourney .cj-pointer {{ color: #315fa8 !important; }}
.codejourney .cj-alias {{ color: #53647c !important; }}
.codejourney .cj-object {{ background: #fff !important; color: #172033 !important; border: 1px solid #bdc9da !important; color-scheme: light; }}
.codejourney .cj-object h4 {{ color: #334155 !important; }}
.codejourney .cj-object pre {{ background: #fff !important; color: #172033 !important; color-scheme: light; }}
.codejourney .cj-object-type {{ background: #f1f5f9 !important; border: 1px solid #cbd5e1 !important; color: #334155 !important; color-scheme: light; }}
.codejourney .cj-object-table {{ background: #fff !important; color: #172033 !important; color-scheme: light; }}
.codejourney .cj-object-table th, .codejourney .cj-object-table td {{ border: 1px solid #cbd5e1 !important; color: #172033 !important; }}
.codejourney .cj-object-table .cj-value-column {{ background: #fff !important; color: #172033 !important; border: 1px solid #cbd5e1 !important; color-scheme: light; }}
.codejourney .cj-object-table .cj-value-column, .codejourney .cj-object-table .cj-value-column * {{ background: #fff !important; color: #172033 !important; border-color: #cbd5e1 !important; text-shadow: none !important; color-scheme: light; }}
.codejourney {{ background: #eef5ff !important; color-scheme: light; }}
.codejourney .cj-object-table thead th {{ background: #f1f5f9 !important; color: #334155 !important; }}
.codejourney .cj-object-table tbody th {{ background: #f8fafc !important; color: #334155 !important; }}
.codejourney .cj-diff {{ background: #f7f9fd !important; color: #172033 !important; border: 1px solid #d5dbe6 !important; border-left: 3px solid #6688c7 !important; color-scheme: light; }}
.codejourney .cj-diff pre {{ background: #f7f9fd !important; color: #172033 !important; color-scheme: light; }}
.codejourney .cj-warning {{ background: #fff6d8 !important; color: #684800 !important; }}
.codejourney .cj-error {{ background: #fde8e8 !important; color: #8d2020 !important; }}
.codejourney .cj-empty, .codejourney .cj-muted {{ color: #53647c !important; }}
@media (max-width: 700px) {{ .cj-layout {{ grid-template-columns: 1fr; }} }}
@media (prefers-reduced-motion: no-preference) {{
  @keyframes cj-pop-fade {{
    from {{ opacity: 0; transform: translateY(3px); }}
    to {{ opacity: 1; transform: translateY(0); }}
  }}
  .cj-output-state.cj-new pre,
  .cj-object.cj-new,
  .cj-diff.cj-new,
  .cj-condition-card.cj-new,
  .cj-warning.cj-new,
  .cj-error.cj-new {{
    animation: cj-pop-fade 160ms ease-out both;
  }}
}}
{''.join(rules)}
</style>
{selector_html}
{empty_state_html}
{controls_html}
{timeline_html}
{output_card_html}
{views_html}
</div>"""
