# Stand-in answers

**These are not real model outputs.** `scripts/make_fixtures.py` generates them so the demo and tests run with no key.

| File | What it is | Which check should reject it |
|---|---|---|
| `good.txt` | A correct answer | None |
| `wrong_number.txt` | Correct, except for an invented dollar figure | `numbers` |
| `wrong_cause.txt` | Correct, except for the wrong cause | `conclusion` |
| `invented_id.txt` | Correct, except for an API key that does not exist | `ids` |
| `fenced_json.txt` | A correct answer wrapped in a Markdown fence | `json` |

Real model results come from the live eval. See [docs/EVALS.md](../docs/EVALS.md).
