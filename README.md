# spend-explainer

Tells you why your Claude bill went up, and refuses to show an AI-written explanation unless its numbers check out.

It's built on the [Usage and Cost Analytics API](https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api), which launched in beta on October 29, 2026. It ships as a Claude Code plugin, and all bundled data is synthetic.

## Try it in one minute

You need Python 3.9 or newer, and you don't need an API key. It has no dependencies.

```bash
pip install spend-explainer
spend-explainer demo
```

Or run it from a clone, with nothing installed:

```bash
git clone https://github.com/thejaredchapman/spend-explainer.git
cd spend-explainer
python3 explain_spend.py demo
```

The examples below use `python3 explain_spend.py`. If you installed with pip, use `spend-explainer` instead. The `evals/` scripts and tests are only in the clone.

The demo walks through one spike, a broken prompt cache, one stage at a time. It runs a correct answer and then an answer with an invented number. The second ends like this:

```text
  [FAIL] numbers    not found in the data: $9999.99

STEP 5  Decide what to show (spend_explainer/pipeline.py)
  shown from : deterministic fallback
  status     : spike (cache_regression)
  driver     : wrkspc_01SYNTHSUPPORT
  headline   : Daily spend went from $270.62 to $431.51 (1.59x).
```

The answers in the demo are hand-made stand-ins, not real model output, so no key is needed.

To see all 6 scenarios and 5 answer types, and to run the tests:

```bash
python3 explain_spend.py demo --all
python3 -m unittest discover -s tests
```

`demo --all` should end with `Unexpected outcomes: 0`, and the tests should end with `OK`.

## How it works

Models are good at explaining, and code is good at being right about numbers. So each does one job:

```text
 API data --> rules compute facts + a conclusion
                   |                    |
                 facts           (hidden from model)
                   v                    |
                 model                  |
                   v                    v
                 answer ---> reliability check
                                  |
                       pass ------+------ fail
                        v                  v
                 model's answer      plain rule-based report
```

1. **Parse.** Convert the API's amounts from cents, and drop days that are still incomplete.
2. **Analyze.** Compare the last 3 days with the earlier baseline, using rules and no model.
3. **Explain.** The model gets the facts, but not the conclusion.
4. **Verify.** Six checks compare the answer with the facts. These cover its format, conclusion, driver, IDs and every number.
5. **Decide.** One failed check means the user sees the plain report instead.

[docs/ARCHITECTURE.md](https://github.com/thejaredchapman/spend-explainer/blob/main/docs/ARCHITECTURE.md) explains every stage, the decisions behind them, and what the check can't catch. The code is meant to be read in that same order, starting at `spend_explainer/analytics.py`.

## Commands

| Command | What it does | Needs |
|---|---|---|
| `demo` | Walks through the pipeline. Add `--all` for every scenario | Nothing |
| `prompt --scenario NAME` | Prints the exact model prompt | Nothing |
| `analyze --scenario NAME` | Prints the facts and the rules' conclusion | Nothing |
| `verify --scenario NAME --answer FILE` | Checks an answer. Exit 0 if shown, 1 if it fell back. Add `--summary` to hide rejected values | Nothing |
| `project --scenario NAME --days 30` | Multiplies the daily increase over a period, with code rather than by hand | Nothing |
| `run --scenario NAME` | Asks a real model, then verifies | `ANTHROPIC_API_KEY` |
| `fetch --start T --end T --out DIR` | Downloads your real data | `ANTHROPIC_ADMIN_API_KEY` |

Scenarios are `quiet`, `one_day_outlier`, `model_mix_shift`, `cache_regression`, `volume_increase` and `broad_growth`. Commands that take `--scenario` also accept `--data DIR` for a folder with `usage.json` and `cost.json`.

## Use it with a real model

```bash
export ANTHROPIC_API_KEY="your key"
python3 explain_spend.py run --scenario cache_regression
```

If the call fails, or the answer fails a check, you still get the plain report. The default model is `claude-haiku-5-5`, and `--model` changes it.

## Use it on your real spend

This needs an [Admin API key](https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api). Enterprise organizations use a key with the `read:analytics` scope.

```bash
export ANTHROPIC_ADMIN_API_KEY="your admin key"
python3 explain_spend.py fetch --start 2026-10-01T00:00:00Z --end 2026-10-18T00:00:00Z --out my-data
python3 explain_spend.py run --data my-data
```

A request can cover at most 31 days, and you need at least 6 complete days. Data typically refreshes within 4 hours, but can take up to 24.

`run` sends your spend figures, workspace IDs, API key IDs and model names to the Claude API.

## Use it as a Claude Code skill

```bash
claude plugin marketplace add thejaredchapman/spend-explainer
claude plugin install spend-explainer@spend-tools
```

Start a new session and ask "why did my Claude spend go up?". Claude itself writes the answer, so no API key is needed for the synthetic scenarios. The same check still decides what you see.

On default permissions, Claude Code asks once whether to use the skill. After that, the skill's commands run without further prompts.

## What it runs and sends

| Action | What happens |
|---|---|
| `demo`, `analyze`, `prompt`, `verify`, `project` | Run locally, read local files, and send nothing |
| `run` | Sends the prompt to `api.anthropic.com` with `ANTHROPIC_API_KEY`. The prompt holds spend figures, workspace IDs, API key IDs and model names |
| `fetch` | Reads usage and cost data from `api.anthropic.com` with `ANTHROPIC_ADMIN_API_KEY`, and writes it to the folder you choose |
| The skill | Runs `python3` on the commands above, and writes your answer to a temporary file. Claude reads the prompt, so the data goes wherever your Claude session runs |
| `evals/` scripts (clone only) | Send the synthetic prompts to Claude Code or to your local EvalForge server |

It stores no keys, collects no telemetry, and runs no code from the network. The skill needs to run `python3` on your machine, and it has only been tested in Claude Code.

## Evals

The evals run the real prompt against real models. You can test the eval itself for free:

```bash
python3 evals/dry_run.py
```

If you're logged in to Claude Code, this runs all 6 scenarios on 3 Claude models and shows how often each answer would be shown:

```bash
python3 evals/run_claude_cli.py
python3 evals/score_results.py
```

This cost about $0.13 to $0.39 per run in testing. For the judged rubric and non-Claude models, use [EvalForge Lite](https://github.com/thejaredchapman/evalforge-lite), which needs an OpenRouter key. [docs/EVALS.md](https://github.com/thejaredchapman/spend-explainer/blob/main/docs/EVALS.md) covers both, and the latest results.

## Verification status

| Part | Status |
|---|---|
| Parsing, analysis, verifier, fallback | Covered by the unit tests |
| Fixture format | Matches the API reference's documented fields, but hasn't been compared with a real response |
| Eval cases, result scoring | Run through EvalForge's real `/api/run` with models mocked |
| `fetch` and `run` | Tested with mocked HTTP only, **never against the live APIs** |
| Conclusions on synthetic data | 36 of 36 real answers correct across Haiku 5.5, Sonnet 5.5 and Opus 5.5. See [docs/EVALS.md](https://github.com/thejaredchapman/spend-explainer/blob/main/docs/EVALS.md) |
| Verifier strictness | Caught 35 of 35 wrong figures planted in real answers |
| EvalForge live run | Ran through OpenRouter on 3 models. Opus passed 6 of 6. Sonnet 4.5 and Haiku 4.5 passed 0 of 6, because they wrapped their JSON in fences. See [docs/EVALS.md](https://github.com/thejaredchapman/spend-explainer/blob/main/docs/EVALS.md) |
| Reasoning and advice quality | Rubric judge scored 16 of 18 answers 5 out of 5 and 2 answers 4 out of 5, but it has not judged the Claude 5.5 answers |
| Plugin manifest | Passes `claude plugin validate .` |
| Skill | Tried in headless and interactive Claude Code sessions on synthetic data. It triggered without being named, and showed the fallback for a planted bad number. On default permissions the only prompt was the skill consent |

## Troubleshooting

| Message | Fix |
|---|---|
| `python3: command not found` | Install Python from [python.org](https://www.python.org/downloads/) |
| `Need at least 6 complete days` | Fetch a longer date range, or pass a smaller `--window-days` |
| `ANTHROPIC_API_KEY is not set` | Export the key, or use `demo`, which doesn't need one |
| `HTTP 401` or `403` from `fetch` | Check that you're using an Admin API key, not a regular API key |
| `Could not reach EvalForge` | Start EvalForge first. See [docs/EVALS.md](https://github.com/thejaredchapman/spend-explainer/blob/main/docs/EVALS.md) |
| Every answer shows `json` failed | The model is wrapping its JSON in fences. Run `prompt`, then see [docs/EVALS.md](https://github.com/thejaredchapman/spend-explainer/blob/main/docs/EVALS.md) |

## Project layout

```text
explain_spend.py        runs the command line without installing
spend_explainer/        the pipeline; start with __init__.py
  data/fixtures/        synthetic API responses, one folder per scenario
  data/recorded/        hand-made stand-in answers
skills/explain-spend/   the Claude Code skill
evals/                  EvalForge test cases and scoring
scripts/make_fixtures.py  regenerates the data folders
tests/                  unit tests, no network
docs/                   architecture and eval guides
```

## License

MIT
