<!-- CONTRACT:start -->
# tide

## Что это
**tide = simplified orchestration machine** — standalone Python project + single
`tide` binary. Pure CLI + markdown, synchronous, human-driven, **no autonomy**
(no web, no Telegram, no daemon). `tide init` unfolds the machine in a dir → that
dir is the control-home/roster from which the human leads all projects. tide
**dogfoods itself** (led as a tide project under its own `.tide/`).

The proven `arcs` + `canon` (two-n: **cannon**) tools are pulled inside as
internal modules of the one binary. Two deterministic roles: **orchestrator**
(cross-project session, owns roster/contracts/cannon-merge/promote) and
**worker** (one arc, produces output + proposes a cannon-delta). cannon-merge is
the single serialization point.

goal:    ship the tide CLI scaffold → working arc/cannon/contract modules, greenfield
stage:   0

## Этапы
- **Этап 0 (сейчас):** scaffold — package layout, argparse CLI root with stubbed
  command groups, role gate (`TIDE_ROLE`), test harness (`tmp_project` fixture),
  build conventions documented in README. Skeleton imports + suite green.
- **Этап 1 (план):** build units U1–U13 in dependency order (core → cannon →
  arc → candidates → strictness/roster → contract → sync → board → init/wiring →
  hooks → launcher/adapters → prompts/skill → e2e+dogfood). See README "build order".

## Контекст
- **Runtime: stdlib only** (argparse, hashlib, pathlib, os, re, datetime). Test
  dep: pytest only. No `click`, no web deps.
- **Handler pattern:** each module exposes plain functions + `register(subparsers)`;
  `cli.py` only wires groups, logic lives in argparse-free functions. See README
  "## build conventions".
- **State on disk:** per project `<project>/.tide/{cannon,arcs,state}`;
  control-home adds `roster.md`. Formats per `build-blueprint.md` tide_dir_format.
- **Source of truth for the build:** the design + blueprint in focus arc 42
  (`.arcs/arcs/42-@tide/arcs/01-design-and-plan/output/`).
<!-- CONTRACT:end -->

## tool ⊥ instance — the bright line

tide ships as a **package** (`pip install` / `uv build` → wheel+sdist), and the
package is **`src/tide/` only**. The wheel contains nothing but the `tide/`
Python package + metadata; the sdist adds `src/` + `tests/` + `README.md`. So:

- **Shipped (the tool):** everything under `src/tide/`. Must stay generic — no
  absolute home paths (`/Users/…`, `/home/…`), no personal identity, no
  instance-specific project names. Contract passports store the **portable
  project name** (`Path(root).resolve().name`), never the absolute path.
- **NOT shipped (this instance):** `.tide/` (our own dogfood work-stream),
  `examples/` (dogfood run captures), `docs/`, `prompts/`, `skills/`, `rules/`,
  this `CLAUDE.md`, `roster.md`. These are git-tracked dev history of *this*
  instance — they do **not** travel with `pip install`. git-tracking ≠ shipping.

A second person gets a clean generic tide via `pip install tide` + `tide init`;
they inherit none of our content, paths, or PII. The enforcement gate is
`tide verify --portable` — it scans the shipped package source + a fresh
`tide init` skeleton for absolute home paths / instance tokens and fails loud.
