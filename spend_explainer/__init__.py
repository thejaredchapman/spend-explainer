"""spend-explainer: explain a Claude spend spike, and never trust the model's numbers.

Read the modules in pipeline order:

    analytics.py  parse Usage and Cost Analytics API responses
    analyze.py    deterministic spike detection; produces the "facts"
    prompt.py     turn facts into a prompt (the verdict is withheld)
    verify.py     reliability check on the model's answer
    fallback.py   model-free report used when the check fails
    pipeline.py   wires the steps together
    client.py     optional live HTTP calls (needs keys)
"""

VERSION = "0.1.0"
