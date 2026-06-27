"""tide.launcher — context → seed → spawn an orchestrator session.

Modules (U11):
  seed.py  — resolve canon + roster + arc + global prompts into a seed string
  menu.py  — `tide menu`: list roster, pick N projects, launch seeded sessions

No autonomy runtime — the launcher only opens a fresh seeded Claude session
(transport is delegated to a pluggable :mod:`tide.adapters` terminal adapter).
"""
