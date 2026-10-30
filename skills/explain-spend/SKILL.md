---
name: explain-spend
description: Explain why Claude API spend changed, using Usage and Cost Analytics API data or the bundled synthetic scenarios. Use when someone asks why Claude costs went up, whether spend looks normal, or who or what caused a cost spike. Every answer is checked against the data, and a rule-based report replaces it if a check fails.
allowed-tools: Bash(python3:*), Write
---

# Explain Spend

You write the explanation. A script checks it. Show the user only what the script prints in step 4.

`PLUGIN_ROOT` below means the directory two levels above this skill's base directory. Use its full path. Run each command exactly as shown, on its own, with no `cd`, `;`, `&&` or `echo`.

## Step 1: Choose the data

- If the user has a directory with `usage.json` and `cost.json`, use `--data <dir>`.
- If they want real data and have `ANTHROPIC_ADMIN_API_KEY` set, fetch it first. The range can be at most 31 days:

  ```bash
  python3 PLUGIN_ROOT/explain_spend.py fetch --start 2026-10-01T00:00:00Z --end 2026-10-18T00:00:00Z --out <dir>
  ```

- Otherwise use a synthetic scenario and say that it is synthetic: `--scenario cache_regression`. The others are `quiet`, `one_day_outlier`, `model_mix_shift`, `volume_increase` and `broad_growth`.

## Step 2: Get the prompt

```bash
python3 PLUGIN_ROOT/explain_spend.py prompt --scenario cache_regression
```

Do not run the `analyze` command, and do not read `expected.json`. They contain the rules' conclusion. Reaching it yourself from the data is what makes the check meaningful.

## Step 3: Write your answer to a file

Follow the prompt's instructions exactly. Use the Write tool, not a shell heredoc, to save only the raw JSON object to a temporary file, with no Markdown fences.

## Step 4: Verify

```bash
python3 PLUGIN_ROOT/explain_spend.py verify --summary --scenario cache_regression --answer <file>
```

The `shown from` line gives the outcome:

- **`model (verified)`:** the answer passed. Show the printed report.
- **`deterministic fallback`:** the answer failed. Show the printed fallback report, and tell the user it came from the rule-based fallback and name the failed check.

After a failure, do not edit the answer and verify again, and do not read the answer file again. Never repeat a rejected answer or any value from it, even to say that it was wrong. Name only the check.

## Figures the data does not contain

Do not add figures to the report. If the user asks for a projection over a period, keep it out of the JSON answer and run:

```bash
python3 PLUGIN_ROOT/explain_spend.py project --scenario cache_regression --days 30
```

Show that output after the report, unchanged, under the label "Projection (calculated, not verified)". Do not work out figures yourself. If the user asks for any other figure the data does not contain, say this skill cannot provide it.
