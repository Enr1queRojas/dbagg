"""Inspect local ratings and export only explicitly reviewed examples."""

import argparse
import json
from pathlib import Path

from dbagg.evaluation.gaps import CATEGORIES, DECISIONS, GapStore
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
    parser.add_argument("--gaps-db", type=Path, default=project_root() / "data" / "gaps.sqlite3")
    commands.add_parser("gaps-summary", help="Conteos de preguntas sin respuesta; sin contenido.")
    gaps_list = commands.add_parser(
        "gaps-list", help="Preguntas pendientes agrupadas por frecuencia; contiene datos privados."
    )
    gaps_list.add_argument("--category", choices=sorted(CATEGORIES))
    gaps_list.add_argument("--limit", type=int, default=50)
    gaps_review = commands.add_parser(
        "gaps-review", help="Revisa un grupo de preguntas iguales con el mismo ID de ejemplo."
    )
    gaps_review.add_argument("id")
    gaps_review.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    gaps_review.add_argument("--reviewer", required=True)
    gaps_review.add_argument("--note", default="")
    gaps_review.add_argument(
        "--case",
        type=Path,
        help="Caso sintético (formato intent-routing); obligatorio en nuevo_caso.",
    )
    gaps_export = commands.add_parser("gaps-export")
    gaps_export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    store = FeedbackStore(args.db)
    gaps = GapStore(args.gaps_db)
    try:
        if args.command == "gaps-summary":
            result = gaps.summary()
        elif args.command == "gaps-list":
            result = gaps.grouped(args.category, max(1, min(args.limit, 500)))
        elif args.command == "gaps-review":
            case = json.loads(args.case.read_text(encoding="utf-8-sig")) if args.case else None
            updated = gaps.review(args.id, args.decision, args.reviewer, args.note, case)
            result = {"id": args.id, "review_status": args.decision, "rows": updated}
        elif args.command == "gaps-export":
            result = {"exported": gaps.export(args.output)}
        elif args.command == "summary":
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
