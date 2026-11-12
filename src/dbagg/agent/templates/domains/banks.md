# Bancos
Bancos son todas las cuentas del catálogo de bancos: instituciones (Santander, Bajío), efectivo
de personas (Enrique, Don Leo), caja chica, créditos y cuentas de ajuste. Si preguntan por dinero
en una cuenta, banco, caja o efectivo, usa saldo_banco o movimientos_banco con banco tal como lo
nombran, nunca customer ni entidad; «¿cuánto debe Enrique?» sigue siendo de clientes.
saldos_bancos lista el saldo de cada cuenta sin sumarlo; «¿cuánto dinero tenemos?» también usa
saldos_bancos, sin total. saldo_banco responde solo el saldo actual. Un reporte o los movimientos
de una cuenta («reporte del banco de Don Leo de este mes») son movimientos_banco: genera un PDF con
todos los movimientos del período; sin fechas omite period y el servicio las pide. Tras una
respuesta «Cuenta X», «dame un reporte de este mes», «¿y sus movimientos?» o las fechas pedidas
siguen con esa cuenta: movimientos_banco con banco=X, nunca estado_cuenta ni una aclaración.
Ejemplos: «¿Cuánto hay en Santander?» → consultar_negocio(operation="saldo_banco",
banco="Santander"). «¿Qué gastos salieron de caja chica este mes?» →
consultar_negocio(operation="movimientos_banco", banco="caja chica", period="este_mes").
