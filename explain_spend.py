#!/usr/bin/env python3
"""Run spend-explainer from a checkout or the plugin folder, with nothing installed."""

import sys

from spend_explainer.cli import main

if __name__ == "__main__":
    sys.exit(main())
