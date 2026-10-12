"""Preview regenerable caches and empty legacy folders; delete only with --apply."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess


CACHE_NAMES = {"__pycache__", ".ruff_cache", ".pytest_cache"}


def is_link(path):
    # Reparse points include junctions on Windows, also on Python 3.11.
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def tracked_files(root):
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)
    return {Path(os.fsdecode(name)) for name in result.stdout.split(b"\0") if name}


def contents(path):
    """Never traverse symlinks or Windows junctions, even inside a cache."""
    pending = [path]
    entries = []
    while pending:
        entry = pending.pop()
        if is_link(entry):
            return None
        entries.append(entry)
        if entry.is_dir():
            pending.extend(entry.iterdir())
        elif not entry.is_file():
            return None
    return entries


def eligible(path, root, tracked):
    entries = contents(path)
    if entries is None:
        return False
    if any(entry.relative_to(root) in tracked for entry in entries):
        return False
    files = [entry for entry in entries if entry.is_file()]
    if path.name in {"dbagg", "__pycache__"}:
        return all(f.parent.name == "__pycache__" and f.suffix in {".pyc", ".pyo"} for f in files)
    if path.name == "core":
        return path.is_dir() and not any(path.iterdir())
    return path.is_dir() and path.name in CACHE_NAMES


def candidates(root):
    # Only these code directories are scanned; .env, config, data and venv are outside scope.
    paths = [root / name for name in sorted(CACHE_NAMES | {"dbagg", "core"})]
    for name in ("src", "tests", "scripts"):
        scope = root / name
        if not scope.exists() or is_link(scope):
            continue
        for directory, folders, _ in os.walk(scope, followlinks=False):
            for folder in list(folders):
                path = Path(directory) / folder
                if is_link(path):
                    folders.remove(folder)
                elif folder in CACHE_NAMES:
                    paths.append(path)
                    folders.remove(folder)
    return sorted(p for p in paths if p.exists() or p.is_symlink())


def clean(root, apply=False):
    tracked = tracked_files(root)  # Fail closed when Git cannot establish what is versioned.
    removed = []
    for path in candidates(root):
        if not eligible(path, root, tracked):
            print(f"Conservado (contenido no regenerable o enlace): {path.relative_to(root)}")
            continue
        if apply:
            shutil.rmtree(path)
        removed.append(path.relative_to(root))
        print(f"{'Eliminado' if apply else 'Candidato'}: {path.relative_to(root)}")
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Aplicar la limpieza acotada.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not (root / "pyproject.toml").is_file():
        parser.error("Ejecuta el script desde un checkout de dbagg.")
    clean(root, apply=args.apply)
    if not args.apply:
        print("Vista previa: no se borró nada. Usa --apply para limpiar; detén el backend antes.")


if __name__ == "__main__":
    main()
