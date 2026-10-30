# Evals

The evals measure how well real models perform on the six synthetic scenarios. There are two ways to run them: through your Claude Code login, or through [EvalForge Lite](https://github.com/thejaredchapman/evalforge-lite).

## Latest results

Run on 2026-10-30 through `run_claude_cli.py`: 6 scenarios on 3 models, run twice, for 36 answers in total.

| Model | Right conclusion | Shown after verification |
|---|---|---|
| `claude-haiku-5-5` | 12 of 12 | 12 of 12 |
| `claude-sonnet-5-5` | 12 of 12 | 12 of 12 |
| `claude-opus-5-5` | 12 of 12 | 12 of 12 |

We also changed one dollar figure in each real answer that had one, by 7% plus $3. The verifier caught 35 of 35. One answer had no dollar figure to change.

**What the first run found.** Before a fix, 15 of 18 answers were shown. All three rejections were correct answers: the verifier dropped the minus sign, so `0.53` failed to match `-0.53`. The stand-in answers had never exposed this, and it is now covered by a regression test. The second run used fresh answers after the fix.

### EvalForge run

Run on 2026-10-30 through OpenRouter, with `openai/gpt-4o-mini` as the rubric judge. It covered 6 scenarios on 3 models, one run each, for $0.25.

| Model | EvalForge grade | Rule checks passed | Shown after verification | Rubric scores (out of 5) |
|---|---|---|---|---|
| `~anthropic/claude-opus-latest` | A+ | 6 of 6 | 6 of 6 | 5, 5, 5, 5, 5, 5 |
| `anthropic/claude-sonnet-4.5` | A | 0 of 6 | 0 of 6 | 5, 4, 5, 5, 5, 5 |
| `anthropic/claude-haiku-4.5` | A | 0 of 6 | 0 of 6 | 5, 4, 5, 5, 5, 5 |

**Both 4.5 models wrapped all 6 answers in Markdown fences,** despite the prompt saying not to. The strict `json` check rejected them, so a user of those models would always see the fallback. With the fences removed by hand, 5 of 6 passed for each model, and every conclusion was correct. Claude 5.5 models did not use fences in any of the 36 answers above.

**The two other rejections were a correct number the verifier could not trace.** On `cache_regression`, both models wrote a cache hit rate fall of 78.9 points, which is 87.7 minus 8.8. The arithmetic is right, but the prompt forbids new calculations and the figure is not in the facts.

**A second run gave the same result.** Opus again passed 6 of 6, and both 4.5 models again fenced all 6 answers and passed 0 of 6. With fences removed by hand, Haiku 4.5 passed 5 of 6 and Sonnet 4.5 passed 4 of 6. Both failures were figures the prompt forbids calculating: 78.9 again, and 86.6 from Sonnet 4.5 on `model_mix_shift`. Grades moved to A+ for all three models, and the rubric scored all 18 answers 5, so small grade changes between runs are noise.

**A high grade does not mean the product works.** EvalForge gave the 4.5 models an A while every answer failed the product's check, because rule checks are only part of its score. Read the `shown` column for the product question.

The two rubric scores of 4 were both on `one_day_outlier`. One answer could have stressed normality more clearly, and the other suggested looking into the one-day outlier. Neither was an error.

These results show the prompt and verifier work on six synthetic scenarios. They don't show that the models are reliable on real spend.

## What each layer measures

| Layer | When it runs | Question | Tool |
|---|---|---|---|
| Rule checks | Offline, per model | Is the conclusion right? | EvalForge `checks` |
| Rubric | Offline, per model | Is the reasoning sound and the action useful? | EvalForge judge |
| Verifier | Every real answer | Is this answer safe to show? | `verify.py` |

The rule checks and the verifier are both needed. In a mocked run through EvalForge's real `/api/run`, six `wrong_number` answers passed all of EvalForge's rule checks, because those checks read the conclusion rather than the prose. The verifier rejected all six.

## Scenarios

| Scenario | Planted situation | Correct conclusion |
|---|---|---|
| `quiet` | Normal traffic | `no_spike` |
| `one_day_outlier` | One expensive day in the baseline | `no_spike` |
| `model_mix_shift` | One key moves from Haiku to Opus | `model_mix_shift`, search workspace |
| `cache_regression` | Most cached input becomes uncached | `cache_regression`, support workspace |
| `volume_increase` | One key's requests rise 12x | `volume_increase`, jobs key |
| `broad_growth` | All traffic rises 1.8x | `broad_growth`, no driver |

## Step 1: Check the eval itself (free, no key)

```bash
python3 evals/build_cases.py
python3 evals/dry_run.py
```

This applies the rule checks to the stand-in answers. `Unexpected outcomes: 0` means good answers pass, and bad answers fail where they should.

## Step 2: Run it with your Claude Code login

This needs only the `claude` command, and it costs little: $0.13 to $0.39 per run in testing.

```bash
python3 evals/run_claude_cli.py
python3 evals/score_results.py
```

It runs the rule checks and the verifier on `claude-haiku-5-5`, `claude-sonnet-5-5` and `claude-opus-5-5`. Use `--models` to change them.

It differs from EvalForge in three ways: it doesn't run the rubric judge, Claude Code adds a short system prompt, and it covers Claude models only. Tools, settings and MCP servers are turned off.

## Step 3: Run it with EvalForge

You need an [OpenRouter API key](https://openrouter.ai/workspaces/default/keys), Python 3.10 or newer, and some credit. The run makes 18 model calls plus judge calls.

In a new terminal, start EvalForge. It reads the key from its own environment, so the key never reaches this package.

```bash
git clone https://github.com/thejaredchapman/evalforge-lite.git
cd evalforge-lite
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export OPENROUTER_API_KEY="your key"
python app.py
```

Leave it running. From this package's folder, in another terminal:

```bash
python3 evals/run_evalforge.py
python3 evals/score_results.py
```

The first command saves `evals/results/latest.json`. The second prints how often users would see each model's answer rather than the fallback:

```text
model                               rules  shown  fallback  errors  failed checks
```

`rules` counts answers that passed every rule check, and `shown` counts answers that passed the verifier.

For leaderboards, grades and a PDF report, see the [EvalForge docs](https://thejaredchapman.github.io/evalforge-lite/).

To choose other models:

```bash
python3 evals/run_evalforge.py --models anthropic/claude-sonnet-4.5 openai/gpt-5
```

Use IDs from EvalForge's model list. It allows up to 4 models per run, and 3 runs per 8 hours per browser session.

## Reading the result

For each model, look at the `failed checks` column.

| Failed check | Likely meaning | What to do |
|---|---|---|
| `conclusion` or `driver` | The model reasoned wrongly | Improve the prompt, or use a stronger model |
| `numbers` | Either an invented number, or a correct derived number the verifier cannot trace | Read the answer. If it was correct, add that value to `facts` |
| `json` | Usually Markdown fences | Tighten the prompt |
| `ids` | A shortened or invented ID | Read the answer |

A fallback is not automatically a failure of the system, because a caught mistake is the design working. What matters is whether each rejection was justified.

## Limits of this eval

- **Small sample.** Six scenarios show that the approach works, not that a model is reliable in general.
- **Synthetic data.** Real organizations have messier patterns, including several causes at once.
- **Model coverage.** EvalForge's catalog pins Claude only up to 4.5, plus the alias `~anthropic/claude-opus-latest`, so use `run_claude_cli.py` for Claude 5.5 models. Sonnet 4.5 retires on November 30, 2026.
- **Rubric coverage.** The rubric has judged only the EvalForge run, not the Claude 5.5 answers, and one cheap judge model scored all of it.
- **Judge bias.** The rubric score comes from an LLM, so treat it as a signal rather than a measurement.
