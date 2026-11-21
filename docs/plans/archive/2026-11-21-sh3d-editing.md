# SweetHome3D editing — implementation plan

Status: ✅ SHIPPED 2026-11-21

Spec: `docs/plans/archive/SWEETHOME3D_EDITING_SETUP.md`
Refactor-first: skipped — `scripts/sh3d.py` is a new standalone module; no existing code is restructured.

## Constants (`scripts/sh3d.py`)
- `HASH = "sha256"` (`hashlib.sha256`, 1 MiB chunks); `NAME_RE = r"^[A-Za-z0-9_-]+$"` (refuse other names).
- `SH3D_VERSION_PINNED = "7.6.1"`; `DROP_HOME_ENTRY = False` (NOTES (a)); `DROPPED = {"ContentDigests"}`.
- `CONTENT_ATTRS`, `NUMERIC_ATTRS` per NOTES.md `## SweetHome3D format` (e), (f).
- Backup path: `backups/<name>-<YYYYmmdd-HHMMSS>.sh3d` (`time.strftime("%Y%m%d-%H%M%S")`); refuse if it exists.
- Preview path: `work/sh3d/<name>/preview.png` — deviates from spec (`sh3d_src/<name>/preview.png`): `work/` is already gitignored.
- DTD path: `work/sh3d/SweetHome3D.dtd`, built by `sh3d.py dtd` from the source zip (Stage 2.2); never vendored.
- `.sh3d_meta.json` (2-space indent, trailing newline):
  `{"source_sha256": "<hex>", "sh3d_version_pinned": "<ver>", "entries": [{"name": "Home.xml", "compress_type": 8}, …]}`
- `entries` = every zip entry in original order (dropped ones included, skipped on push); `compress_type` = `ZipInfo.compress_type`.

## CLI shape
- `uv run python scripts/sh3d.py [--homes D] [--src D] [--backups D] [--work D] [--dtd F] {pull,push,validate,inspect,preview} <name>` | `dtd [--src-zip F]`
- Root defaults: repo-relative `homes/ sh3d_src/ backups/ work/`, `--dtd` default `work/sh3d/SweetHome3D.dtd`; tests always pass all roots under `tmp_path`.
- `main(argv: list[str] | None = None) -> int`; `Sh3dError` → `sh3d: <msg>` on stderr, exit 1. Tests call `sh3d.main([...])`.

## Stage 1 — SweetHome3D version + format findings

### Stage 1.1 [USER] install
- [USER] install SweetHome3D on Windows; report the version (Help → About). Claude pins that version everywhere below.
- Done: version string known.

### Stage 1.2 VERIFY against the installed version
- Jar: `ls /mnt/c/Program\ Files*/Sweet\ Home\ 3D/lib/`; then per jar `unzip -l "$j" | grep -i 'SweetHome3D\.dtd'` → jar path + entry path.
- Source: download `SweetHome3D-<ver>-src.zip` (SourceForge) into a new empty `work/sh3d-src/`; unzip there; read only, never run.
- Read `DefaultHomeInputStream`, `HomeFileRecorder`, the app's `HomeFileRecorder` construction (prefer-XML flag), `HomeXMLHandler`, `HomeXMLExporter`.
- Settle: (a) entry read first, `Home.xml` vs `Home`, as the app is configured; (b) fallback when `Home` is absent; (c) ContentDigests absence tolerated.
- Settle: (d) units (cm, radians) and colour encoding (e.g. `%08X` ARGB); (e) `CONTENT_ATTRS`; (f) numeric attrs for `NUMERIC_ATTRS`.
- NOTES.md `## Versions`: one line — SH3D version, jar path, DTD entry path. New `## SweetHome3D format`: one line per (a)–(f).
- `DROP_HOME_ENTRY = True` if `Home` can win over `Home.xml`; else `False`.
- Verify: `grep -c 'SweetHome3D' NOTES.md` ≥ 2; each of (a)–(f) cites a `File.java:line`.
- Done: NOTES.md committed (`docs(notes): SweetHome3D <ver> format findings`).

## Stage 2 — core: pull, validate, push

### Stage 2.1 deps + skeleton + pull
- Focus: `pyproject.toml`, `uv.lock`, `scripts/sh3d.py`, `tests/test_sh3d.py`.
- `uv add lxml` (lock changes deliberately; `UV_LOCKED=1` otherwise). If pyright flags lxml as untyped: `uv add --dev types-lxml`.
- Skeleton: constants, `Roots` dataclass, argparse, `main`, `sha256_file`, `Sh3dError`.
- `pull`: refuse if `homes/<name>.sh3d` missing; dirty check (below); clear `sh3d_src/<name>/`; write entries byte-for-byte.
- `pull` skips `ContentDigests` (and `Home` if `DROP_HOME_ENTRY`); preserves sub-paths (`3/model.obj`); writes meta.
- Dirty check: target = realpath of `<src>/<name>` if it exists, else realpath of `<src>`.
- Then `git -C <target> rev-parse --is-inside-work-tree` fails → refuse; `git -C <target> status --porcelain -- .` non-empty → refuse.
- Test fixture `make_sh3d(path)`: zip with `Home` (STORED), `0` (STORED, binary), `1` (DEFLATED), `Home.xml` (DEFLATED), `ContentDigests`.
- Fixture Home.xml: one level, wall, room (4 points), `pieceOfFurniture` (`model="0" icon="1"`), `doorOrWindow`; src root `git init` + empty commit.
- Tests: meta sha == file sha; entry order + compress_type recorded; resource bytes equal; ContentDigests not written.
- Tests: dirty src refuses; non-git src refuses; missing `.sh3d` refuses; bad name (`../x`) refuses.
- Verify: `uv run pytest tests/test_sh3d.py -q && uv run ruff check scripts/sh3d.py tests/test_sh3d.py && uv run ruff format --check scripts/sh3d.py tests/test_sh3d.py && uv run pyright scripts/sh3d.py tests/test_sh3d.py`
- Done: verify green; commit `feat(sh3d): pull — unpack .sh3d to an entry-ordered source tree`.

### Stage 2.2 validate
- Focus: `scripts/sh3d.py`, `tests/test_sh3d.py`.
- Missing `--dtd` file → `Sh3dError("DTD not found: <path> — run make sh3d-dtd")`.
- `dtd`: read `SweetHome3D-7.6.1-src/src/com/eteks/sweethome3d/io/HomeXMLHandler.java` from `--src-zip` (default `work/sh3d-src/SweetHome3D-7.6.1-src.zip`); take the `<pre>`…`</pre>` javadoc,
  strip ` * `, `&lt;`→`<`, apply the 3 NOTES patches; must load via `etree.DTD` else error; write `--dtd`. Missing zip → error naming the curl in NOTES.
- Parse Home.xml with `etree.XMLParser(resolve_entities=False, no_network=True)`; well-formedness errors reported with line.
- `etree.DTD(dtd).validate(root)`; report `dtd.error_log` lines.
- Refs: every `CONTENT_ATTRS` value without `://` and not `jar:` must exist under `sh3d_src/<name>/`.
- Numbers: any attr value in `{NaN, -NaN, Infinity, -Infinity}` → error; `NUMERIC_ATTRS` empty or non-float → error.
- `NUMERIC_ATTRS` is a pinned literal frozenset (ruled 2026-11-21); a drift test re-derives it from the source zip, skipped when absent.
- All errors collected, printed one per line, exit 1; clean → `validate: ok <name>`.
- Tests use a minimal synthetic DTD written in `tmp_path` covering the fixture elements; never the real DTD.
- Tests: ok fixture passes; missing resource ref fails; `x="NaN"` fails; `width=""` fails; malformed XML fails; missing DTD fails with message.
- Tests (`dtd`, synthetic zip in `tmp_path`): extract + 3 patches loads; missing zip names the curl; unloadable DTD errors.
- Verify: Stage 2.1 command. Done: green; commit `feat(sh3d): validate — DTD, resource refs, NaN/empty numbers`.

### Stage 2.3 push
- Focus: `scripts/sh3d.py`, `tests/test_sh3d.py`.
- Order: meta exists (else "pull first") → `homes/<name>.sh3d` exists → sha == `source_sha256` (else "edited in GUI since pull — run pull first").
- Then validate (fail → refuse) → src files ⊆ meta entries + `.sh3d_meta.json` (unknown file → refuse; new resources unsupported).
- Then `shutil.copy2` to backup path → write `homes/.<name>.sh3d.tmp` → `os.replace` onto `homes/<name>.sh3d`.
- Pack: meta order, per-entry `compress_type`, skip `DROPPED`; Home.xml BOM stripped, must decode UTF-8, declared encoding absent or UTF-8.
- After pack: `source_sha256` = sha of new file; print `push: ok <name> — commit sh3d_src/<name>/.sh3d_meta.json; reopen in SH3D`.
- Warn on stderr if meta `sh3d_version_pinned` ≠ `SH3D_VERSION_PINNED`.
- Tests: pull→push round-trip: names/order (minus dropped), compress_type, bytes of every kept entry equal source.
- Tests: ContentDigests absent; `Home` absent iff `DROP_HOME_ENTRY`; backup bytes == original; second push without pull succeeds.
- Tests: conflict — modify `homes/x.sh3d` after pull → exit 1, `.sh3d` unchanged, no backup written.
- Tests: BOM in Home.xml stripped; invalid Home.xml → push refuses, `.sh3d` unchanged; unknown src file refuses.
- Verify: Stage 2.1 command. Done: green; commit `feat(sh3d): push — conflict check, backup, order/compression-preserving pack`.

## Stage 3 — inspect + preview

### Stage 3.1 inspect
- Focus: `scripts/sh3d.py`, `tests/test_sh3d.py`.
- Fixed-width tables, one per kind: levels (id, name, elevation, height); walls (id, xStart, yStart, xEnd, yEnd, thickness, height, L/R colour, level).
- Rooms (id, name, area m² by shoelace, level); furniture (id, name, x, y, angle°, width, depth, height, level); doors/windows same columns.
- `furnitureGroup` children flattened with `group/<id>` prefix; wall height absent → home `wallHeight`, marked `*`.
- Colours rendered `#RRGGBB` per the 1.2 encoding; last line `unhandled: <tag>×<count> …` for tags not covered.
- Tests: fixture output contains the wall id, the furniture name, room area 2 decimals, and an `unhandled:` line.
- Verify: Stage 2.1 command. Done: green; commit `feat(sh3d): inspect — levels, walls, rooms, furniture, openings`.

### Stage 3.2 preview
- Focus: `scripts/sh3d.py`, `tests/test_sh3d.py`.
- `matplotlib.use("Agg")` before `pyplot` import; one subplot per level; y-axis inverted (SH3D y grows down); equal aspect; cm axes.
- Walls as thickness polygons, rooms filled + name at centroid, furniture rotated rectangles + name, doors/windows distinct colour.
- Curved walls (`arcExtent`) drawn straight and listed in `unhandled:`; output `work/sh3d/<name>/preview.png`, path printed.
- Tests: file exists, starts with `b"\x89PNG"`, size > 1 KiB; nothing written under `--src`.
- Verify: Stage 2.1 command. Done: green; commit `feat(sh3d): preview — top-down matplotlib PNG under work/`.

## Stage 4 — Makefile, README, home-file links

### Stage 4.1 Makefile targets
- Focus: `Makefile`, `tests/test_sh3d.py`.
- `SH3D_SRC_ZIP ?= work/sh3d-src/SweetHome3D-7.6.1-src.zip`, `SH3D_DTD := work/sh3d/SweetHome3D.dtd`.
- Parse-time guard: `ifneq ($(filter pull push validate inspect preview,$(MAKECMDGOALS)),)` + `$(origin NAME)` ≠ `command line` or NAME ∉ `[A-Za-z0-9_-]+` → `$(error …)` (WSL exports NAME).
- `sh3d-dtd` (phony, always rebuilds): `uv run python scripts/sh3d.py --dtd $(SH3D_DTD) dtd --src-zip $(SH3D_SRC_ZIP)`.
- `validate push: sh3d-dtd`; each target runs `uv run python scripts/sh3d.py <cmd> $(NAME)`; `push` then prints
  `wslpath -w "$$(realpath homes)/$(NAME).sh3d"`.
- Add the five + `sh3d-dtd` to `.PHONY` and `help`.
- Tests: `make -n inspect` → non-zero, stderr has `NAME`; `make -n inspect NAME=x` prints `scripts/sh3d.py inspect x`.
- Falsifier: `make validate NAME=x SH3D_SRC_ZIP=/nonexistent; echo exit=$?` → `exit=2`, stderr names the zip.
- Verify: Stage 2.1 command + the falsifier. Done: commit `feat(make): pull/push/validate/inspect/preview NAME= targets`.

### Stage 4.2 links + README
- Focus: `README.md`, `.git/info/exclude` (untracked).
- `homes/`, `sh3d_src/`, `backups/` are local, untracked here.
- Create the three local dirs; append `/homes`, `/sh3d_src`, `/backups` to `.git/info/exclude`.
- README `## SweetHome3D editing workflow`: spec steps 1–5 with `make … NAME=x`; `sh3d_src` commits via `git -C sh3d_src …`.
- README also: commit `.sh3d_meta.json` after `push`; close the file in SH3D before `push`; open the path `make push` prints.
- Falsifier: `git ls-files | grep -E '^(homes|sh3d_src|backups)/' ; test $? -eq 1` and `git status --short` lists none of the three.
- Done: commit `docs(readme): SweetHome3D editing workflow` (README only).

## Stage 5 — verify + report

### Stage 5.1 gate + review
- `make check` (whitespace + `ruff check .` + `ruff format --check .` + `pyright` + `pytest -q`) green.
- `Agent(subagent_type="code-reviewer")` over Stages 2–4 diffs; fix findings in a follow-up commit.
- Done: gate green after fixes.

### Stage 5.2 live e2e [USER]
- Ruled 2026-11-21: edit→push→GUI-reopen leg proven live via the realpath under `\\wsl$\…`; rest runs on scratch `homes/x.sh3d`.
- Round-trip: `make pull NAME=x` → `make push NAME=x` no edits → pushed Home.xml parses equal to the source's.
- Conflict: byte change to `homes/x.sh3d` after pull → `make push NAME=x` exits non-zero, `backups/` unchanged.
- A symlinked `homes/` path under `\\wsl$`: untested → open item in 5.3; README names the realpath `make push` prints.

### Stage 5.3 honest report
- List unverified items left (read order, units, DTD path, symlink via `\\wsl$`) — each either cited in NOTES.md or flagged open.
- List the `unhandled:` tags `make inspect NAME=x` printed on the real home (names of tags only — no home data in this repo).
- Done: plan `git mv` to `docs/plans/archive/`; commit messages name no home data.
