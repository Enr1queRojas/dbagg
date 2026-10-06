"""
Score de riesgo de crédito por cliente.

Score = C x R
  C = D / P~               pagos típicos que representa la deuda actual
  R = max(1, t / d~)       factor de retraso (solo penaliza)

  D  = saldo actual (vw_AlertasCobranza)
  P~ = pago típico   (promedio sin atípicos IQR si hay >= 8 datos, si no mediana)
  d~ = intervalo típico entre pagos (misma regla)
  t  = días desde el último pago (o desde el primer cargo si nunca ha pagado)

Clientes con menos de 4 días de pago en la ventana usan P~ y d~ medianos
de su segmento (quintil de deuda).

Solo ejecuta SELECT: compatible con un usuario de solo lectura.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pyodbc

# ============ PARÁMETROS ============
VENTANA_DIAS = 365
MIN_PAGOS = 4            # menos días de pago -> usar segmento
MIN_PAGOS_IQR = 8        # menos datos -> mediana; si no, promedio filtrado por IQR
K_IQR = 1.5
N_SEGMENTOS = 5          # quintiles de deuda
UMBRAL_AMARILLO = 3.0    # provisional: calibrar con clientes que cayeron en impago
UMBRAL_ROJO = 6.0
EXCLUIR = {"AAVARIOS"}   # claves que no son clientes reales

# ============ NOMBRES DE COLUMNAS ============
# Reemplaza con los nombres reales (la posición es la que se vio en las muestras).
COB = {
    "tabla":   "proadel.COBRANZA_DATA",
    "cliente": "[CLIENTE]",
    "monto":   "[IMPORTE]",
    "estatus": "[ESTADO]",
    "fecha":   "[NOTE_DATE]",
}
CRE = {
    "tabla":   "proadel.CREDITO_DATA",
    "cliente": "[CLIENTE]",
    "estatus": "[ESTADO]",
    "fecha":   "[NOTE_DATE]",
}
ALERTAS = {
    "vista":       "proadel.vw_AlertasCobranza",
    "idx_cliente": 0,   # CLIENTE
    "idx_saldo":   5,   # SALDO_PENDIENTE
}

# ============ CONSULTAS ============
SQL_PAGOS = f"""
WITH pagos_diarios AS (
    SELECT {COB['cliente']}               AS cliente,
           CAST({COB['fecha']} AS date)   AS fecha,
           SUM({COB['monto']})            AS monto
    FROM {COB['tabla']}
    WHERE {COB['estatus']} = 'ACTIVA'
      AND {COB['monto']} > 0
    GROUP BY {COB['cliente']}, CAST({COB['fecha']} AS date)
),
intervalos AS (
    SELECT cliente, fecha, monto,
           DATEDIFF(DAY,
                    LAG(fecha) OVER (PARTITION BY cliente ORDER BY fecha),
                    fecha) AS intervalo
    FROM pagos_diarios
)
SELECT cliente, fecha, monto, intervalo
FROM intervalos
WHERE fecha >= ?
"""

SQL_ULTIMO_PAGO = f"""
SELECT {COB['cliente']} AS cliente, MAX(CAST({COB['fecha']} AS date)) AS ultimo_pago
FROM {COB['tabla']}
WHERE {COB['estatus']} = 'ACTIVA' AND {COB['monto']} > 0
GROUP BY {COB['cliente']}
"""

SQL_PRIMER_CARGO = f"""
SELECT {CRE['cliente']} AS cliente, MIN(CAST({CRE['fecha']} AS date)) AS primer_cargo
FROM {CRE['tabla']}
WHERE {CRE['estatus']} = 'ACTIVA'
GROUP BY {CRE['cliente']}
"""

SQL_SALDOS = f"SELECT * FROM {ALERTAS['vista']}"


# ============ UTILIDADES ============
def _leer(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    df = pd.DataFrame.from_records(cur.fetchall(), columns=cols)
    cur.close()
    return df


def _leer_saldos(conn):
    cur = conn.cursor()
    cur.execute(SQL_SALDOS)
    i_cli, i_sal = ALERTAS["idx_cliente"], ALERTAS["idx_saldo"]
    datos = [(r[i_cli], r[i_sal]) for r in cur.fetchall()]
    cur.close()
    return pd.DataFrame(datos, columns=["cliente", "D"])


def _limpiar_clave(serie):
    return serie.astype(str).str.strip()


def valor_tipico(valores):
    """>= MIN_PAGOS_IQR datos: promedio sin atípicos (IQR). Menos: mediana."""
    v = np.asarray(valores, dtype=float)
    v = v[~np.isnan(v)]
    if v.size == 0:
        return np.nan
    if v.size >= MIN_PAGOS_IQR:
        q1, q3 = np.percentile(v, [25, 75])
        iqr = q3 - q1
        limpios = v[(v >= q1 - K_IQR * iqr) & (v <= q3 + K_IQR * iqr)]
        return float(limpios.mean()) if limpios.size else float(np.median(v))
    return float(np.median(v))


def semaforo(score):
    if score > UMBRAL_ROJO:
        return "rojo"
    if score > UMBRAL_AMARILLO:
        return "amarillo"
    return "verde"


# ============ FUNCIÓN PRINCIPAL ============
def calcular_score_riesgo(conn_str, hoy=None):
    hoy = hoy or date.today()
    inicio = hoy - timedelta(days=VENTANA_DIAS)

    # --- 1. Extracción (solo lectura) ---
    conn = pyodbc.connect(conn_str, readonly=True)
    try:
        pagos = _leer(conn, SQL_PAGOS, (inicio,))
        ultimo = _leer(conn, SQL_ULTIMO_PAGO)
        primer = _leer(conn, SQL_PRIMER_CARGO)
        saldos = _leer_saldos(conn)
    finally:
        conn.close()

    for df in (pagos, ultimo, primer, saldos):
        df["cliente"] = _limpiar_clave(df["cliente"])
    pagos["monto"] = pagos["monto"].astype(float)
    saldos["D"] = saldos["D"].astype(float)

    # --- 2. Limpieza e indicadores propios ---
    stats = (
        pagos.groupby("cliente")
        .agg(
            n_pagos=("monto", "size"),
            P_propio=("monto", valor_tipico),
            d_propio=("intervalo", valor_tipico),
        )
        .reset_index()
    )

    base = (
        saldos[~saldos["cliente"].isin(EXCLUIR)]
        .merge(stats, on="cliente", how="left")
        .merge(ultimo, on="cliente", how="left")
        .merge(primer, on="cliente", how="left")
    )
    base["n_pagos"] = base["n_pagos"].fillna(0).astype(int)
    base["historial"] = base["n_pagos"] >= MIN_PAGOS

    # --- 3. Segmentos por monto de deuda ---
    con_deuda = base["D"] > 0
    base["segmento"] = np.nan
    base.loc[con_deuda, "segmento"] = pd.qcut(
        base.loc[con_deuda, "D"], q=N_SEGMENTOS, labels=False, duplicates="drop"
    )
    ref = base[base["historial"] & con_deuda]
    seg = ref.groupby("segmento").agg(
        P_seg=("P_propio", "median"), d_seg=("d_propio", "median")
    )
    base = base.merge(seg, left_on="segmento", right_index=True, how="left")
    base["P_seg"] = base["P_seg"].fillna(ref["P_propio"].median())
    base["d_seg"] = base["d_seg"].fillna(ref["d_propio"].median())

    usa_propio = base["historial"] & base["d_propio"].notna()
    base["fuente"] = np.where(usa_propio, "propio", "segmento")
    base["P_tipico"] = np.where(usa_propio, base["P_propio"], base["P_seg"])
    base["d_tipico"] = np.where(usa_propio, base["d_propio"], base["d_seg"])

    # --- 4. Días sin pagar ---
    hoy_ts = pd.Timestamp(hoy)
    ref_fecha = pd.to_datetime(base["ultimo_pago"]).fillna(
        pd.to_datetime(base["primer_cargo"])
    )
    base["t"] = (hoy_ts - ref_fecha).dt.days

    # --- 5. Fórmula final ---
    P = base["P_tipico"].clip(lower=0.01)
    d = base["d_tipico"].clip(lower=1)
    base["C"] = base["D"].clip(lower=0) / P
    base["R"] = np.maximum(1.0, (base["t"] / d).fillna(1.0))
    base["score"] = base["C"] * base["R"]
    base.loc[~con_deuda, ["C", "score"]] = 0.0
    base.loc[~con_deuda, "R"] = 1.0
    base["dias_liquidar"] = base["score"] * d
    base["semaforo"] = base["score"].apply(semaforo)

    cols = [
        "cliente", "D", "n_pagos", "fuente", "segmento",
        "P_tipico", "d_tipico", "t", "C", "R", "score",
        "dias_liquidar", "semaforo",
    ]
    return base[cols].sort_values("score", ascending=False).reset_index(drop=True)



if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()

    server = os.getenv('DB_SERVER')
    database = os.getenv('DB_NAME')
    username = os.getenv('DB_USER')
    password = os.getenv('DB_PASSWORD')

    CONN_STR = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};DATABASE={database};"
        f"UID={username};PWD={password};"
        "Encrypt=yes;TrustServerCertificate=yes;ApplicationIntent=ReadOnly;"
    )

    resultado = calcular_score_riesgo(CONN_STR)
    pd.set_option("display.width", 200)
    print(resultado.head(30).round(2))
    resultado.to_csv("score_riesgo.csv", index=False, encoding="utf-8-sig")