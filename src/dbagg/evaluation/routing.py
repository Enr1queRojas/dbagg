"""Score the real model's first routing decision on synthetic cases; never queries the database."""

import argparse
import json
import sys
from datetime import date

from dbagg.agent.intent import requests_candidate_comparison
from dbagg.agent.prompt_files import load_prompt_bundle
from dbagg.agent.prompts import build_messages, tool_definitions
from dbagg.agent.service import Assistant, _normalize, code_confirmed
from dbagg.config import Settings
from dbagg.context.loader import load_business_context
from dbagg.paths import project_root

# Sending one of these is the same request as omitting it, unless the case expects another value.
DEFAULTS = {"period": "todo", "limit": 5, "codigo_exacto": False, "comparar": "periodo_anterior"}
NAME_ARGUMENTS = {"customer", "producto"}


def _canonical(arguments):
    canonical = {}
    for key, value in arguments.items():
        if value in (None, "") or (key in DEFAULTS and value == DEFAULTS[key]):
            continue
        canonical[key] = _normalize(value) if key in NAME_ARGUMENTS else value
    return canonical


def matches(case, tool, arguments):
    return tool == case["expected_tool"] and _canonical(arguments) == _canonical(
        case.get("expected_arguments", {})
    )


def observe(assistant, case, today):
    """Return the first tool call, the decision the service would act on."""
    question = case["question"]
    if requests_candidate_comparison(question):
        return "pedir_aclaracion", {"reason": "comparacion"}
    bundle = load_prompt_bundle(assistant.llm_config)
    schema = json.dumps(assistant.catalog.cards(assistant.tables), ensure_ascii=False)
    messages = build_messages(
        schema, load_business_context(), question, case.get("history"), today=today, bundle=bundle
    )
    tools = tool_definitions(bundle)
    calls = assistant.provider.complete(messages, tools).tool_calls
    if not calls:
        # The service asks once more before giving up; that second reply is what it acts on.
        calls = assistant.provider.complete(messages, tools, require_tool=True).tool_calls
    if not calls:
        return None, {}
    arguments = json.loads(calls[0].arguments)
    if "codigo_exacto" in arguments:
        # The service drops an unconfirmed flag, so it does not change what would be executed.
        arguments["codigo_exacto"] = arguments["codigo_exacto"] is True and code_confirmed(
            arguments.get("customer"), question, case.get("history")
        )
    return calls[0].name, arguments


def run(assistant, dataset, repeat=1, only=None):
    today = date.fromisoformat(dataset["business_today"])
    results = []
    for case in dataset["cases"]:
        if only and case["id"] not in only:
            continue
        result = {"id": case["id"], "runs": repeat, "passed": 0, "observed": [], "errors": []}
        for _ in range(repeat):
            try:
                tool, arguments = observe(assistant, case, today)
                passed = matches(case, tool, arguments)
            except Exception as exc:
                # One failed call must not lose the batch; provider messages may echo private data.
                result["errors"].append(type(exc).__name__)
                continue
            if passed:
                result["passed"] += 1
            elif [tool, arguments] not in result["observed"]:
                result["observed"].append([tool, arguments])
        results.append(result)
    return results


class _NoDatabase:
    def __getattr__(self, name):
        raise RuntimeError("La evaluación de enrutamiento no consulta la base.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evalúa la elección de herramienta del modelo configurado; consume su API."
    )
    parser.add_argument(
        "--dataset", default=project_root() / "evals" / "cases" / "intent-routing.json"
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--only", nargs="*", help="IDs de los casos a ejecutar.")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    with open(args.dataset, encoding="utf-8") as stream:
        dataset = json.load(stream)
    assistant = Assistant(Settings.from_env(), db=_NoDatabase())
    profile = assistant.llm_config.profile
    print(f"Modelo: {profile.model} | prompt: {load_prompt_bundle(assistant.llm_config).version}")
    results = run(assistant, dataset, args.repeat, set(args.only or ()))
    assistant.close()
    for result in results:
        print(f"{result['passed']}/{result['runs']} {result['id']}")
        for tool, arguments in result["observed"]:
            print(f"    obtuvo: {tool} {json.dumps(arguments, ensure_ascii=False)}")
        for error in result["errors"]:
            print(f"    error: {error}")
    exact = sum(r["passed"] == r["runs"] for r in results)
    print(f"Casos correctos en todas sus ejecuciones: {exact}/{len(results)}")
    return 0 if exact == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
