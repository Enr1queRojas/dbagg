"""Render a standalone credit-risk report."""

from datetime import date
from dbagg.paths import project_root
from dbagg.reporting.risk import VENTANA_DIAS


def generar_html(resultado, salida=None, hoy=None):
    """Render the report directly from calculated rows; no intermediate files."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent
    destino = Path(salida) if salida else project_root() / "reportes" / "score_riesgo.html"
    hoy = hoy or date.today()
    campos = {
        "cliente": "c",
        "D": "D",
        "n_pagos": "n",
        "fuente": "f",
        "segmento": "segmento",
        "P_tipico": "P",
        "d_tipico": "d",
        "t": "t",
        "C": "C",
        "R": "R",
        "score": "score",
        "dias_liquidar": "dias",
        "semaforo": "semaforo_score",
    }
    if "motivo" in resultado.columns:
        campos["motivo"] = "motivo"
    # pandas converts missing/non-finite numbers to JSON null, preserving precision.
    rows = json.loads(
        resultado[list(campos)]
        .rename(columns=campos)
        .to_json(orient="records", force_ascii=False, double_precision=15)
    )
    payload = json.dumps(
        {"fecha_corte": hoy.isoformat(), "rows": rows}, ensure_ascii=False, allow_nan=False
    )
    # Prevent data from closing a script element or introducing HTML.
    payload = (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    template = (root / "templates" / "riesgo.html").read_text(encoding="utf-8")
    helpers = (root / "static" / "risk-data.js").read_text(encoding="utf-8")
    html = (
        template.replace("__REPORT_DATE__", f"Corte al {hoy:%d/%m/%Y}")
        .replace("__WINDOW_DAYS__", str(VENTANA_DIAS))
        .replace("__REPORT_HELPERS__", helpers)
        .replace("__REPORT_JSON__", payload)
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(html, encoding="utf-8")
    return destino.resolve()
