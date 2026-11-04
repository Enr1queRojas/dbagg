"""Build the bundle that reproduces the deployment on another machine.

    .\\.venv\\Scripts\\python.exe scripts\\package_deploy.py

The bundle holds the images, the Compose files, the guide and the private config/ (prompts and
model settings, which git ignores). It never holds .env or data/: credentials travel separately.
Treat it as confidential anyway, because the prompt describes the business.
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = "linux/amd64"
COMPOSE = ("docker", "compose", "--profile", "tunnel")
PUBLIC_FILES = ("docker-compose.yml", "Caddyfile", ".env.example")


def private_config(root):
    """Files under config/ that git ignores but the bot needs, without the prompt backups."""
    config = Path(root) / "config"
    files = [config / name for name in ("llm.toml", "business_context.json")]
    prompts = config / "prompts"
    if prompts.is_dir():
        files += [path for path in prompts.rglob("*") if path.parent.name != "backup"]
    return sorted(path for path in files if path.is_file())


def check_contents(names):
    """Last barrier: refuses the bundle if a credential or stored conversation got in."""
    for name in names:
        path = Path(name)
        secret = path.name == ".env" or (
            path.name.startswith(".env.") and path.name != ".env.example"
        )
        if secret or path.suffix == ".sqlite3" or "data" in path.parts:
            raise ValueError(f"El paquete no puede incluir {name}")


def run(*command):
    return subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True).stdout


def normalized(member):
    """Modes set here so an archive built on Windows is readable by the container on Linux."""
    member.mode = 0o755 if member.isdir() else 0o644
    member.uid = member.gid = 0
    member.uname = member.gname = "root"
    return member


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()

    revision = run("git", "describe", "--always", "--dirty").strip()
    if revision.endswith("-dirty"):
        print("Aviso: hay cambios sin commit; el paquete no corresponderá a un commit exacto.")
    name = f"dbagg-deploy-{revision}"
    work = args.output / name
    shutil.rmtree(work, ignore_errors=True)
    (work / "config" / "prompts").mkdir(parents=True)

    print("Construyendo imágenes…")
    run(*COMPOSE, "build", "dbagg")
    run(*COMPOSE, "pull", "caddy", "cloudflared")
    images = run(*COMPOSE, "config", "--images").split()
    print(f"Exportando {', '.join(images)}…")
    run("docker", "save", "--platform", PLATFORM, "-o", str(work / "images.tar"), *images)

    for file_name in PUBLIC_FILES:
        shutil.copy2(ROOT / file_name, work / file_name)
    shutil.copy2(ROOT / "docs" / "deploy-vm.md", work / "DEPLOY.md")
    for path in private_config(ROOT):
        target = work / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    (work / "REVISION").write_text(revision + "\n", encoding="utf-8", newline="\n")

    check_contents(path.relative_to(work) for path in work.rglob("*"))
    archive = args.output / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(work, arcname=name, filter=normalized)
    shutil.rmtree(work)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    # LF even on Windows: sha256sum -c on Linux would read a CR as part of the file name.
    (args.output / f"{archive.name}.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n"
    )
    print(f"Paquete: {archive} ({archive.stat().st_size / 1_000_000:.0f} MB)")
    print(f"SHA-256: {digest}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(f"Falló {' '.join(exc.cmd[:3])}: {exc.stderr.strip()}")
