"""Compatibility CLI: python score_riesgo.py --salida reportes/score_riesgo.html."""

from dbagg.reporting.cli import main
from dbagg.reporting.html import generar_html
from dbagg.reporting.risk import calcular_score_riesgo, semaforo, valor_tipico

__all__ = ["calcular_score_riesgo", "generar_html", "semaforo", "valor_tipico"]

if __name__ == "__main__":
    main()
