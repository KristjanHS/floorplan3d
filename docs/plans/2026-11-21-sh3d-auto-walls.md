# SH3D auto walls — generate internal walls into Home.xml, user fixes in the GUI

Status: proposed 2026-11-21. Goal: `make walls NAME=<name>` writes detected walls into `sh3d_src/<NAME>/Home.xml`, tagged as generated;
the user then edits (not traces) them in SweetHome3D. Measured results (scores, overlay verdicts) → a local results doc, never here.

## Rulings (user, 2026-11-21)
- Detector: classic parallel-pair line segments (arXiv 1710.01802; 1911.11356) with OpenCV on the level's background image. CubiCasa5K / PyTorch OUT.
- Fallback ONLY if below bar: FloorplanToBlender3d wall boxes (`scripts/ftb_probe.py` `box_segments` :64 / `verts_segments` :72 over `work/<floor>/ftb`), merged and scored the same way.
- Bar: hand-traced walls with thickness inside `GAP_CM` = ground truth (thicker exterior walls excluded). Traced wall matched ⇔ a generated wall within 10 cm centre-line distance and 3°. PASS ≥ 70 % per level; extras reported, not gated.
- Internal only = skip generated walls matching a traced wall (no thickness cap). Edited auto wall on re-run = adopt (untag, keep). Height = the wall's level `height`.
- Detect on the level's embedded background image (it is the trace PNG — no second px frame).
- Re-run replaces ONLY tagged walls; hand-traced walls never touched. `make push NAME=` repacks unchanged.
- `scripts/preprocess.py` `wall_mask` is NOT reused (fails on double-line + hatching walls).

## Known facts (SH3D 7.6.1 source, `work/sh3d-src/`)
- Background px → plan cm: `x = u·s − xOrigin`, `y = v·s − yOrigin`, `s = scaleDistance / |start−end|`; scale points in image px, origins in cm
  (`swing/PlanComponent.java:2573-2575`, `model/BackgroundImage.java:123-140`). Hypothesis until Stage 1.2's falsifier passes.
- DTD: `wall (property*, …)`, `id ID #REQUIRED`, `wallAtStart`/`wallAtEnd IDREF` → deleting a wall must clear refs to it or validation fails.
- `cv2.createLineSegmentDetector` present in the locked OpenCV (5.x).

## Refactor-first assessment
`scripts/sh3d.py` (633 lines) owns zip/DTD/inspect I/O; adding detection + XML writing would push it past ~1000 lines with two unrelated concerns.
Recommend: NEW `scripts/walls.py` (pure numpy/cv2 geometry: segments, pairing, merge, score — no XML) + NEW `scripts/sh3d_walls.py`
(Home.xml side: background transform, traced-wall read, tagged write, CLI) importing `sh3d.read_home`/`load_dtd`/`validate_errors`. No edits to `sh3d.py` beyond imports it already exports.

## Default constants (`scripts/walls.py`; each "pick default; <test> validates")
| Name | Value | Validated by |
|---|---|---|
| `GAP_CM` | 8–40 cm (→ px via level scale) | `test_pair_rejects_gap_outside_range` |
| `MIN_SEG_CM` | 40 cm (drops hatch strokes) | `test_segments_drop_hatching` |
| `PARALLEL_DEG` | 2° | `test_pair_parallel_tolerance` |
| `MIN_OVERLAP` | 0.6 of the shorter segment | `test_pair_requires_overlap` |
| `MERGE_OFFSET_CM` / `MERGE_GAP_CM` / `MERGE_DEG` | 5 cm / 100 cm (bridges door gaps) / 2° | `test_merge_collinear_across_door_gap` |
| `SNAP_CM` | 15 cm | `test_snap_l_and_t_junctions` |
| `MATCH_DIST_CM` / `MATCH_DEG` / `MATCH_COVER` | 10 cm / 3° / 0.5 of traced length | `test_score_tolerance_edges` |
| `PASS_RATIO` | 0.70 | `test_score_pass_threshold` |

## Stage 1 — Transform + traced-wall overlay (falsifier for px↔cm)
- **1.1 Level backgrounds.** `sh3d_walls.py`: `level_backgrounds(root, tree)` → per level (level bg, else home bg) image path, scale, origin; `px_to_cm`/`cm_to_px`.
  Tests `tests/test_sh3d_walls.py` on a toy Home.xml + tiny PNG: known scale/origin round trip; level without bg skipped. Falsifier: swapped sign in origin fails `test_px_to_cm_matches_plancomponent`.
- **1.2 Overlay + ink check.** `make walls-overlay NAME=` draws traced walls (both faces at ±thickness/2) on each level's image → `work/walls/<NAME>-L<i>.png`; prints per-level
  ink-hit rate (dark px sampled along faces). Test: synthetic image + matching walls ≥ 0.8, same walls shifted 50 cm < 0.3 (the check CAN fail). Also report whether the image equals a `work/*_trace.png`.
- **1.3 [USER] eyeball** the overlay: traced walls sit on drawn wall lines. Record ink rates + image identity in a local results doc. Misfit → stop, fix transform.

## Stage 2 — Pair detector (`scripts/walls.py`)
- **2.1 Fixture + segments.** `tests/wall_fixtures.py`: numpy canvas with double-line walls (known thickness, L/T junctions, a door gap) + diagonal hatching between lines.
  `segments(img, px_per_cm)` = LSD + `MIN_SEG_CM` filter. Tests `tests/test_walls.py`: all wall faces found; `test_segments_drop_hatching` asserts zero hatch segments survive.
- **2.2 Pairing.** `pair(segs)` → `Wall(centre p1,p2, thickness=gap)` for parallel, overlapping pairs with gap in `GAP_CM`; each segment used once (nearest valid partner).
  Tests: fixture thicknesses recovered ±2 px, centre ±1 px; `test_pair_rejects_gap_outside_range` (50 cm gap → none); `test_pair_requires_overlap`.
- **2.3 Merge + snap.** `merge(walls)` joins collinear pieces; `snap(walls)` moves endpoints within `SNAP_CM` onto shared junction points.
  Tests: `test_merge_collinear_across_door_gap`, `test_snap_l_and_t_junctions`; falsifier: two parallel walls 30 cm apart must NOT merge.

## Stage 3 — Score + overlay (thinnest visible slice; no Home.xml writes)
- **3.1 Score.** `walls.score(generated, traced)` → matched/traced, unmatched traced ids, extras. Tests `test_score_tolerance_edges` (9 cm/2.9° match; 11 cm/3.1° miss), `test_score_pass_threshold`.
- **3.2 `make walls-score NAME=`.** `sh3d_walls.py score NAME`: per level detect → cm → score vs traced walls on that level; prints table + PASS/FAIL; overlay PNG (image + traced green,
  generated magenta, unmatched traced red) → `work/walls/<NAME>-L<i>-score.png`. End-to-end test on toy home + fixture image in `tmp_path`. Read-only: test asserts Home.xml sha unchanged.
- **3.3 Run live + [USER] GO/NO-GO.** Prereq [USER]: trace ~5 internal walls per level in SH3D, then `make pull`. Run on the user's home; record per-level scores in a local results doc. [USER] eyeballs the score overlays.
  Gate: every level ≥ 70 % and user GO → Stage 5. Otherwise → Stage 4.

## Stage 4 — Fallback: FTB wall boxes (ONLY if Stage 3.3 is below bar)
- **4.1 FTB source.** `sh3d_walls.py` loads the last convert's `work/<floor>/ftb` walls via `ftb_probe.box_segments`/`verts_segments` → `Wall` (box long axis = centre, short side = thickness),
  FTB px → background px via `plans.at_downscale`/work meta. Test: synthetic boxes → expected walls; falsifier: wrong downscale fails the position assert.
- **4.2 Merge + rescore.** `walls.union(pairs, ftb)` dedupes with the match tolerances; `walls-score SOURCE=pairs|ftb|both`. Test: duplicate wall from both sources counted once.
- **4.3 [USER] gate again.** Record scores in a local results doc. Still below bar → STOP, record, ask (no write-in).

## Stage 5 — Write-in (`make walls NAME=`)
- **5.1 Tag survival check.** Tag = `<property name="floorplan3d.auto" value="<geometry sig>"/>` + id prefix `auto-`. Test: tagged wall in toy Home.xml passes `validate_errors`.
  [USER] on a scratch home (`NAME=autotest`, never the real one): push → open in SH3D → move a wall → save → pull; assert property + id survive. Fails → pick the surviving one; record in NOTES.md.
- **5.2 `write_walls()`.** Remove tagged walls + clear `wallAtStart`/`wallAtEnd` refs to them; skip generated walls that match a traced wall; insert new walls at the DTD-legal position
  (after the last `<wall>`), `level` set, height = the level's `height`, numbers `.`-decimal. Tests: hand walls byte-identical; second run byte-identical (idempotent); dangling ref cleared; DTD clean.
- **5.3 Edited-tag guard + target.** Tagged wall whose geometry ≠ its sig = user-edited → untag (becomes hand-traced), report. `make walls NAME=` (refuses on dirty `sh3d_src/<NAME>`, like pull).
  Tests: edited wall survives a re-run untagged; unedited replaced.

## Stage 6 — Close
- **6.1** `make check` green. Live: `make walls NAME=<name>` → `make push NAME=<name>` → [USER] opens the printed Windows path in SH3D: walls on drawn lines, editable, saves cleanly.
- **6.2** `code-reviewer` sub-agent over the whole diff; fix findings. NOTES.md: background transform + tag-survival facts (generic only). Results → a local results doc. Archive this plan.
