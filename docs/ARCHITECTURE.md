# Architecture

This explains how the package works and why it is built this way. Read the [README](../README.md) first and run the demo, because the demo prints these same stages.

## The core idea

Models are good at explaining. Code is good at being right about numbers. So each does only its own job:

- **Code computes the facts** and reaches its own conclusion.
- **The model explains** from those facts, without seeing the conclusion.
- **Code compares the two.** They must agree before anyone sees the model's answer.
- **If they disagree,** the user gets a plain report built only from the facts.

## Data flow

```text
 usage.json + cost.json         real API responses, or synthetic files of the same shape
          |
          v
 analytics.py                   cents to dollars, drop incomplete days, join on day and group
          |
          v
 analyze.py  ---> verdict       status, cause, driver: kept away from the model
          |              \
        facts             \
          |                \
          v                 \
 prompt.py                   |
          |                  |
          v                  |
        MODEL                |
          |                  |
       raw text              |
          |                  v
          +----------> verify.py   6 checks: all must pass
                             |
                 pass        |        fail, or no answer
             +---------------+---------------+
             v                               v
     model's answer                  fallback.py report
```

`pipeline.decide()` is the only place that chooses between the two outputs.

## Modules

| File | Job | Depends on |
|---|---|---|
| `analytics.py` | Turns API JSON into `DayRow` records | Nothing |
| `analyze.py` | Detects spikes, produces `facts` and `Verdict` | `analytics` |
| `prompt.py` | Turns facts into the prompt | `analyze` (cause names only) |
| `verify.py` | Runs the reliability checks | `analyze` |
| `fallback.py` | Builds the model-free report | `analyze` |
| `pipeline.py` | Chooses which output to show | `verify`, `fallback` |
| `client.py` | Makes all HTTP calls | Nothing |
| `cli.py` | Command line, which `explain_spend.py` wraps | All of the above |

Only `client.py` touches the network, which is why everything else runs without a key.

## How a spike is detected

Each step lives in `analyze.py`, and the thresholds are constants at the top of that file.

1. **Split the days.** The last 3 complete days are the window. Earlier days are the baseline.
2. **Compare.** It is a spike if the window's daily mean is at least 1.5x the baseline's daily median, and the rise is at least $25 a day. The median means one odd day cannot shift the baseline, which the `one_day_outlier` scenario tests.
3. **Find the driver.** Rank workspaces by how much each added. If the top one has under 50% of the increase, the cause is `broad_growth`.
4. **Name the cause.** Inside the driving workspace, test in this order:

| Test | Cause |
|---|---|
| Requests rose at least 1.5x | `volume_increase` |
| One model's share of cost moved at least 30 points | `model_mix_shift` |
| Cache hit rate fell at least 20 points | `cache_regression` |
| None of the above | `unclassified` |

The order matters. A cache failure and a model change both raise cost per request, but more requests explains a rise more directly, so volume is tested first.

## API details that shaped the code

These come from the [Usage and Cost Analytics API guide](https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api).

| API behaviour | What goes wrong if ignored | Where it is handled |
|---|---|---|
| `amount` is a string in cents | Every figure is 100x too high | `analytics.cents_to_usd` |
| Data past `data_refreshed_at` is incomplete | A partial day looks like a fall in spend | `analytics._is_complete` |
| Days with no usage have no row | Medians are computed over the wrong days | `analyze._daily_series` |
| `model` can be null | Code execution rows crash or vanish | `analytics._key` |
| At most 31 days per request | The request is rejected | `client.fetch_analytics` |
| A cursor can expire with HTTP 410 | Pagination fails partway | `client._fetch_all_pages` |

Spend figures can also change until about 7 days after the end of a calendar month, so recent numbers are provisional.

## The reliability check

`verify.py` runs these checks in order. Any failure rejects the whole answer.

| Check | Question | Example it catches |
|---|---|---|
| `json` | Is it parseable JSON? | A Markdown-fenced answer |
| `schema` | Are all fields present and valid? | A missing `headline` |
| `conclusion` | Do status and cause match the rules? | Calling a cache failure a traffic rise |
| `driver` | Is it the right workspace or key? | Blaming the wrong team |
| `ids` | Does every ID in the prose exist? | An invented API key |
| `numbers` | Does every number trace back to the facts? | `$9999.99` |

**Numbers are matched by kind.** Dollars match only dollar facts, and percentages only percentage facts, so `$65.7` is rejected even when `65.7%` is a real fact. Rounding is allowed: `$432` matches `431.51`.

**Numbers are matched by size, not sign.** `fell by $0.53` is a fair way to state `-0.53`, so `0.53` and `-0.53` both match. The `conclusion` check covers whether spend is a spike.

**The check is strict on purpose.** It may reject an answer that is right but phrased in a way it cannot trace, such as a correctly calculated difference that is not in the facts. The cost of that mistake is a plainer report, while the cost of the opposite mistake is a wrong number in front of a finance team. The live eval measures how often it happens.

### What the check cannot catch

- A real number attached to the wrong claim, such as a true figure from the wrong workspace.
- Numbers written as words, such as "doubled" or "nine hundred".
- A number stated in the wrong direction, such as "rose $0.53" when it fell.
- Weak reasoning or a poor recommendation. The EvalForge rubric judges these offline, but nothing judges them at run time.
- A rule that is wrong. If `analyze.py` misclassifies something, a correct model answer is rejected and the fallback repeats the mistake. The rules are the source of truth, so they need their own tests.

## Decisions and alternatives

| Decision | Why | Rejected alternative |
|---|---|---|
| Withhold the verdict from the model | Otherwise the eval only measures copying | Giving the model the conclusion to phrase |
| Fall back without retrying | Simple and predictable, with no extra cost or latency | Retrying with the error message, which can be added later if the eval shows a high fallback rate |
| Reject Markdown fences | The product and the eval use one contract | Stripping fences in the product only, which would make the two disagree |
| One user message, no tools | EvalForge sends only `{model, messages}`, so this keeps the eval and the product identical | Structured outputs, which EvalForge cannot test |
| Standard library only | Runs cold on Python 3.9 with nothing to install | The Anthropic SDK |
| Keep prompt thresholds in the facts | An answer can cite the rule it applied | Rejecting any mention of a threshold |

**Why use a model at all?** On these six scenarios, the rules alone reach the right conclusion. The model adds a readable explanation grounded in the specifics, and a next step fitted to the situation. If you only need a label, use the rules and skip the model.

## Skill mode

Inside Claude Code the assistant is the model, so no API key is needed:

1. The skill runs `prompt`, which prints facts but no verdict.
2. Claude writes its answer to a file.
3. The skill runs `verify`, and exit code 0 or 1 says which report to show.

The skill tells Claude not to run `analyze` or read `expected.json`. That is an instruction, not a technical barrier, so skill mode is less strict than the eval.

Three details keep this path honest:

- `verify --summary` names failed checks but hides their details, so Claude never sees a rejected value it could repeat.
- `project` does projection arithmetic in code, so Claude has no reason to calculate a figure itself.
- `allowed-tools` in `SKILL.md` lets the skill's commands run without a prompt after the user consents to the skill.

**The limit of skill mode:** the verifier checks the answer file, not Claude's final message. In testing, Claude sometimes reformatted a verified report as bullets and reworded parts of it, using the same figures. Only the instructions in `SKILL.md` stop it from changing more. `run` and `verify` print the checked text directly, so use those when the exact wording must be the verified wording.

## Adding a cause

1. Add the constant to `analyze.py`, and a test in `_classify`.
2. Add text to `CAUSE_TEXT` and `CAUSE_ACTION` in `fallback.py`.
3. Describe it in `INSTRUCTIONS` in `prompt.py`.
4. Add a scenario in `scripts/make_fixtures.py` and a rubric in `evals/build_cases.py`.
5. Run `python3 scripts/make_fixtures.py && python3 evals/build_cases.py`, then the tests.
