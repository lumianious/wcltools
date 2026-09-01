"""Build the native-platform executable bundle and a portable ZIP; no publishing."""

import hashlib
import importlib.metadata
import platform
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--name", "wcltools", "--paths", str(ROOT),
            "--add-data", f"{ROOT / 'wcltools' / 'data'}:wcltools/data",
            "--add-data", f"{ROOT / 'wcltools' / 'skill'}:wcltools/skill",
            "--copy-metadata", "keyring"]
    if sys.platform == "win32":
        args += ["--hidden-import", "keyring.backends.Windows"]
    args.append(str(ROOT / "wcltools" / "__main__.py"))
    subprocess.run(args, cwd=ROOT, check=True)
    for name in ("README.md", "LICENSE", "NOTICE"):
        shutil.copy2(ROOT / name, ROOT / "dist" / "wcltools" / name)
    # Keep the build environment's notices, including vendored dependencies.
    # Collecting these explicitly avoids relying on PyInstaller's data hooks.
    notices = ROOT / "dist" / "wcltools" / "third-party-licenses"
    notices.mkdir(exist_ok=True)
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", notices / "Python-LICENSE.txt")
    for distribution in importlib.metadata.distributions():
        for entry in distribution.files or []:
            if not entry.name.lower().startswith(("license", "copying", "notice")):
                continue
            source = Path(distribution.locate_file(entry))
            if source.is_file():
                destination = notices / distribution.metadata["Name"] / str(entry).replace("/", "__")
                destination.parent.mkdir(exist_ok=True)
                shutil.copy2(source, destination)
    sys.path.insert(0, str(ROOT))
    from wcltools import __version__
    name = f"wcltools-{__version__}-{platform.system().lower()}-{platform.machine().lower()}"
    archive = Path(shutil.make_archive(str(ROOT / "dist" / name), "zip", ROOT / "dist", "wcltools"))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(archive)


if __name__ == "__main__":
    main()
