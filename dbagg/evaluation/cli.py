"""Inspect local ratings and export only explicitly reviewed examples."""

import argparse
import json
from pathlib import Path

from dbagg.evaluation.store import FeedbackStore
from dbagg.paths import project_root


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Revisión humana del agente; no entrena ni llama APIs."
    )
    parser.add_argument("--db", type=Path, default=project_root() / "data" / "feedback.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("summary")
    commands.add_parser("pending")
    show = commands.add_parser(
        "show", help="Muestra localmente una evaluación; puede contener datos privados."
    )
    show.add_argument("id")
    review = commands.add_parser("review")
    review.add_argument("id")
    review.add_argument("--decision", choices=["approved", "rejected"], required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument(
        "--case", type=Path, help="JSON corregido/anonimizado y verificado; obligatorio al aprobar."
    )
    export = commands.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    store = FeedbackStore(args.db)
    try:
        if args.command == "summary":
            result = store.summary()
        elif args.command == "pending":
            result = store.list_pending()
        elif args.command == "show":
            result = store.get(args.id)
        elif args.command == "review":
            case = json.loads(args.case.read_text(encoding="utf-8-sig")) if args.case else None
            store.review(args.id, args.decision, args.reviewer, case)
            result = {"id": args.id, "review_status": args.decision}
        else:
            result = {"exported": store.export(args.output)}
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Error de evaluación: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
