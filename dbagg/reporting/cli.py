"""Report command-line entry point."""

from datetime import date
from dbagg.paths import project_root
from dbagg.reporting.risk import calcular_score_riesgo
from dbagg.reporting.html import generar_html


def main():
    import argparse
    import os
    import webbrowser
    from pathlib import Path
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Calcula el riesgo y genera un reporte HTML.")
    parser.add_argument("--salida", type=Path, help="Ruta del HTML de salida")
    parser.add_argument("--abrir", action="store_true", help="Abre el reporte en el navegador")
    args = parser.parse_args()
    load_dotenv(project_root() / ".env")
    # A full string also supports Windows authentication/custom ODBC drivers.
    conn_str = os.getenv("DB_CONNECTION_STRING")
    if not conn_str:
        required = ("DB_SERVER", "DB_NAME", "DB_USER", "DB_PASSWORD")
        missing = [key for key in required if not os.getenv(key)]
        if missing:
            parser.error("Faltan variables en .env: " + ", ".join(missing))

        def odbc_value(value):
            return "{" + value.replace("}", "}}") + "}"

        conn_str = (
            "DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER={odbc_value(os.environ['DB_SERVER'])};"
            f"DATABASE={odbc_value(os.environ['DB_NAME'])};"
            f"UID={odbc_value(os.environ['DB_USER'])};"
            f"PWD={odbc_value(os.environ['DB_PASSWORD'])};"
            "Encrypt=yes;TrustServerCertificate=yes;ApplicationIntent=ReadOnly;"
        )
    hoy = date.today()
    resultado = calcular_score_riesgo(conn_str, hoy=hoy)
    salida = generar_html(resultado, args.salida, hoy=hoy)
    print(f"Reporte generado: {salida} ({len(resultado)} clientes)")
    if args.abrir:
        webbrowser.open(salida.as_uri())


if __name__ == "__main__":
    main()
