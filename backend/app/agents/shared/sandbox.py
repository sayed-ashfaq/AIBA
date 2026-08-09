"""Runs model-written Python in a resource-limited subprocess: a hard timeout, a memory cap, and an
import allowlist (pandas, numpy, plus a few safe stdlib modules — no filesystem or network access
beyond the input data it's given).

Used by python_agent (run_python, tabular results) and visualizer (run_chart, rendered charts) —
same isolation mechanics, different runner script and allowlist, so the subprocess/timeout/resource
plumbing is shared and only the two run_* entry points differ. This is process isolation plus a
static allowlist, not a hardened sandbox against adversarial code — proportionate to where the code
comes from: our own orchestration pipeline, not untrusted end-user input. The same trust level SQL
generation already runs at.
"""

import ast
import base64
import json
import os
import resource
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Optional

TIMEOUT_SECONDS = 10
MEMORY_LIMIT_BYTES = 512 * 1024 * 1024

# charts pull in matplotlib/seaborn on top of pandas/numpy, which costs noticeably more at import
# time than plain tabular code — a longer timeout absorbs that without changing what counts as a
# runaway script
CHART_TIMEOUT_SECONDS = 20
CHART_MEMORY_LIMIT_BYTES = 768 * 1024 * 1024

ALLOWED_IMPORTS = {"pandas", "numpy", "math", "statistics", "datetime", "json", "decimal"}
CHART_ALLOWED_IMPORTS = ALLOWED_IMPORTS | {"matplotlib", "seaborn"}

# OpenBLAS (loaded transitively by numpy/pandas) sizes its thread pool to the host's detected core
# count and reserves virtual address space for it at import time, before any real data is touched
# — under RLIMIT_AS below, that reservation alone can exceed the cap and fail with "OpenBLAS error:
# Memory allocation still failed after 10 retries" on nothing more than `import pandas`. Pinning
# every BLAS/OMP thread pool to 1 removes that host-dependent variable entirely: our datasets are
# capped at a few thousand rows, so single-threaded BLAS costs nothing.
_SANDBOX_ENV = {
    **os.environ,
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}

# matplotlib builds a font-list cache on first use and otherwise defaults to a dir under $HOME,
# which may not be writable (or may not exist) in this process's environment. Pointing it at one
# fixed, pre-existing directory — rather than the per-call tempdir every other sandbox file lives in
# — means that cache is built once and reused by every later chart, instead of paying the same
# rebuild cost inside CHART_TIMEOUT_SECONDS on every single call.
_MPL_CACHE_DIR = Path(tempfile.gettempdir()) / "aiba_mpl_cache"
_MPL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CHART_SANDBOX_ENV = {**_SANDBOX_ENV, "MPLCONFIGDIR": str(_MPL_CACHE_DIR)}

_MAX_OUTPUT_CHARS = 4000

_RESULT_START = "__AIBA_RESULT_START__"
_RESULT_END = "__AIBA_RESULT_END__"
# printed instead of a result block when the model's code ran but never plotted anything — an empty
# figure is never a legitimate answer, so this is treated as an error rather than a blank chart
_NO_FIGURE_MARKER = "__AIBA_NO_FIGURE__"

_RUNNER_TEMPLATE = """\
import json
import pandas as pd
import numpy as np

dfs = []
for _path in {data_paths!r}:
    with open(_path) as _f:
        _payload = json.load(_f)
    dfs.append(pd.DataFrame(_payload["rows"], columns=_payload["columns"]))
df = dfs[0] if dfs else None

result_df = None

{code}

if result_df is not None:
    if not isinstance(result_df, pd.DataFrame):
        result_df = pd.DataFrame(result_df)
    print({result_start!r})
    print(result_df.to_json(orient="split", date_format="iso"))
    print({result_end!r})
"""

# Agg is the non-interactive backend — there's no display to draw to inside the subprocess, and
# selecting it before pyplot is imported is what stops matplotlib probing for one and failing.
# The model draws with plt/sns calls against df/dfs same as run_python; whatever ends up on the
# current figure (plt.gcf()) is what gets captured — no explicit savefig/show from the model's code.
_CHART_RUNNER_TEMPLATE = """\
import base64
import io
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import numpy as np

dfs = []
for _path in {data_paths!r}:
    with open(_path) as _f:
        _payload = json.load(_f)
    dfs.append(pd.DataFrame(_payload["rows"], columns=_payload["columns"]))
df = dfs[0] if dfs else None

{code}

_fig = plt.gcf()
if not _fig.get_axes():
    print({no_figure_marker!r})
else:
    _buf = io.BytesIO()
    _fig.savefig(_buf, format="png", dpi=150, bbox_inches="tight")
    print({result_start!r})
    print(base64.b64encode(_buf.getvalue()).decode("ascii"))
    print({result_end!r})
"""


@dataclass
class SandboxResult:
    columns: Optional[list[str]] = None
    rows: Optional[list[dict]] = None
    error: Optional[str] = None


@dataclass
class SandboxChartResult:
    image_base64: Optional[str] = None
    error: Optional[str] = None


def _check_imports(code: str, allowed: set[str]) -> Optional[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"syntax error: {exc}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        disallowed = [name for name in names if name not in allowed]
        if disallowed:
            return f"import of {disallowed} is not allowed — only {sorted(allowed)}"
    return None


def _limit_resources(timeout: int, memory_bytes: int) -> None:
    # RLIMIT_CPU is a backstop behind subprocess.run's wall-clock timeout below; RLIMIT_AS caps
    # total address space, so a runaway allocation fails fast instead of pressuring the host
    resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))


def _run_subprocess(
    script_path: Path, timeout: int, memory_bytes: int, env: dict
) -> Optional[subprocess.CompletedProcess]:
    """Run one sandbox script, or None on timeout — shared by run_python and run_chart, which only
    differ in the script they hand it and the limits/env to run it under."""
    try:
        return subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=partial(_limit_resources, timeout, memory_bytes),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return None


def _write_data_files(tmp: str, datasets: list[dict]) -> list[str]:
    data_paths = []
    for i, dataset in enumerate(datasets):
        path = Path(tmp) / f"data_{i}.json"
        path.write_text(json.dumps(dataset))
        data_paths.append(str(path))
    return data_paths


def _extract_block(stdout: str) -> Optional[str]:
    """The raw text between the result markers, or None if the script never printed either — e.g.
    it crashed after producing unrelated stdout, rather than via a nonzero exit code."""
    if _RESULT_START not in stdout or _RESULT_END not in stdout:
        return None
    return stdout.split(_RESULT_START, 1)[1].split(_RESULT_END, 1)[0].strip()


def _parse_result(stdout: str) -> Optional[tuple[list[str], list[dict]]]:
    body = _extract_block(stdout)
    if body is None:
        return None
    parsed = json.loads(body)
    columns = parsed["columns"]
    rows = [dict(zip(columns, row)) for row in parsed["data"]]
    return columns, rows


def run_python(code: str, datasets: list[dict]) -> SandboxResult:
    """datasets: [{"columns": [...], "rows": [...]}, ...], loaded into `dfs` (and `df` = dfs[0])
    inside the sandbox in the order given. The code must assign its answer to `result_df` — a
    DataFrame, or anything pd.DataFrame() accepts — for anything to come back; nothing is captured
    from bare prints or a trailing expression."""
    error = _check_imports(code, ALLOWED_IMPORTS)
    if error:
        return SandboxResult(error=error)

    with tempfile.TemporaryDirectory() as tmp:
        data_paths = _write_data_files(tmp, datasets)

        script_path = Path(tmp) / "script.py"
        script_path.write_text(
            _RUNNER_TEMPLATE.format(
                data_paths=data_paths, code=code, result_start=_RESULT_START, result_end=_RESULT_END
            )
        )

        proc = _run_subprocess(script_path, TIMEOUT_SECONDS, MEMORY_LIMIT_BYTES, _SANDBOX_ENV)
        if proc is None:
            return SandboxResult(error=f"execution timed out after {TIMEOUT_SECONDS}s")

        if proc.returncode != 0:
            return SandboxResult(error=proc.stderr.strip()[-_MAX_OUTPUT_CHARS:] or "code exited with an error and no output")

        parsed = _parse_result(proc.stdout)
        if parsed is None:
            return SandboxResult(
                error="code ran but never assigned result_df, so there's nothing to return. "
                f"stdout was: {proc.stdout.strip()[-_MAX_OUTPUT_CHARS:] or '(empty)'}"
            )
        columns, rows = parsed
        return SandboxResult(columns=columns, rows=rows)


def run_chart(code: str, datasets: list[dict]) -> SandboxChartResult:
    """Same data-loading convention as run_python (df/dfs), but the code is expected to draw with
    matplotlib/seaborn rather than assign a result_df — whatever ends up on the current figure after
    the code runs is captured as a PNG and returned base64-encoded. An error if the code raises,
    times out, or never actually plots anything."""
    error = _check_imports(code, CHART_ALLOWED_IMPORTS)
    if error:
        return SandboxChartResult(error=error)

    with tempfile.TemporaryDirectory() as tmp:
        data_paths = _write_data_files(tmp, datasets)

        script_path = Path(tmp) / "script.py"
        script_path.write_text(
            _CHART_RUNNER_TEMPLATE.format(
                data_paths=data_paths,
                code=code,
                result_start=_RESULT_START,
                result_end=_RESULT_END,
                no_figure_marker=_NO_FIGURE_MARKER,
            )
        )

        proc = _run_subprocess(script_path, CHART_TIMEOUT_SECONDS, CHART_MEMORY_LIMIT_BYTES, _CHART_SANDBOX_ENV)
        if proc is None:
            return SandboxChartResult(error=f"execution timed out after {CHART_TIMEOUT_SECONDS}s")

        if proc.returncode != 0:
            return SandboxChartResult(error=proc.stderr.strip()[-_MAX_OUTPUT_CHARS:] or "code exited with an error and no output")

        if _NO_FIGURE_MARKER in proc.stdout:
            return SandboxChartResult(
                error="code ran but never drew anything — call a plotting function (e.g. "
                "sns.barplot, plt.plot, sns.heatmap) against df/dfs before returning"
            )

        image_base64 = _extract_block(proc.stdout)
        if image_base64 is None:
            return SandboxChartResult(
                error="code ran but produced no chart. "
                f"stdout was: {proc.stdout.strip()[-_MAX_OUTPUT_CHARS:] or '(empty)'}"
            )
        return SandboxChartResult(image_base64=image_base64)
