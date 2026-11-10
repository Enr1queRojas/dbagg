"""Render benchmark artifacts with matplotlib (optional reporting dependency)."""

import argparse
import html
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

COLORS = {"model": "#3268dd", "transcription": "#9b5de5", "prompt_build": "#718096",
          "business": "#16a085", "schema": "#d69e2e", "sql": "#db7c27", "render": "#16a085",
          "delivery": "#718096"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.input / "benchmark.json").read_text())
    events = [json.loads(line) for line in (args.input / "usage.jsonl").read_text().splitlines()]
    calls = [e for e in events if e["event"] == "usage"]
    mock = manifest["mode"] == "mock"
    badge = "SIMULACIÓN · conteos artificiales, no facturados" if mock else "API real · conteos del proveedor"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "svg.fonttype": "none"})
    labels = []
    for message in manifest["messages"]:
        selected = [e for e in calls if e["request_id"] == message["request_id"]]
        labels.extend(f'M{message["id"]} / llamada {i+1}' for i in range(len(selected)))
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), layout="constrained")
    fig.suptitle("DBAGG | Consumo por llamada\n" + badge, fontweight="bold")
    positions = list(range(len(calls)))
    for field, title, color in [("input_tokens", "Entrada (incluye caché)", "#3268dd"),
                                ("output_tokens", "Salida", "#16a085")]:
        heights = [e.get(field) or 0 for e in calls]
        bottoms = [e.get("input_tokens") or 0 for e in calls] if field == "output_tokens" else None
        axes[0].bar(positions, heights, bottom=bottoms, label=title, color=color)
    axes[0].set(xticks=positions, xticklabels=labels, ylabel="Tokens reportados", title="Entrada y salida")
    axes[0].tick_params(axis="x", rotation=45)
    axes[0].legend()
    axes[0].grid(axis="y", alpha=.15)
    bottom = [0] * len(calls)
    for role, color in [("system", "#3268dd"), ("user", "#16a085"),
                        ("assistant", "#9b5de5"), ("tool", "#db7c27")]:
        values = [e.get("message_chars_by_role", {}).get(role, 0) for e in calls]
        axes[1].bar(positions, values, bottom=bottom, color=color, label=role)
        bottom = [a+b for a, b in zip(bottom, values)]
    axes[1].set(xticks=positions, xticklabels=labels, ylabel="Caracteres (no tokens)",
                title="Contexto reenviado por rol · tamaño observado")
    axes[1].tick_params(axis="x", rotation=45)
    axes[1].legend()
    for i, event in enumerate(calls):
        if event.get("total_tokens") is None:
            axes[0].annotate("Sin usage", (i, 0), rotation=90)
    fig.savefig(args.output / "consumo.svg")
    fig.savefig(args.output / "consumo.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(len(manifest["messages"]), 1, figsize=(13, 10), layout="constrained", squeeze=False)
    fig.suptitle("DBAGG | Etapas de cada mensaje\n" + badge, fontweight="bold")
    for ax, message in zip(axes[:, 0], manifest["messages"]):
        spans = [e for e in events if e["request_id"] == message["request_id"]
                 and e["stage"] in COLORS]
        labels = []
        for i, event in enumerate(spans):
            ax.barh(i, max(event["duration_ms"], .005), left=event["start_ms"],
                    color=COLORS[event["stage"]])
            token_label = (f' · {event["total_tokens"]} tokens' if event.get("total_tokens") is not None
                           else " · usage desconocido" if event["event"] == "usage" else " · 0 tokens directos")
            labels.append(event["stage"] + token_label)
        ax.set(yticks=range(len(labels)), yticklabels=labels, xlabel="Milisegundos desde el inicio",
               title=f'M{message["id"]}: {message["question"]}')
        ax.invert_yaxis()
        ax.grid(axis="x", alpha=.15)
    fig.savefig(args.output / "etapas.svg")
    fig.savefig(args.output / "etapas.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(12, 11))
    ax.set(xlim=(0, 13), ylim=(0, 12))
    ax.axis("off")
    fig.suptitle("DBAGG | Recorrido del mensaje", fontsize=18, fontweight="bold")
    nodes = {
        "entry": (6, 11, "Webhook WhatsApp\nFirma, autorización, deduplicación · 0 tokens", "#e7edf5"),
        "voice": (2.6, 9.5, "Si llega audio: transcripción API\nusage / tokens de audio / segundos", "#eadeff"),
        "control": (9.2, 9.5, "Si es comando local\nRespuesta directa · 0 tokens", "#e7edf5"),
        "prompt": (6, 8, "Preparar contexto + historial + herramientas\n0 tokens directos; se facturan al enviarse", "#e7edf5"),
        "model": (6, 6.5, "API del modelo · gpt-4.1-mini\nEntrada + salida + caché · medido por llamada", "#dce8ff"),
        "business": (2.5, 4.7, "consultar_negocio / pedir_aclaracion\nSQL validado + respuesta en código\n0 tokens directos", "#daf3ea"),
        "sql": (9, 4.7, "describir_tablas / consultar_sql\nEsquema, datos o error → siguiente ronda\n0 tokens directos", "#fff0d9"),
        "render": (6, 2.7, "Renderizar respuesta / PDF en código\n0 tokens directos", "#daf3ea"),
        "delivery": (6, 1, "Enviar respuesta + guardar memoria\n0 tokens directos · historial en siguiente consulta", "#e7edf5"),
    }
    for x, y, label, color in nodes.values():
        ax.add_patch(FancyBboxPatch((x-2.2, y-.45), 4.4, .9,
                                   boxstyle="round,pad=.12", facecolor=color, edgecolor="#aab8cc"))
        ax.text(x, y, label, ha="center", va="center", fontsize=9)
    for source, target in [("entry", "voice"), ("entry", "control"), ("entry", "prompt"),
                           ("voice", "prompt"), ("prompt", "model"), ("model", "business"),
                           ("model", "sql"), ("business", "render"), ("sql", "render"),
                           ("render", "delivery")]:
        x, y, *_ = nodes[source]
        tx, ty, *_ = nodes[target]
        ax.annotate("", xy=(tx, ty+.58), xytext=(x, y-.58),
                    arrowprops={"arrowstyle": "->", "color": "#697b94",
                                "connectionstyle": "arc3,rad=-.35" if source == "control" else "arc3,rad=0"})
    ax.plot([11.55, 12.5, 12.5, 8.9], [9.5, 9.5, 1, 1], color="#697b94", linewidth=1)
    ax.annotate("", xy=(8.35, 1), xytext=(8.9, 1),
                arrowprops={"arrowstyle": "->", "color": "#697b94"})
    ax.annotate("", xy=(8.1, 6.5), xytext=(10.8, 5.3),
                arrowprops={"arrowstyle": "->", "color": "#db7c27", "connectionstyle": "arc3,rad=.55"})
    ax.text(10.8, 6.35, "Repite contexto\nhasta 9 rondas", ha="center", color="#a45d12", fontsize=9)
    ax.text(6, 5.8, "Sin herramienta al inicio: una llamada extra", ha="center", fontsize=8, color="#697b94")
    fig.savefig(args.output / "flujo.svg", bbox_inches="tight")
    fig.savefig(args.output / "flujo.png", bbox_inches="tight", dpi=150)
    plt.close(fig)
    images = "\n".join((args.output / name).read_text().split("?>", 1)[-1]
                       for name in ("consumo.svg", "etapas.svg", "flujo.svg"))
    report = ("<!doctype html><html lang='es'><meta charset='utf-8'><title>DBAGG · Usage</title>"
              "<style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:20px;color:#17304a}"
              "svg{width:100%;height:auto} .note{background:#fff0d9;padding:20px;border-radius:12px}</style>"
              f"<h1>DBAGG · Medición de consumo</h1><p class='note'>{html.escape(badge)}. "
              "Las pruebas reales requieren OPENAI_API_KEY y acceso a api.openai.com. "
              "Caché y razonamiento son subconjuntos, no tokens adicionales al total. "
              "Los tamaños por rol son caracteres; el proveedor no desglosa tokens por sección.</p>" + images)
    (args.output / "reporte.html").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
