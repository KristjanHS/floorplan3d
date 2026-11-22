---
paths:
  - "sh3d_src/**"
last_verified: 2026-11-22
---
# SweetHome3D model entries (`sh3d_src/<home>/<n>`, OBJ text)

- An entry is shared by every piece with `model='<n>'` in Home.xml: editing it changes all of them; `push` refuses new entries.
- Keep CRLF line ends AND the final CRLF: SH3D's OBJLoader face loop needs an EOL (`OBJLoader.java:1389`), else red cross.
- Dropping parts: drop the group's `g`/`usemtl`/`s`/`f` lines, then keep only used `v` lines and renumber `f` indices (1-based).
- SH3D scales the model's bbox to the piece's width/depth/height: removing parts that set the bbox (e.g. a handrail) changes what `height` means.
- Falsifier before `make push`: `python3 -I -c "import sys;b=open(sys.argv[1],'rb').read();l=b.split(b'\r\n');n=sum(x[:2]==b'v ' for x in l);assert l[-1]==b'' and all(1<=int(t)<=n for x in l if x[:2]==b'f ' for t in x.split()[1:])" sh3d_src/<home>/<n>`
