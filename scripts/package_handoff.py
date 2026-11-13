"""Create a local public-source archive with SHA-256 integrity records."""
from pathlib import Path
import hashlib
import json
import os
import zipfile

root = Path(__file__).resolve().parents[1]
dist = root / "dist"
dist.mkdir(exist_ok=True)
selected = ["README.md", "Open AIP Demo.cmd", ".gitignore", ".gitattributes", "requirements.lock", "config.example.json",
            ".github", "src", "scripts", "tests", "schemas", "prompts", "data", "docs", "reports",
            "output/aip_four_component"]
files = []
for entry in selected:
    path = root / entry
    if not path.exists():
        continue
    if path.is_file():
        files.append(path)
    else:
        for directory, dirs, names in os.walk(path, followlinks=False):
            dirs[:] = [d for d in dirs if d not in {"node_modules", "__pycache__", ".venv"}
                       and not (Path(directory)/d).is_symlink() and not (Path(directory)/d).is_junction()]
            files.extend(Path(directory)/n for n in names if not n.endswith((".pyc", ".pyo")))

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

files = sorted(set(files))
manifest = {"version": "1.0.0", "scope": "Public prototype source and synthetic reference assets; not an inference packet",
            "files": [{"path": p.relative_to(root).as_posix(), "sha256": sha(p), "bytes": p.stat().st_size} for p in files]}
manifest_path = dist / "handoff_manifest.json"
manifest_path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
target = dist / "aip_software_handoff.zip"
with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for p in files:
        z.write(p,"aip_software/"+p.relative_to(root).as_posix())
    z.write(manifest_path,"aip_software/handoff_manifest.json")
with zipfile.ZipFile(target) as z:
    assert z.testzip() is None
    for record in manifest["files"]:
        assert hashlib.sha256(z.read("aip_software/"+record["path"])).hexdigest()==record["sha256"]
target.with_suffix(".zip.sha256").write_text(sha(target)+"  "+target.name+"\n",encoding="ascii")
print(f"Verified {len(files)} source files: {target.name}")
