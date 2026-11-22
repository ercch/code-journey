# CodeJourney

CodeJourney is an educational Python execution visualizer for Jupyter
notebooks. It helps beginners inspect source-line execution, variable changes,
collections, aliases, loop iterations, condition previews, and recognizable
learning hazards.

## Motivation

Beginners often have to infer program behavior by repeatedly comparing source
code with the current values of variables. Traditional line-by-line traces can
also lose the structure of loops. CodeJourney presents captured execution steps
as a navigable notebook view, with variable diffs and loop-aware landmarks
intended to make common patterns easier to inspect.

CodeJourney is a learning aid, not a replacement for a debugger or a complete
model of Python execution.

## Verified features

The following features are present in the current implementation and reviewed
against the repository source:

- `run_viz(code_str, *, max_steps=500)` returns structured trace data.
- `%%pyviz` runs a Python cell and displays the resulting trace in IPython or
  Jupyter.
- Standard output is captured as execution proceeds and shown in an Output
  card that follows the selected trace step, rather than as separate notebook
  cell output.
- Captured trace events include source lines, variable snapshots, variable
  diffs, collection mutations, and runtime or syntax errors.
- Supported loop metadata includes `for`, `while`, nested loops, iteration
  boundaries, and known totals when they can be established from source.
- Safe condition previews show short-circuit evaluation order for supported and/or operands, including expressions Python skips.
- Supported conditional statments metadata will be visualized and have the evaluated expression, subsitutes values, and whether the condition evaluates to True or False
- Shared object identity is rendered as alias links rather than inferred from
  equal values.
- The HTML view is CSS-only and is intended to work in notebook renderers that
  restrict JavaScript.
- The implementation includes educational prompts for supported, recognizable
  cases such as list mutation during iteration, first-iteration `break` or
  `return`, and assignment to a Python built-in name.

These source-level and unit-test features have not been marked manually
verified unless they are personally confirmed by the repository maintainer.
Use [the manual test checklist](docs/manual-test-checklist.md) for notebook and
fresh-install verification.

## Requirements

- Python 3.9 or newer.
- Jupyter/IPython for notebook display and `%%pyviz`.
- The `run_viz` API can be used in Python environments with or without
  IPython.

## Installation

### From PyPI
CodeJourney is published on PyPI
```bash
python -m pip install codejourney
```
PyPI package: https://pypi.org/project/codejourney/
After installation, restart the Jupyter kernel before importing the package

### Development installation

From the repository root:

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest
```

Run the existing test suite with:

```bash
python -m pytest
```

The test command verifies repository automation, but it does not replace
personal review of the interactive notebook UI.

## Quick start: `run_viz`

Install the published package from PyPI with:

```shell
pip install codejourney
```

```python
from codejourney import run_viz

trace = run_viz(
    "total = 0\n"
    "for number in [1, 2, 3]:\n"
    "    total += number\n",
    max_steps=100,
)

trace.events
trace.error
trace.warnings
trace._repr_html_()
```

In a Jupyter notebook, displaying `trace` may render the interactive HTML:

```python
trace
```

`max_steps` must be a positive integer and defaults to `500`.

## Quick start: `%%pyviz`

Import `codejourney` in an active IPython/Jupyter session to register the
`%%pyviz` cell magic automatically:

```python
import codejourney
```

Then run a source cell:

```python
%%pyviz
total = 0
for number in [1, 2, 3]:
    total += number
```

The magic returns a `TraceResult`; outside a live IPython environment, use
`run_viz` instead.

## Visualization controls

The notebook view provides the following controls:

- **Timeline ticks** — select an execution step directly.
- **END** — select the final execution state; it follows the numbered steps.
- **Prev** — move to the previous captured execution step.
- **Next** — move to the next captured execution step.
- **Prev Iteration** — move to the previous loop-start boundary.
- **Next Iteration** — move to the next loop-start boundary.
- **Jump to Next Change** — move to the next later step that reports a variable
  diff.
- **Step counter** — show the selected step and total number of captured steps.
- **Output** — show accumulated standard output through the selected state.
- **Source panel** — highlight the source line associated with the selected
  execution state.
- **Variables** — show variable names and scalar or collection values captured
  at that state.
- **Objects** — show object or collection representations and links from
  object-valued variables.
- **What changed?** — show added, removed, or modified variables and, when
  available, affected list indices or dictionary keys.
- **Learning notes** — show supported educational warnings, execution errors
  from the step where they occur onward, and trace-limit truncation notices.
- **Iteration badge** — show loop iteration context when available.

Controls disabled for the current step or state are shown as unavailable.

## Supported scope

CodeJourney can inspect and visualize:

- Python source supplied as a string to `run_viz`, or a complete cell passed to
  `%%pyviz`.
- Supported built-in scalar values and nested built-in collections.
- Observed `line`, `return`, and `exception` trace events.
- `for` and `while` loop structure and source-expressed condition metadata.
- Syntax and runtime errors, with partial results where execution already
  produced trace events.

Support is deliberately bounded. CodeJourney does not trace every construct,
native operation, extension call, generator, or third-party library behavior
in full. It does not promise complete support for every Python program or every
possible exception.

## Security warning

**Executed code is not sandboxed.** `run_viz` and `%%pyviz` execute submitted
code with the permissions of the current Python process. Analyzed code can
perform file or network I/O, invoke native code, consume resources, or block.
Only run code you trust and review it before execution.

`max_steps` limits traced Python line events. It is not a timeout and is not a
security boundary. Execution can continue beyond the trace limit for behavior
that does not produce further counted events.

## Limitations

- This is an educational execution trace, not a full debugger.
- It does not provide breakpoints or control of a live process.
- Captured values and object displays are snapshots, not live variable references.
- Unsupported values receive safe type-oriented descriptions rather than
  arbitrary representations.
- Aliases are based on observed shared object identity.
- Arbitrary condition expressions are not re-evaluated for display;
  short-circuit sub-expressions are previewed only when they use supported
  constants, captured scalar values, and comparisons.
- Loop totals may be unknown for dynamic iterables or `while` loops.
- A reached step limit produces a partial visualization and warning.
- Syntax and runtime errors are represented alongside available partial data;
  they should not be interpreted as successful completion.
- Snapshot depth and trace behavior may omit or summarize deeply nested or
  uncommon values.
- Learning warnings are prompts, not proofs of an error or defect.

## Comparison with existing execution visualizers

CodeJourney overlaps with existing execution visualizers, including browser
and notebook tools such as Python Tutor, but has a narrower educational focus.
The current implementation emphasizes loop iteration landmarks, variable
diffs, collection mutation details, aliases, condition previews, and
recognizable beginner warnings.

Python Tutor and other established tools may provide broader compatibility,
longer-lived ecosystems, or additional debugger-style features. CodeJourney
does not claim to be better than those tools, the first execution visualizer,
or a general replacement for an IDE debugger.

Choose the tool appropriate to the program and learning task. CodeJourney is
most useful for short, reviewable Python examples in Jupyter.

## Team

- Eric Yiu
- Yuen Cheng
- Jason Lai

## License

Repository metadata currently declares **MIT**. A separate license file or
official release wording has not been provided here as a completed claim:
**[License text and link placeholder]**.

## Version

Current release: 0.2.1

## Project links

- GitHub: https://github.com/ercch/code-journey
- PyPI: https://pypi.org/project/codejourney/
- Documentation: this README and SPEC.md

The project specification is available in [SPEC.md](SPEC.md).
