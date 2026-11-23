"""CodeJourney's structured Python execution tracing API."""

from __future__ import annotations

import io
import linecache
import sys
import types
import uuid
from contextlib import redirect_stdout
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .analysis import (
    ConditionInfo,
    EducationalWarning,
    LoopInfo,
    ProgramAnalysis,
    analyze_source,
)

__all__ = [
    "AliasInfo",
    "CollectionSnapshot",
    "ConditionEvaluation",
    "ConditionInfo",
    "ConditionPreview",
    "EducationalWarning",
    "ExecutionError",
    "LoopBoundary",
    "LoopInfo",
    "LoopIteration",
    "ObjectSnapshot",
    "ProgramAnalysis",
    "TraceEvent",
    "TraceResult",
    "VariableDiff",
    "VisualizationResult",
    "run_viz",
]


@dataclass(frozen=True)
class ObjectSnapshot:
    """Safe type-only description of a value not inspected by CodeJourney."""

    type_name: str
    display_name: Optional[str] = None


@dataclass(frozen=True)
class CollectionSnapshot:
    """Immutable snapshot of a dictionary, set, or frozenset."""

    type_name: str
    items: Tuple[Any, ...]


@dataclass(frozen=True)
class ExecutionError:
    """A syntax or runtime error raised while analyzing the source."""

    type_name: str
    message: str
    line_number: Optional[int] = None


@dataclass(frozen=True)
class VariableDiff:
    """A variable change and the affected collection entries, when known."""

    name: str
    change: str
    before: Any = None
    after: Any = None
    list_indices: Tuple[int, ...] = ()
    dictionary_keys: Tuple[Any, ...] = ()


@dataclass(frozen=True)
class AliasInfo:
    name: str
    linked_to: str

    @property
    def label(self) -> str:
        return f"Linked to {self.linked_to}"


@dataclass(frozen=True)
class LoopIteration:
    loop_id: str
    kind: str
    iteration_index: int
    total_iterations: Optional[int]
    line_number: int
    group_id: str = ""
    depth: int = 0

    @property
    def label(self) -> str:
        total = self.total_iterations if self.total_iterations is not None else "?"
        return f"Iteration {self.iteration_index + 1} of {total}"


@dataclass(frozen=True)
class LoopBoundary:
    kind: str
    loop_id: str
    iteration_index: int
    total_iterations: Optional[int]
    group_id: str = ""


@dataclass(frozen=True)
class ConditionEvaluation:
    expression: str
    value: str
    status: str
    note: str


@dataclass(frozen=True)
class ConditionPreview:
    condition_id: str
    expression: str
    preview: str
    result: Optional[bool]
    evaluations: Tuple[ConditionEvaluation, ...] = ()


@dataclass(frozen=True)
class TraceEvent:
    """One observed event from the submitted program.

    For line events, ``after`` is the snapshot at line entry and ``before`` is
    the previous snapshot from the same frame.
    """

    event: str
    line_number: Optional[int]
    source_line: Optional[str]
    before: Dict[str, Any]
    after: Dict[str, Any]
    changed_variables: Tuple[str, ...]
    exception_type: Optional[str] = None
    variable_diffs: Tuple[VariableDiff, ...] = ()
    aliases: Tuple[AliasInfo, ...] = ()
    loop_iterations: Tuple[LoopIteration, ...] = ()
    loop_boundaries: Tuple[LoopBoundary, ...] = ()
    condition_preview: Optional[ConditionPreview] = None
    warning_codes: Tuple[str, ...] = ()
    frame_id: int = 0
    output: str = ""

    @property
    def variables(self) -> Dict[str, Any]:
        """Return the snapshot associated with this event."""
        return self.after


@dataclass(frozen=True)
class TraceResult:
    """Structured trace data returned by :func:`run_viz`."""

    source: str
    events: Tuple[TraceEvent, ...]
    max_steps: int
    truncated: bool = False
    error: Optional[ExecutionError] = None
    loops: Tuple[LoopInfo, ...] = ()
    conditions: Tuple[ConditionInfo, ...] = ()
    warnings: Tuple[EducationalWarning, ...] = ()

    def _repr_html_(self) -> str:
        from .visualization import render_html

        return render_html(self)

VisualizationResult = TraceResult


class _StepLimitReached(BaseException):
    pass


def _type_name(value: Any) -> str:
    return type.__getattribute__(type(value), "__name__")


def _snapshot_value(
    value: Any,
    active_ids: Optional[set[int]] = None,
    depth: int = 0,
) -> Any:
    if type(value) in (type(None), bool, int, float, str, bytes):
        return value

    if active_ids is None:
        active_ids = set()
    value_id = id(value)
    value_type = type(value)

    if value_id in active_ids:
        return ObjectSnapshot("recursive " + _type_name(value))
    if depth >= 8:
        return ObjectSnapshot("nested " + _type_name(value))

    if value_type in (list, tuple, dict, set, frozenset):
        active_ids.add(value_id)
        try:
            if value_type is list:
                return [
                    _snapshot_value(item, active_ids, depth + 1)
                    for item in value
                ]
            if value_type is tuple:
                return tuple(
                    _snapshot_value(item, active_ids, depth + 1)
                    for item in value
                )
            if value_type is dict:
                return CollectionSnapshot(
                    "dict",
                    tuple(
                        (
                            _snapshot_value(key, active_ids, depth + 1),
                            _snapshot_value(item, active_ids, depth + 1),
                        )
                        for key, item in value.items()
                    ),
                )
            return CollectionSnapshot(
                _type_name(value),
                tuple(_snapshot_value(item, active_ids, depth + 1) for item in value),
            )
        finally:
            active_ids.remove(value_id)

    display_name = None
    if value_type is types.BuiltinFunctionType:
        display_name = value_type.__getattribute__(value, "__name__")
    return ObjectSnapshot(_type_name(value), display_name)


def _snapshot_locals(local_values: Dict[str, Any]) -> Dict[str, Any]:
    return {
        name: _snapshot_value(value)
        for name, value in local_values.items()
        if isinstance(name, str) and not name.startswith("__")
    }


def _changed_variables(
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Tuple[str, ...]:
    names = before.keys() | after.keys()
    return tuple(
        sorted(
            name
            for name in names
            if name not in before or name not in after or before[name] != after[name]
        )
    )


def _same_snapshot(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) in (type(None), bool, int, float, str, bytes):
        return left == right
    if isinstance(left, ObjectSnapshot):
        return left.type_name == right.type_name
    if isinstance(left, CollectionSnapshot):
        return left.type_name == right.type_name and _same_snapshot(left.items, right.items)
    if type(left) in (list, tuple):
        return len(left) == len(right) and all(
            _same_snapshot(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return False


def _mapping_items(snapshot: Any) -> Optional[Tuple[Tuple[Any, Any], ...]]:
    if isinstance(snapshot, CollectionSnapshot) and snapshot.type_name == "dict":
        return snapshot.items
    return None


def _collection_changes(before: Any, after: Any) -> Tuple[Tuple[int, ...], Tuple[Any, ...]]:
    changed_indices = ()
    if type(before) is list and type(after) is list:
        changed_indices = tuple(
            index
            for index in range(max(len(before), len(after)))
            if index >= len(before)
            or index >= len(after)
            or not _same_snapshot(before[index], after[index])
        )

    before_items = _mapping_items(before)
    after_items = _mapping_items(after)
    changed_keys = ()
    if before_items is not None and after_items is not None:
        unmatched = list(before_items)
        found_keys = []
        changed = []
        for key, value in after_items:
            match = next(
                (
                    index
                    for index, (old_key, _) in enumerate(unmatched)
                    if _same_snapshot(old_key, key)
                ),
                None,
            )
            if match is None:
                changed.append(key)
            else:
                old_key, old_value = unmatched.pop(match)
                found_keys.append(old_key)
                if not _same_snapshot(old_value, value):
                    changed.append(key)
        changed.extend(key for key, _ in unmatched)
        changed_keys = tuple(changed)
    return changed_indices, changed_keys


def _variable_diffs(
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Tuple[VariableDiff, ...]:
    diffs = []
    for name in _changed_variables(before, after):
        if name not in before:
            change = "added"
        elif name not in after:
            change = "removed"
        else:
            change = "modified"
        old_value = before.get(name)
        new_value = after.get(name)
        indices, keys = _collection_changes(old_value, new_value)
        diffs.append(
            VariableDiff(
                name=name,
                change=change,
                before=old_value,
                after=new_value,
                list_indices=indices,
                dictionary_keys=keys,
            )
        )
    return tuple(diffs)


def _aliases(local_values: Dict[str, Any]) -> Tuple[AliasInfo, ...]:
    references: Dict[int, list[str]] = {}
    seen_containers = set()
    scalar_types = (type(None), bool, int, float, complex, str, bytes)

    def visit(path: str, value: Any) -> None:
        value_type = type(value)
        mutable = value_type in (list, dict, set, bytearray) or value_type not in (
            *scalar_types,
            tuple,
            frozenset,
        )
        if mutable:
            references.setdefault(id(value), []).append(path)
        if value_type in (list, tuple):
            if id(value) in seen_containers:
                return
            seen_containers.add(id(value))
            for index, item in enumerate(value):
                visit(f"{path}[{index}]", item)
        elif value_type is dict:
            if id(value) in seen_containers:
                return
            seen_containers.add(id(value))
            for index, item in enumerate(value.values()):
                visit(f"{path}.value[{index}]", item)

    for name, value in local_values.items():
        if isinstance(name, str) and not name.startswith("__"):
            visit(name, value)
    return tuple(
        AliasInfo(name=path, linked_to=paths[0])
        for paths in references.values()
        if len(paths) > 1
        for path in paths[1:]
    )


def _condition_preview(
    condition: ConditionInfo,
    values: Dict[str, Any],
    result: Optional[bool],
) -> ConditionPreview:
    import ast

    class _SubstituteNames(ast.NodeTransformer):
        complete = True

        def visit_Name(self, node: ast.Name) -> Any:
            if not isinstance(node.ctx, ast.Load):
                return node
            value = values.get(node.id, _MISSING)
            if type(value) not in (type(None), bool, int, float, str):
                self.complete = False
                return node
            return ast.copy_location(ast.Constant(value=value), node)

    try:
        expression_tree = ast.parse(condition.expression, mode="eval")
        substitute = _SubstituteNames()
        substituted_tree = substitute.visit(expression_tree)
        ast.fix_missing_locations(substituted_tree)
        substituted = ast.unparse(substituted_tree.body)
    except (SyntaxError, ValueError):
        substitute = None
        substituted = condition.expression

    if substitute is not None and substitute.complete and result is not None:
        if condition.expression == substituted:
            preview = f"{substituted} -> {result}"
        else:
            preview = f"{condition.expression} -> {substituted} -> {result}"
    else:
        preview = f"{condition.expression} (value unavailable)"
    return ConditionPreview(condition.condition_id, condition.expression, preview, result)


def _sanitize_loop_iterations(
    events: list[TraceEvent],
    loops: Tuple[LoopInfo, ...],
) -> list[TraceEvent]:
    import dataclasses

    loops_by_id = {loop.loop_id: loop for loop in loops}
    sanitized = []
    for index, event in enumerate(events):
        iterations = []
        for iteration in event.loop_iterations:
            loop = loops_by_id[iteration.loop_id]
            if event.event == "line" and event.line_number == loop.line_number:
                next_line = None
                for later in events[index + 1 :]:
                    if later.frame_id == event.frame_id and later.event in {"line", "return"}:
                        next_line = later.line_number
                        break
                if (
                    next_line is None
                    or not loop.contains(next_line)
                    or (
                        loop.total_iterations is not None
                        and iteration.iteration_index >= loop.total_iterations
                    )
                ):
                    continue
            elif event.event == "return" and not any(
                boundary.loop_id == iteration.loop_id and boundary.kind == "end"
                for boundary in event.loop_boundaries
            ):
                continue
            iterations.append(iteration)
        sanitized.append(
            dataclasses.replace(event, loop_iterations=tuple(iterations))
        )
    return sanitized


_MISSING = object()


def _safe_condition_value(node: Any, values: Dict[str, Any]) -> Any:
    import ast

    safe_types = (type(None), bool, int, float, str)
    if isinstance(node, ast.Constant):
        return node.value if type(node.value) in safe_types else _MISSING
    if isinstance(node, ast.Name):
        value = values.get(node.id, _MISSING)
        return value if type(value) in safe_types else _MISSING
    if isinstance(node, ast.UnaryOp):
        operand = _safe_condition_value(node.operand, values)
        if operand is _MISSING:
            return _MISSING
        if isinstance(node.op, ast.Not):
            return not operand
        if type(operand) in (int, float):
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
    if isinstance(node, ast.Compare):
        left = _safe_condition_value(node.left, values)
        if left is _MISSING:
            return _MISSING
        for operation, comparator in zip(node.ops, node.comparators):
            right = _safe_condition_value(comparator, values)
            if right is _MISSING:
                return _MISSING
            try:
                if isinstance(operation, ast.Eq):
                    matched = left == right
                elif isinstance(operation, ast.NotEq):
                    matched = left != right
                elif isinstance(operation, ast.Lt):
                    matched = left < right
                elif isinstance(operation, ast.LtE):
                    matched = left <= right
                elif isinstance(operation, ast.Gt):
                    matched = left > right
                elif isinstance(operation, ast.GtE):
                    matched = left >= right
                elif isinstance(operation, ast.Is):
                    matched = left is right
                elif isinstance(operation, ast.IsNot):
                    matched = left is not right
                elif (
                    isinstance(operation, (ast.In, ast.NotIn))
                    and type(right) is str
                    and type(left) is str
                ):
                    matched = left in right
                    if isinstance(operation, ast.NotIn):
                        matched = not matched
                else:
                    return _MISSING
            except TypeError:
                return _MISSING
            if not matched:
                return False
            left = right
        return True
    return _MISSING


def _condition_evaluations(expression: Any, values: Dict[str, Any]) -> Tuple[ConditionEvaluation, ...]:
    import ast

    evaluations = []

    def value_text(value: Any) -> str:
        if value is _MISSING:
            return "Value unavailable"
        return repr(value)

    def visit(node: Any) -> Any:
        if not isinstance(node, ast.BoolOp):
            value = _safe_condition_value(node, values)
            evaluations.append(
                ConditionEvaluation(
                    ast.unparse(node),
                    value_text(value),
                    "evaluated" if value is not _MISSING else "unknown",
                    (
                        "This operand was evaluated in Python's left-to-right order."
                        if value is not _MISSING
                        else "Not previewed safely; CodeJourney does not re-run this expression."
                    ),
                )
            )
            return value

        is_and = isinstance(node.op, ast.And)
        operator_name = "and" if is_and else "or"
        previous_value = _MISSING
        for index, operand in enumerate(node.values):
            if previous_value is not _MISSING and (
                (is_and and not previous_value) or (not is_and and previous_value)
            ):
                truth = bool(previous_value)
                evaluations.append(
                    ConditionEvaluation(
                        ast.unparse(operand),
                        "Not evaluated",
                        "skipped",
                        f"Short-circuited: {truth} {operator_name} ... skips this operand.",
                    )
                )
                continue
            if previous_value is _MISSING and index > 0:
                evaluations.append(
                    ConditionEvaluation(
                        ast.unparse(operand),
                        "Value unavailable",
                        "unknown",
                        "Whether Python evaluates this operand cannot be previewed safely.",
                    )
                )
                continue
            if isinstance(operand, ast.BoolOp):
                current_value = visit(operand)
            else:
                current_value = _safe_condition_value(operand, values)
                evaluations.append(
                    ConditionEvaluation(
                        ast.unparse(operand),
                        value_text(current_value),
                        "evaluated" if current_value is not _MISSING else "unknown",
                        (
                            "First operand is evaluated."
                            if index == 0
                            else f"Previous operand was {bool(previous_value)}; {operator_name} evaluates the next operand."
                        )
                        if current_value is not _MISSING
                        else "Not previewed safely; CodeJourney does not re-run this expression.",
                    )
                )
            previous_value = current_value
        return previous_value

    if isinstance(expression, ast.BoolOp):
        visit(expression)
    return tuple(evaluations)


def _attach_condition_previews(
    events: list[TraceEvent],
    conditions: Tuple[ConditionInfo, ...],
) -> list[TraceEvent]:
    import ast
    import dataclasses

    by_line = {condition.line_number: condition for condition in conditions}
    enriched = list(events)
    for index, event in enumerate(events):
        condition = by_line.get(event.line_number)
        if event.event != "line" or condition is None:
            continue

        next_line = None
        condition_raised = False
        for later in events[index + 1 :]:
            if later.frame_id != event.frame_id:
                continue
            if later.event == "exception":
                condition_raised = True
                break
            if later.event in {"line", "return"}:
                next_line = later.line_number
                break

        result = (
            condition.body_contains(next_line)
            if next_line is not None and not condition_raised
            else None
        )
        values = {
            name: value
            for name, value in event.after.items()
            if type(value) in (type(None), bool, int, float, str)
        }
        try:
            condition_tree = ast.parse(condition.expression, mode="eval").body
            evaluations = _condition_evaluations(condition_tree, values)
        except (SyntaxError, ValueError):
            evaluations = ()
        enriched[index] = dataclasses.replace(
            event,
            condition_preview=dataclasses.replace(
                _condition_preview(condition, values, result),
                evaluations=evaluations,
            ),
        )
    return enriched


def _error_line(error: BaseException, filename: str) -> Optional[int]:
    line_number = error.lineno if type(error) is SyntaxError else None
    if line_number is not None:
        return line_number
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename == filename:
            line_number = traceback.tb_lineno
        traceback = traceback.tb_next
    return line_number


def _safe_error_message(error: BaseException) -> str:
    arguments = BaseException.__getattribute__(error, "args")
    if type(arguments) is tuple and arguments:
        message = arguments[0]
        if type(message) is str:
            return message
        if type(message) in (type(None), bool, int, float):
            return str(message)
    return "Execution raised an exception."


def run_viz(code_str: str, *, max_steps: int = 5000) -> TraceResult:
    """Execute source and return a bounded, structured Python trace.

    This function is not a security sandbox. Submitted code runs with this
    process's normal permissions; ``max_steps`` limits traced Python lines only.
    """
    if not isinstance(code_str, str):
        raise TypeError("code_str must be a string")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int):
        raise TypeError("max_steps must be an integer")
    if max_steps <= 0:
        raise ValueError("max_steps must be a positive integer")

    filename = f"<codejourney-{uuid.uuid4().hex}>"
    source_lines = code_str.splitlines()
    events = []
    warnings = []
    line_steps = 0
    previous_snapshots: Dict[int, Dict[str, Any]] = {}
    loop_states: Dict[int, Dict[str, Dict[str, Any]]] = {}
    loop_invocations: Dict[Tuple[int, str], int] = {}
    namespace: Dict[str, Any] = {"__name__": "__main__"}
    error: Optional[ExecutionError] = None
    truncated = False
    output = io.StringIO()

    try:
        analysis = analyze_source(code_str)
        compiled = compile(code_str, filename, "exec")
    except (SyntaxError, ValueError, TypeError) as exc:
        error = ExecutionError(
            type_name=_type_name(exc),
            message=_safe_error_message(exc),
            line_number=_error_line(exc, filename),
        )
        return TraceResult(code_str, (), max_steps, error=error)

    loops_by_id = {loop.loop_id: loop for loop in analysis.loops}
    loop_depths = {
        loop.loop_id: sum(
            1
            for outer in analysis.loops
            if outer.loop_id != loop.loop_id
            and outer.line_number != loop.line_number
            and outer.contains(loop.line_number)
        )
        for loop in analysis.loops
    }

    linecache.cache[filename] = (
        len(code_str),
        None,
        source_lines,
        filename,
    )

    def trace(frame: types.FrameType, event: str, arg: Any) -> Any:
        nonlocal line_steps
        if frame.f_code.co_filename != filename:
            return None

        if event == "line":
            if line_steps >= max_steps:
                raise _StepLimitReached()
            line_steps += 1

        if event not in {"line", "return", "exception"}:
            return trace
        if (
            event == "exception"
            and isinstance(arg, tuple)
            and arg
            and arg[0] is _StepLimitReached
        ):
            return trace

        frame_id = id(frame)
        line_number = frame.f_lineno
        boundaries = []
        state = loop_states.setdefault(frame_id, {})

        if event == "line":
            for loop_id in tuple(state):
                loop = next(item for item in analysis.loops if item.loop_id == loop_id)
                if line_number != loop.line_number and not loop.contains(line_number):
                    current = state.pop(loop_id)
                    if current["started"]:
                        boundaries.append(
                            LoopBoundary(
                                "end",
                                loop_id,
                                current["index"],
                                loop.total_iterations,
                                current["group_id"],
                            )
                        )

            for loop in analysis.loops:
                if line_number != loop.line_number:
                    continue
                current = state.get(loop.loop_id)
                if current is None:
                    invocation_key = (frame_id, loop.loop_id)
                    invocation = loop_invocations.get(invocation_key, 0) + 1
                    loop_invocations[invocation_key] = invocation
                    current = {
                        "index": 0,
                        "started": False,
                        "group_id": f"{loop.loop_id}-run-{invocation}",
                    }
                else:
                    if current["started"]:
                        boundaries.append(
                            LoopBoundary(
                                "end",
                                loop.loop_id,
                                current["index"],
                                loop.total_iterations,
                                current["group_id"],
                            )
                        )
                    current = {
                        "index": current["index"] + 1,
                        "started": False,
                        "group_id": current["group_id"],
                    }
                state[loop.loop_id] = current

            for loop in analysis.loops:
                current = state.get(loop.loop_id)
                if current is not None and loop.contains(line_number) and not current["started"]:
                    current["started"] = True
                    boundaries.append(
                        LoopBoundary(
                            "start",
                            loop.loop_id,
                            current["index"],
                            loop.total_iterations,
                            current["group_id"],
                        )
                    )

        active_iterations = []
        for loop in analysis.loops:
            current = state.get(loop.loop_id)
            if current is not None and (
                line_number == loop.line_number or loop.contains(line_number)
            ):
                active_iterations.append(
                    LoopIteration(
                        loop_id=loop.loop_id,
                        kind=loop.kind,
                        iteration_index=current["index"],
                        total_iterations=loop.total_iterations,
                        line_number=loop.line_number,
                        group_id=current["group_id"],
                        depth=loop_depths[loop.loop_id],
                    )
                )
        active_iterations.sort(
            key=lambda item: (
                item.depth,
                item.line_number,
                item.loop_id,
            )
        )

        if event == "return":
            for loop_id, current in tuple(state.items()):
                loop = next(item for item in analysis.loops if item.loop_id == loop_id)
                if current["started"]:
                    boundaries.append(
                        LoopBoundary(
                            "end",
                            loop_id,
                            current["index"],
                            loop.total_iterations,
                            current["group_id"],
                        )
                    )
            loop_states.pop(frame_id, None)

        after = _snapshot_locals(frame.f_locals)
        before = previous_snapshots.get(frame_id, {})
        changed = _changed_variables(before, after)
        diffs = _variable_diffs(before, after)
        source_line = (
            source_lines[line_number - 1]
            if 1 <= line_number <= len(source_lines)
            else None
        )
        exception_type = None
        if event == "exception" and isinstance(arg, tuple) and arg:
            exception_type = type.__getattribute__(arg[0], "__name__")

        warning_codes = []
        for warning in analysis.warnings:
            if warning.line_number == line_number:
                if warning.code in {"break-first-iteration", "return-first-iteration"}:
                    if any(item.iteration_index == 0 for item in active_iterations):
                        warning_codes.append(warning.code)
                        warnings.append(warning)
                else:
                    warning_codes.append(warning.code)
                    warnings.append(warning)

        events.append(
            TraceEvent(
                event=event,
                line_number=line_number,
                source_line=source_line,
                before=before,
                after=after,
                changed_variables=changed,
                exception_type=exception_type,
                variable_diffs=diffs,
                aliases=_aliases(frame.f_locals),
                loop_iterations=tuple(active_iterations),
                loop_boundaries=tuple(boundaries),
                warning_codes=tuple(dict.fromkeys(warning_codes)),
                frame_id=frame_id,
                output=output.getvalue(),
            )
        )
        if event == "return":
            previous_snapshots.pop(frame_id, None)
        else:
            previous_snapshots[frame_id] = after
        return trace

    previous_trace = sys.gettrace()
    try:
        sys.settrace(trace)
        with redirect_stdout(output):
            exec(compiled, namespace, namespace)
    except _StepLimitReached:
        truncated = True
    except Exception as exc:
        error = ExecutionError(
            type_name=_type_name(exc),
            message=_safe_error_message(exc),
            line_number=_error_line(exc, filename),
        )
    finally:
        sys.settrace(previous_trace)
        linecache.cache.pop(filename, None)

    events = _attach_condition_previews(events, analysis.conditions)
    events = _sanitize_loop_iterations(events, analysis.loops)
    warning_by_key = {
        (warning.code, warning.line_number): warning
        for warning in analysis.warnings
        if warning.code not in {"break-first-iteration", "return-first-iteration"}
    }
    warning_by_key.update(
        {
            (warning.code, warning.line_number): warning
            for warning in warnings
        }
    )
    unique_warnings = tuple(warning_by_key.values())

    return TraceResult(
        source=code_str,
        events=tuple(events),
        max_steps=max_steps,
        truncated=truncated,
        error=error,
        loops=analysis.loops,
        conditions=analysis.conditions,
        warnings=unique_warnings,
    )


def _register_pyviz_magic() -> None:
    try:
        from IPython import get_ipython
    except ImportError:
        return

    shell = get_ipython()
    if shell is not None:
        shell.register_magic_function(pyviz, magic_kind="cell", magic_name="pyviz")


def pyviz(line: str, cell: str) -> TraceResult:
    """IPython cell magic implementation for ``%%pyviz``."""
    del line
    return run_viz(cell)


_register_pyviz_magic()
