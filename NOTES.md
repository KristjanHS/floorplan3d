# Notes

## Versions
- Upstream `vendor/FloorplanToBlender3d` @ `2da4c95` (vetted: `docs/security-review-FloorplanToBlender3d.md`).
- Blender 5.2.4 LTS, `~/.local/opt/blender-5.2.4-linux-x64/`, tarball sha256 `2fd14c50987426897ba60237ee996edf2b4151f4b1a72a460ca20be58c1d2d86`.
- Upstream run-path deps at current versions via `uv add` (never its `requirements.txt` pins); exact versions in `uv.lock`.
- SweetHome3D 7.6.1 (Windows, `C:\Program Files (x86)\Sweet Home 3D\lib\SweetHome3D.jar`, 32-bit Java 1.8.0_202): no `.dtd` in any jar.
- SweetHome3D 7.6.1 source `work/sh3d-src/SweetHome3D-7.6.1-src.zip` sha256 pinned in `sh3d.SRC_ZIP_SHA256` (`build_dtd` refuses a mismatch); DTD = `io/HomeXMLHandler.java:80-463` javadoc.
- Fetch: `curl -sSL -o work/sh3d-src/SweetHome3D-7.6.1-src.zip https://sourceforge.net/projects/sweethome3d/files/SweetHome3D-source/SweetHome3D-7.6.1-src/SweetHome3D-7.6.1-src.zip/download`

## SweetHome3D format (7.6.1 source, paths under `com/eteks/sweethome3d/`)
- (a) `Home.xml` wins over `Home` in any entry order: `io/DefaultHomeInputStream.java:285-289`, app passes the XML handler (`SweetHome3D.java:200`, `io/HomeFileRecorder.java:330-331`) ⇒ `DROP_HOME_ENTRY = False`.
- (b) `Home` absent → `Home.xml` read alone; both absent → `IOException("Missing entry …")`: `io/DefaultHomeInputStream.java:293-294`.
- (c) No `ContentDigests` → digest check skipped (`contentDigests == null`): `io/HomeContentContext.java:112,154`.
- (d) Lengths cm (`model/LengthUnit.java:122,187` CENTIMETER identity), angles radians (`model/HomePieceOfFurniture.java:1362`), colours `%08X` ARGB (`io/XMLWriter.java:198`).
- (e) `CONTENT_ATTRS` = `model icon planIcon image`, + `property value` when `type="CONTENT"`: `io/HomeXMLHandler.java:798,820,1235-1237,1800`; URL first, else entry (`:1929`).
- (f) `io/HomeXMLHandler.java` `parse(Optional)?<T>(attributes…, "<name>")` calls: `NUMERIC_ATTRS` = Float|Decimal, `INT_ATTRS` = Integer|Long,
  `COLOR_ATTRS` = Color (`Long.parseLong(v, 16)`); `Float.parseFloat` accepts `NaN` (`:1916`).
- Windows opens a pushed home only via the realpath `make push` prints; a symlinked `homes/` path under `\\wsl$` fails to open (GUI-tested).
- DTD as shipped fails lxml: patch `renderer CADATA`→`CDATA`, `lightSourceMaterial name #REQUIRED`→`name CDATA #REQUIRED`, `endMarkSize CDATA "10";`→`"10"`.

## Upstream quirks
- `make upstream-test` skips `Testing/visuals/` (`cv2.imshow` aborts without a display) and pre-creates `Configs/` (`test_config` reads `../Configs`).
- Upstream code runs under `scripts/sandbox.py`: bwrap, no network, `--clearenv`, read-only allowlist binds (`/usr`, `/etc`, repo, uv Python, Blender dir) + named rw dirs — host `/run`, `/var`, `$HOME` stay unmounted.
- Blender runs with `--factory-startup` (`export.py` adds `--disable-autoexec`); in `convert.py` it replaces `-noaudio` so upstream's positional `argv[5:]` stay put.
- Runtime shims in `scripts/ftb_generate.py` (upstream stays unpatched): float points for `cv2.pointPolygonTest` (OpenCV 5), `np.int0` alias (NumPy 2).
- No doors/windows on Linux: the Blender script joins their data paths with `\\` (`floorplan_to_3dObject_in_blender.py:220-262`).
- `convert.py` links `Images/` into the scratch dir: door/window templates load CWD-relative (`const.DOOR_MODEL`).
- Previews render with Cycles CPU (16 samples, ~15 s/floor): Workbench segfaults headless (`EGL_BAD_MATCH`).
- Wall loss is in detection, not the Blender build (`make probe`): `wall_filter`'s 3×3 opening ×2 erases thin strokes, and
  only walls with a vertex inside the LARGEST gray<230 contour survive (fixture: that contour spans 69 % × 47 %).
- `sandbox.bwrap_cmd` mounts a tmpfs on `/tmp`, so `convert.py` cannot read a PNG under `/tmp` (e2e test works under `work/`).
- `export` never deletes: stale `*_UNSCALED*` files stay in `output/<floor>/` after calibrating.
- The preprocess wall mask (`wall_min_px`) assumes walls are thick strokes. On sheets that draw walls as thin double
  lines + hatching it keeps only solid wall runs, the sheet border and the title block; the outline frame then wraps
  the page, and `wall check OK` is vacuous (model and drawn extents share that frame). An `outline_cover` warning far
  below 0.97 is the tell — use a hand override `input/<floor>_walls.png` there.
