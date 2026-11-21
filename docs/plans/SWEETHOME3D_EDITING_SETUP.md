# Task: extend `floorplan3d` so Claude Code can safely edit my Sweet Home 3D files

→ plan: `docs/plans/2026-11-21-sh3d-editing.md`

You are Claude Code working in my existing `floorplan3d` repo. Add a small, reliable workflow for editing `.sh3d` files as text (Home.xml) while I keep using the Sweet Home 3D GUI for viewing and manual work. Work step by step, verify each step, and ask me before anything irreversible.

## Background (facts — VERIFY where marked)
- A `.sh3d` file is a ZIP. Entries: `Home.xml` (XML, DTD `SweetHome3D.dtd`), legacy `Home` (Java-serialized, kept for compatibility), optional `ContentDigests` (checksums), and numbered entries (`0`, `1`, … = 3D models, textures, images referenced from Home.xml).
- After external edits that change referenced resources, `ContentDigests` must be removed.
- VERIFY which entry SH3D reads first (`Home.xml` vs `Home`) by reading the Sweet Home 3D source for my installed version (e.g. `DefaultHomeInputStream` / `HomeFileRecorder` in the SweetHome3D jar or SourceForge source). If `Home` could win, the pack step must drop the stale `Home` entry. Record the finding in `NOTES.md`.
- VERIFY units in Home.xml (expected: centimetres, angles in radians) from the DTD/source.
- I run Sweet Home 3D on Windows; the repo lives in WSL. I open files via `\\wsl$\...`.

## Ground rules
- The source of truth for Claude's edits is the unpacked `Home.xml`; the `.sh3d` is what I open in the GUI. These two can diverge — handle that explicitly (see sync rules).
- Never generate a whole home from scratch; only edit homes that were saved by Sweet Home 3D (they contain the needed resources).
- Never edit while I have the file open in SH3D. Ask me to close it before `push`.
- Repo is PUBLIC: home files (`sh3d_src/`, `homes/`, `backups/`) never land in it — they stay local, untracked
  and excluded via `.git/info/exclude` (like `input/`); never upload my plans anywhere.
- Python-first, reuse the repo's existing venv and Makefile style. Use `lxml` for XML.

## Layout to add
```
homes/                 # <name>.sh3d files I open in the GUI (gitignored; built artifacts + my GUI saves)
sh3d_src/<name>/       # unpacked contents, committed to git (Home.xml + resource entries)
  Home.xml
  0, 1, ...            # resources, byte-for-byte
  .sh3d_meta.json      # entry order, compression per entry, sha256 of the .sh3d it came from
backups/               # timestamped copies of .sh3d before every push (gitignored)
scripts/sh3d.py        # CLI with subcommands below
```

## `scripts/sh3d.py` subcommands
- `pull <name>` — unpack `homes/<name>.sh3d` → `sh3d_src/<name>/`, record its sha256 + entry order/compression in `.sh3d_meta.json`. Refuse if `sh3d_src/<name>/` has uncommitted changes.
- `push <name>` — validate, then pack `sh3d_src/<name>/` → `homes/<name>.sh3d`:
  - refuse if the current `homes/<name>.sh3d` sha256 ≠ the one recorded at last pull (= I edited in the GUI since; tell me to `pull` first),
  - copy the old file to `backups/<name>-<timestamp>.sh3d`,
  - preserve original entry order and compression, write Home.xml as UTF-8 without BOM,
  - drop `ContentDigests`; drop or keep `Home` according to the VERIFY finding,
  - update the recorded sha256.
- `validate <name>` — XML well-formed; DTD validation (extract `SweetHome3D.dtd` from the installed SweetHome3D jar — VERIFY its path); every resource referenced in Home.xml exists as an entry; no NaN/empty numeric attributes.
- `inspect <name>` — print a compact table of levels, walls (id, start/end, thickness, height, colours), rooms (name, area), furniture (id, name, x, y, angle, width/depth/height), doors/windows. This is how I and you refer to items.
- `preview <name>` — render a top-down PNG from Home.xml with matplotlib (walls, rooms with names, furniture rectangles with names, doors/windows). Write to `sh3d_src/<name>/preview.png` (gitignored).

## Makefile targets
`make pull NAME=x`, `make push NAME=x`, `make validate NAME=x`, `make inspect NAME=x`, `make preview NAME=x`.

## Editing workflow (document this in README.md)
1. I trace/edit in the SH3D GUI, save to `homes/<name>.sh3d`, close it.
2. `make pull NAME=x` → commit ("pull: GUI edits").
3. I ask Claude Code for an edit (e.g. "move the sofa 40 cm toward the window", "paint bedroom walls #E8E4DC", "add a 60×200 cm wardrobe on the north wall of the bedroom").
4. Claude Code: `inspect` → edit Home.xml with a small script or precise edit → `validate` → `preview` and look at the PNG yourself → show me the `git diff` summary → commit with a descriptive message.
5. `make push NAME=x` → I open the file in SH3D to check in 3D.

## Verification before saying "done"
- Round-trip test: `pull` then `push` with no edits → SH3D opens it identically (ask me to confirm once).
- Edit test: move one furniture item by 50 cm via Home.xml → `validate`, `preview` shows the move → I confirm in the GUI.
- Conflict test: change `homes/x.sh3d` after pull → `push` must refuse.
- Unit tests (pytest) for pull/push round-trip byte equality of resources and for the conflict check.

## Report honestly
List anything unverified (entry read order, units, DTD path), and any SH3D features you saw in Home.xml but did not support in `inspect`/`preview`.
