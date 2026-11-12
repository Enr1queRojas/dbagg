"""Regenerate agent/templates/system.md from its modules; --check fails if it differs."""

import argparse
import sys

from dbagg.agent.prompt_files import TEMPLATES, compose_system_prompt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Solo comprobar, sin escribir")
    args = parser.parse_args(argv)
    target = TEMPLATES / "system.md"
    expected = compose_system_prompt()
    current = target.read_text(encoding="utf-8") if target.exists() else ""
    if args.check:
        if current != expected:
            print("system.md difiere de sus módulos; ejecuta scripts/build_system_prompt.py")
            return 1
        return 0
    target.write_text(expected, encoding="utf-8")
    print(f"system.md regenerado: {len(expected)} caracteres")
    return 0


if __name__ == "__main__":
    sys.exit(main())
