#!/usr/bin/env python3
"""Fixture: dies immediately on start, the way a server with a missing dep does."""
import sys

sys.stderr.write("ModuleNotFoundError: No module named 'totally_missing_dep'\n")
sys.exit(1)
