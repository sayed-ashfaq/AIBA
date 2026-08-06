"""Runs model-written Python in a resource-limited subprocess: a hard timeout, a memory cap, and an
import allowlist (pandas, numpy, plus a few safe stdlib modules — no filesystem or network access
beyond the input data it's given).

Used by python_agent. This is process isolation plus a static allowlist, not a hardened sandbox
against adversarial code — proportionate to where the code comes from: our own orchestration
pipeline, not untrusted end-user input. The same trust level SQL generation already runs at.
"""

import ast
import json
import resource
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TIMEOUT_SECONDS = 10
MEMORY_LIMIT_BYTES = 512 * 1024 * 1024

ALLOWED_IMPORTS = {"pandas", "numpy", "math", "statistics", "datetime", "json", "decimal"}

_MAX_OUTPUT_CHARS = 4000

_RESULT_START = "__AIBA_RESULT_START__"
_RESULT_END = "__AIBA_RESULT_END__"

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


@dataclass
class SandboxResult:
    columns: Optional[list[str]] = None
    rows: Optional[list[dict]] = None
    error: Optional[str] = None


def _check_imports(code: str) -> Optional[str]:
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
        disallowed = [name for name in names if name not in ALLOWED_IMPORTS]
        if disallowed:
            return f"import of {disallowed} is not allowed — only {sorted(ALLOWED_IMPORTS)}"
    return None


def _limit_resources() -> None:
    # RLIMIT_CPU is a backstop behind subprocess.run's wall-clock timeout below; RLIMIT_AS caps
    # total address space, so a runaway allocation fails fast instead of pressuring the host
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))


def _parse_result(stdout: str) -> Optional[tuple[list[str], list[dict]]]:
    if _RESULT_START not in stdout or _RESULT_END not in stdout:
        return None
    body = stdout.split(_RESULT_START, 1)[1].split(_RESULT_END, 1)[0].strip()
    parsed = json.loads(body)
    columns = parsed["columns"]
    rows = [dict(zip(columns, row)) for row in parsed["data"]]
    return columns, rows


def run_python(code: str, datasets: list[dict]) -> SandboxResult:
    """datasets: [{"columns": [...], "rows": [...]}, ...], loaded into `dfs` (and `df` = dfs[0])
    inside the sandbox in the order given. The code must assign its answer to `result_df` — a
    DataFrame, or anything pd.DataFrame() accepts — for anything to come back; nothing is captured
    from bare prints or a trailing expression."""
    error = _check_imports(code)
    if error:
        return SandboxResult(error=error)

    with tempfile.TemporaryDirectory() as tmp:
        data_paths = []
        for i, dataset in enumerate(datasets):
            path = Path(tmp) / f"data_{i}.json"
            path.write_text(json.dumps(dataset))
            data_paths.append(str(path))

        script_path = Path(tmp) / "script.py"
        script_path.write_text(
            _RUNNER_TEMPLATE.format(
                data_paths=data_paths, code=code, result_start=_RESULT_START, result_end=_RESULT_END
            )
        )

        try:
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                preexec_fn=_limit_resources,
            )
        except subprocess.TimeoutExpired:
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
