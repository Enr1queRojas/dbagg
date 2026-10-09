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

# ============ PARÁMETROS ============
VENTANA_DIAS = 365
MIN_PAGOS = 4  # menos días de pago -> usar segmento
MIN_PAGOS_IQR = 8  # menos datos -> mediana; si no, promedio filtrado por IQR
K_IQR = 1.5
N_SEGMENTOS = 5  # quintiles de deuda
UMBRAL_AMARILLO = 3.0  # provisional: calibrar con clientes que cayeron en impago
UMBRAL_ROJO = 6.0
EXCLUIR = {"AAVARIOS"}  # claves que no son clientes reales

# ============ NOMBRES DE COLUMNAS ============
# Reemplaza con los nombres reales (la posición es la que se vio en las muestras).
COB = {
    "tabla": "proadel.COBRANZA_DATA",
    "cliente": "[CLIENTE]",
    "monto": "[IMPORTE]",
    "estatus": "[ESTADO]",
    "fecha": "[FECHA]",
}
CRE = {
    "tabla": "proadel.CREDITO_DATA",
    "cliente": "[CLIENTE]",
    "estatus": "[ESTADO]",
    "fecha": "[NOTE_DATE]",
}
ALERTAS = {
    "vista": "proadel.vw_AlertasCobranza",
    "idx_cliente": 0,  # CLIENTE
    "idx_saldo": 5,  # SALDO_PENDIENTE
}


# ============ CONSULTAS ============
def _fecha_sql(column):
    # SQL Server can cast an empty string to 1900-01-01. Normalize blanks to NULL
    # first, and use style 103 for both native dates and dd/MM/yyyy text columns.
    return f"TRY_CONVERT(date, NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(40), {column}, 103))), ''), 103)"


FECHA_COBRANZA = _fecha_sql(COB["fecha"])
FECHA_CREDITO = _fecha_sql(CRE["fecha"])
SQL_PAGOS = f"""
WITH pagos_diarios AS (
    SELECT {COB["cliente"]}               AS cliente,
           {FECHA_COBRANZA}              AS fecha,
           SUM({COB["monto"]})            AS monto
    FROM {COB["tabla"]}
    WHERE {COB["estatus"]} = 'ACTIVA'
      AND {COB["monto"]} > 0
      AND {FECHA_COBRANZA} < ?
    GROUP BY {COB["cliente"]}, {FECHA_COBRANZA}
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
SELECT {COB["cliente"]} AS cliente, MAX({FECHA_COBRANZA}) AS ultimo_pago
FROM {COB["tabla"]}
WHERE {COB["estatus"]} = 'ACTIVA' AND {COB["monto"]} > 0
  AND {FECHA_COBRANZA} < ?
GROUP BY {COB["cliente"]}
"""

SQL_PRIMER_CARGO = f"""
SELECT {CRE["cliente"]} AS cliente, MIN({FECHA_CREDITO}) AS primer_cargo
FROM {CRE["tabla"]}
WHERE {CRE["estatus"]} = 'ACTIVA'
  AND {FECHA_CREDITO} < ?
GROUP BY {CRE["cliente"]}
"""

SQL_SALDOS = f"SELECT * FROM {ALERTAS['vista']}"

# Unparseable dates cannot safely be assigned to a period. Keep the affected
# customer unscored instead of interpreting missing dates as no payments.
SQL_FECHAS_INVALIDAS = f"""
SELECT {COB["cliente"]} AS cliente, COUNT(*) AS fechas_invalidas
FROM {COB["tabla"]}
WHERE {COB["estatus"]} = 'ACTIVA' AND {COB["monto"]} > 0
  AND {FECHA_COBRANZA} IS NULL
GROUP BY {COB["cliente"]}
"""


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
    cleaned = serie.astype("string").str.strip()
    if (cleaned.isna() | cleaned.eq("")).any():
        raise ValueError("Hay registros sin clave de cliente; corrige el origen del reporte.")
    return cleaned


def valor_tipico(valores):
    """>= MIN_PAGOS_IQR datos: promedio sin atípicos (IQR). Menos: mediana."""
    v = np.asarray(valores, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return np.nan
    if v.size >= MIN_PAGOS_IQR:
        q1, q3 = np.percentile(v, [25, 75])
        iqr = q3 - q1
        limpios = v[(v >= q1 - K_IQR * iqr) & (v <= q3 + K_IQR * iqr)]
        return float(limpios.mean()) if limpios.size else float(np.median(v))
    return float(np.median(v))


def semaforo(score):
    if pd.isna(score) or not np.isfinite(score):
        return "sin_datos"
    if score > UMBRAL_ROJO:
        return "rojo"
    if score > UMBRAL_AMARILLO:
        return "amarillo"
    return "verde"


# ============ FUNCIÓN PRINCIPAL ============
def calcular_score_riesgo(conn_str, hoy=None):
    """Use current balances and payment history up to hoy; not a historical balance snapshot."""
    hoy = hoy or date.today()
    inicio = hoy - timedelta(days=VENTANA_DIAS)
    fin = hoy + timedelta(days=1)

    # --- 1. Extracción (solo lectura) ---
    import pyodbc

    conn = pyodbc.connect(conn_str, readonly=True, timeout=10)
    try:
        conn.timeout = 15
        pagos = _leer(conn, SQL_PAGOS, (fin, inicio))
        ultimo = _leer(conn, SQL_ULTIMO_PAGO, (fin,))
        primer = _leer(conn, SQL_PRIMER_CARGO, (fin,))
        calidad = _leer(conn, SQL_FECHAS_INVALIDAS)
        saldos = _leer_saldos(conn)
    finally:
        conn.close()

    return calcular_desde_datos(pagos, ultimo, primer, saldos, calidad, hoy)


def calcular_desde_datos(pagos, ultimo, primer, saldos, calidad, hoy):
    """Calculate from extracted daily payments and current balances without SQL I/O."""
    pagos, ultimo, primer, saldos, calidad = (
        frame.copy() for frame in (pagos, ultimo, primer, saldos, calidad)
    )

    for df in (pagos, ultimo, primer, saldos, calidad):
        df["cliente"] = _limpiar_clave(df["cliente"])
    for df in (ultimo, primer, saldos, calidad):
        if df["cliente"].duplicated().any():
            raise ValueError("Hay claves de cliente duplicadas en un resumen del reporte.")
    pagos["monto"] = pagos["monto"].astype(float)
    saldos["D"] = pd.to_numeric(saldos["D"], errors="coerce").astype(float)
    saldos.loc[~np.isfinite(saldos["D"]), "D"] = np.nan

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
        .merge(calidad, on="cliente", how="left")
    )
    base["n_pagos"] = base["n_pagos"].fillna(0).astype(int)
    base["historial"] = base["n_pagos"] >= MIN_PAGOS

    # --- 3. Segmentos por monto de deuda ---
    saldo_conocido = base["D"].notna()
    con_deuda = saldo_conocido & (base["D"] > 0)
    sin_deuda = saldo_conocido & (base["D"] <= 0)
    base["segmento"] = np.nan
    if con_deuda.any():
        base.loc[con_deuda, "segmento"] = pd.qcut(
            base.loc[con_deuda, "D"], q=N_SEGMENTOS, labels=False, duplicates="drop"
        )
    ref = base[base["historial"] & con_deuda]
    seg = ref.groupby("segmento").agg(P_seg=("P_propio", "median"), d_seg=("d_propio", "median"))
    base = base.merge(seg, left_on="segmento", right_index=True, how="left")
    base["P_seg"] = base["P_seg"].fillna(ref["P_propio"].median())
    base["d_seg"] = base["d_seg"].fillna(ref["d_propio"].median())

    usa_propio = base["historial"] & base["d_propio"].notna()
    base["fuente"] = np.where(usa_propio, "propio", "segmento")
    base["P_tipico"] = np.where(usa_propio, base["P_propio"], base["P_seg"])
    base["d_tipico"] = np.where(usa_propio, base["d_propio"], base["d_seg"])

    # --- 4. Días sin pagar ---
    hoy_ts = pd.Timestamp(hoy)
    ref_fecha = pd.to_datetime(base["ultimo_pago"]).fillna(pd.to_datetime(base["primer_cargo"]))
    base["t"] = (hoy_ts - ref_fecha).dt.days

    # --- 5. Fórmula final ---
    P = base["P_tipico"].clip(lower=0.01)
    d = base["d_tipico"].clip(lower=1)
    base["C"] = base["D"].clip(lower=0) / P
    base["R"] = np.maximum(1.0, base["t"] / d)
    base["score"] = base["C"] * base["R"]
    base.loc[sin_deuda, ["C", "score"]] = 0.0
    base.loc[sin_deuda, "R"] = 1.0
    base["dias_liquidar"] = base["score"] * d
    base.loc[sin_deuda, "dias_liquidar"] = 0.0
    base["motivo"] = ""
    base.loc[con_deuda & base["score"].isna(), "motivo"] = (
        "Historial insuficiente para calcular el riesgo."
    )
    base.loc[con_deuda & base["t"].isna(), "motivo"] = (
        "No hay fecha válida del último pago ni del primer cargo."
    )
    base.loc[con_deuda & base["fechas_invalidas"].fillna(0).gt(0), "motivo"] = (
        "Hay pagos activos con FECHA vacía o inválida."
    )
    base.loc[~saldo_conocido, "motivo"] = "Saldo desconocido o inválido."
    base.loc[base["motivo"].ne(""), ["C", "R", "score", "dias_liquidar"]] = np.nan
    base["semaforo"] = base["score"].apply(semaforo)

    cols = [
        "cliente",
        "D",
        "n_pagos",
        "fuente",
        "segmento",
        "P_tipico",
        "d_tipico",
        "t",
        "C",
        "R",
        "score",
        "dias_liquidar",
        "semaforo",
        "motivo",
    ]
    return base[cols].sort_values("score", ascending=False).reset_index(drop=True)
