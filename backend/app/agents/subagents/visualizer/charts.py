"""Which chart, drawn from which columns.

Split deliberately in two. The rules below decide what is *possible* — that is a question about
shapes, and shapes are something code judges more reliably than a language model: a pie chart of
eighty categories is wrong no matter how confidently it was chosen. The model then decides which
of the possible charts best answers the question that was asked, which is a question about
meaning, and one code has no purchase on at all.

Because it picks from a numbered list rather than naming columns freely, a hallucinated column
name isn't something that has to be caught after the fact — it can't be expressed.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.subagents.sql_agent.db import QueryResult
from app.agents.subagents.visualizer.profile import ColumnProfile, ResultProfile, profile_result
from app.core.llm import get_llm
from app.core.logging import get_logger, log_duration

logger = get_logger(__name__)

CHOICE_PROMPT = """You are a data analyst choosing how to display a query result. You'll be given \
the question that was asked, the shape of the result, a few sample rows, and a numbered list of \
charts that the data can actually support.

Pick the one that answers the question best, and return its number as a JSON string, e.g. \
`"0"` or `"-1"`.

- Choose by what the question is asking, not by what looks impressive. "How did revenue change \
this year" wants the trend over time. "Which region sold most" wants the comparison across \
regions. "What share of orders came from each channel" wants the proportions.
- Prefer the simpler chart when two would work equally well.
- If the question asks for a particular kind of chart and no option is that kind, that chart is \
not possible for this data. Pick the closest one that is, and say so in `reason` — "a line needs \
a time axis and this result has none" — rather than switching silently and letting the user think \
they got what they asked for. Return -1 if nothing here is a reasonable substitute.
- Return -1 if none of them would tell the user anything the numbers don't already say — a result \
with two rows, or one where the interesting part is a single figure, is better read as a table. \
Declining is a valid answer and is better than a chart nobody needed.
- In `y`, list the measures worth plotting. Use all of the chosen chart's y columns unless one of \
them is on a wildly different scale or is plainly incidental to the question, in which case drop \
it. Never name a column that isn't listed for the chart you picked.
- Write `title` in the language of the question, the way it would be captioned in a report — \
"Revenue by month", not "sum_revenue vs month_bucket". No more than about eight words.
- Keep `reason` to one clause explaining why this chart answers the question."""

# nothing to compare with fewer than two rows — a single bar is a number with decoration
MIN_ROWS = 2
# past this many bars the labels collide and the chart stops being readable before it stops fitting
MAX_BAR_CATEGORIES = 50
# a pie is read by comparing slice areas, which stops working almost immediately
MAX_PIE_SLICES = 7
# distinct lines on one chart, or grouped bars in one cluster
MAX_SERIES = 8
# measures plotted together; beyond a handful the legend carries the chart
MAX_MEASURES = 4
# a scatter is a claim about a distribution, and a distribution needs points
MIN_SCATTER_ROWS = 10
# rows shown to the model alongside the profile, purely so values have some texture
SAMPLE_ROWS = 3

# columns that read as something a person would want on an axis, rather than the code beside it
_LABEL_NAME_RE = re.compile(r"(?:^|_)(name|label|title|description|desc)s?$", re.I)


class ChartType(str, Enum):
    BAR = "bar"
    LINE = "line"
    AREA = "area"
    PIE = "pie"
    SCATTER = "scatter"


class ChartSpec(BaseModel):
    """A chart, described rather than drawn.

    This crosses the API and the frontend renders it. Nothing here is code, which is the point:
    there is no sandbox to escape because there is nothing to execute, and the same spec re-renders
    interactively, in either theme, at whatever size the browser happens to be.
    """

    type: ChartType
    x: str
    y: list[str]
    # splits y into one line or bar group per distinct value of this column
    series: Optional[str] = None
    title: str
    # why this chart was chosen — shown on the card, and the first thing worth reading when a
    # choice looks wrong
    reason: str


@dataclass(frozen=True)
class Candidate:
    type: ChartType
    x: str
    y: list[str]
    series: Optional[str]
    rationale: str

    def describe(self, index: int) -> str:
        line = f"{index}. {self.type.value} — x: {self.x}, y: {', '.join(self.y)}"
        if self.series:
            line += f", one series per {self.series}"
        return f"{line} ({self.rationale})"


def _humanise(name: str) -> str:
    return name.replace("_", " ").strip()


def _default_title(candidate: Candidate) -> str:
    measures = " and ".join(_humanise(name) for name in candidate.y)
    title = f"{measures} by {_humanise(candidate.x)}"
    if candidate.series:
        title += f", split by {_humanise(candidate.series)}"
    return title[:1].upper() + title[1:]


def candidates(profile: ResultProfile) -> list[Candidate]:
    """Every chart this result can honestly support, best-guess first.

    Empty is a normal answer, not a failure: a single scalar, a list of names with nothing measured
    against them, and an empty result all belong in a table rather than a chart.
    """
    if profile.row_count < MIN_ROWS:
        return []

    numeric = profile.by_role("numeric")
    if not numeric:
        return []

    temporal = profile.by_role("temporal")
    measures = [column.name for column in numeric][:MAX_MEASURES]

    # only a column with few enough distinct values can carry an axis; the rest are free text as
    # far as a chart is concerned. Identifiers sort last rather than being dropped, so a result
    # that has nothing but keys can still be charted against one.
    # tie-broken toward a readable label last, after cardinality, so it only decides between
    # columns of identical width — which is exactly the code-and-name pair a join tends to produce
    axis_categories = sorted(
        (c for c in profile.by_role("categorical") if 2 <= c.distinct <= MAX_BAR_CATEGORIES),
        key=lambda c: (c.is_key, c.distinct, not _LABEL_NAME_RE.search(c.name)),
    )
    # never split by an identifier: a legend of raw ids is unreadable whatever the chart
    series_categories = [c for c in axis_categories if c.distinct <= MAX_SERIES and not c.is_key]

    def split_by(axis: Optional[ColumnProfile] = None) -> Optional[str]:
        # splitting only reads well against a single measure — two measures across four regions is
        # eight lines and no insight
        if len(measures) != 1:
            return None
        excluded = {axis.name, *axis.duplicates} if axis else set()
        return next((c.name for c in series_categories if c.name not in excluded), None)

    found: list[Candidate] = []

    if temporal:
        clock = temporal[0]
        axis, series = clock.name, split_by(clock)
        found.append(Candidate(ChartType.LINE, axis, measures, series, "a measure over time"))
        found.append(Candidate(ChartType.BAR, axis, measures, series, "the same, as discrete periods"))
        if len(measures) == 1 and not series:
            found.append(Candidate(ChartType.AREA, axis, measures, None, "a running total or volume over time"))

    if axis_categories:
        axis = axis_categories[0]
        series = split_by(axis)
        found.append(Candidate(ChartType.BAR, axis.name, measures, series, f"comparison across {axis.distinct} categories"))

        # a pie asserts the slices sum to something meaningful, which negatives break outright
        if len(measures) == 1 and axis.distinct <= MAX_PIE_SLICES and (numeric[0].minimum or 0) >= 0:
            found.append(Candidate(ChartType.PIE, axis.name, measures, None, "share of a total"))

    # only when there's no time or category axis to prefer — otherwise a scatter of two measures is
    # a worse answer to the same question
    if len(numeric) >= 2 and not temporal and not axis_categories and profile.row_count >= MIN_SCATTER_ROWS:
        found.append(
            Candidate(ChartType.SCATTER, numeric[0].name, [numeric[1].name], None, "correlation between two measures")
        )

    seen = set()
    unique = []
    for candidate in found:
        key = (candidate.type, candidate.x, tuple(candidate.y), candidate.series)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


class _Choice(BaseModel):
    """What the model is allowed to contribute: which candidate, which of its measures, and a
    title a person would actually want above the chart."""

    # Groq validates tool-call arguments before Pydantic can coerce them, and some models emit
    # small integers as JSON strings. Keep the schema stringly and parse below.
    choice: str = Field(description='the chart number to draw as a string, or "-1" to draw none')
    y: list[str] = Field(description="which of that chart's y columns to plot — all of them unless one is noise")
    title: str = Field(description="a short title in the language of the question, not the column names")
    reason: str = Field(description="one clause on why this chart answers the question")


def _spec(candidate: Candidate, title: str, reason: str, y: Optional[list[str]] = None) -> ChartSpec:
    return ChartSpec(
        type=candidate.type,
        x=candidate.x,
        y=y or candidate.y,
        series=candidate.series,
        title=title,
        reason=reason,
    )


def _choose(question: str, profile: ResultProfile, options: list[Candidate], rows: list[dict]) -> Optional[ChartSpec]:
    context = (
        f"Question: {question}\n\n"
        f"Result shape:\n{profile.describe()}\n\n"
        f"Sample rows: {rows[:SAMPLE_ROWS]}\n\n"
        f"Charts available:\n" + "\n".join(c.describe(i) for i, c in enumerate(options))
    )

    with log_duration("Chart selection"):
        choice = get_llm("visualizer").with_structured_output(_Choice).invoke(
            [SystemMessage(content=CHOICE_PROMPT), HumanMessage(content=context)]
        )

    try:
        selected = int(choice.choice)
    except ValueError:
        logger.info("visualizer declined to chart (choice=%s)", choice.choice)
        return None

    if not 0 <= selected < len(options):
        # -1 is the documented way to decline; anything else out of range is a misread prompt, and
        # either way there is no chart to draw
        logger.info("visualizer declined to chart (choice=%s)", choice.choice)
        return None

    candidate = options[selected]
    # the model may narrow the measures but not invent them
    narrowed = [name for name in choice.y if name in candidate.y]
    return _spec(candidate, choice.title, choice.reason, narrowed or None)


def select(
    question: str, result: QueryResult, always_ask: bool = False
) -> tuple[Optional[ChartSpec], ResultProfile]:
    """The chart for this result, if there is one worth drawing.

    Returns the profile either way — the frontend needs to know which columns are measures and
    which are labels in order to offer the user a different chart than the one chosen here.

    `always_ask` keeps the model in the loop even when the rules leave only one option. Worth a
    round trip when the user asked for a particular chart by name: they are owed either the chart
    they asked for or a decline, and handing them a different one without comment answers a
    question they didn't ask.
    """
    profile = profile_result(result)
    options = candidates(profile)

    if not options:
        logger.info("no chart candidates for a %d-row result", profile.row_count)
        return None, profile

    if len(options) == 1 and not always_ask:
        # nothing to deliberate over, so nothing worth a round trip to a model — the rules already
        # decided this is both possible and the only possibility
        only = options[0]
        return _spec(only, _default_title(only), only.rationale), profile

    return _choose(question, profile, options, result.rows), profile
