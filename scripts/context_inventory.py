"""Reproducible character inventory; this does not estimate billed tokens or cost."""

import json
from collections import Counter

from dbagg.agent.context_builder import select_context
from dbagg.agent.prompt_files import load_prompt_bundle
from dbagg.agent.prompts import tool_definitions
from dbagg.context.loader import load_business_context
from dbagg.database.catalog import load_catalog
from dbagg.routing.registry import available_routes


def inventory():
    catalog = load_catalog()
    tables = frozenset(catalog.sources)
    bundle = load_prompt_bundle()
    context = load_business_context()
    baseline = {
        "system": len(bundle.system),
        "business": len(context),
        "schema": len(json.dumps(catalog.cards(tables), ensure_ascii=False)),
        "tools": len(json.dumps(tool_definitions(bundle, banks=True), ensure_ascii=False)),
    }
    baseline["total"] = sum(baseline.values())
    sections = []
    for block in bundle.system.split("\n# "):
        title = block.splitlines()[0].lstrip("# ")
        sections.append({"section": title, "characters": len(block)})
    lines = Counter(line.strip() for line in bundle.system.splitlines() if len(line.strip()) >= 60)
    routes = []
    for route in available_routes(catalog, tables).values():
        selected = select_context(route, context, bundle, catalog, tables)
        sizes = {
            "system": len(selected.bundle.system),
            "business": len(selected.context),
            "schema": len(selected.schema),
            "tools": len(json.dumps(selected.tools, ensure_ascii=False)),
        }
        sizes["total"] = sum(sizes.values())
        routes.append(
            {
                "route": route.name,
                "version": selected.bundle.version,
                "modules": selected.modules,
                "topics": route.topics,
                "tables": sorted(route.tables),
                "characters": sizes,
                "reduction_percent": round((1 - sizes["total"] / baseline["total"]) * 100, 2),
            }
        )
    return {
        "measurement": "characters, packaged context, full catalog; not billed tokens",
        "baseline": baseline,
        "sections": sections,
        "exact_duplicate_lines": [{"line": line, "count": n} for line, n in lines.items() if n > 1],
        "routes": routes,
    }


if __name__ == "__main__":
    print(json.dumps(inventory(), ensure_ascii=False, indent=2))
