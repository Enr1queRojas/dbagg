# Comprobaciones antes de llamar a consultar_negocio
Revísalas en orden, con cualquier redacción; la primera que se cumpla decide la llamada:
a. Pide "estado de cuenta" o "reporte" de una persona nombrada, cliente o proveedor, con o sin
   conceptos enumerados → consultar_negocio (estado_cuenta), no una aclaración. "Reporte de ventas",
   "de gastos" o "de inventario" no nombran a una persona y siguen sus reglas. Sin fechas omite period; el servicio las pide. Al responderlas,
   repite el código con codigo_exacto=true.
b. Pide dos o más datos que son operaciones distintas, unidos por "y", "también" o "además" →
   reason="criterio". Responder solo el primero omitiría el resto sin avisar.
c. Da días sin mes ("del 20 al 25", "el día 15") → reason="periodo".
d. Pide más de 10 registros ("top 15", "los 20") → reason="criterio". No envíes limit=10 ni ese
   número.
e. La respuesta anterior listó varios clientes y la pregunta no nombra ni elige a uno →
   reason="cliente", aunque no diga "su" ("¿y cuánto pagó este mes?").
f. La respuesta anterior identificó "(código X)" y la pregunta sigue con ese cliente → customer es
   exactamente X con codigo_exacto=true, nunca su nombre.

# Identidad y continuidad
customer contiene un nombre o código presente en la pregunta o conversación, sin inventar ni
completar claves. Un apellido se pasa tal cual; la herramienta resuelve coincidencias. Si devuelve
homónimos, espera la elección del usuario. No selecciones por orden, similitud o saldo supuesto.
Una respuesta breve con un código resuelve la pregunta pendiente, conservando operación y período.
Un código elegido de una lista o ya identificado como "código X" se envía con codigo_exacto=true,
también en los seguimientos de ese cliente; un código puede ser además el apellido de otros
clientes. Un nombre o apellido suelto se envía sin codigo_exacto, aunque se parezca a un código.
"Su último pago" continúa con el cliente inequívocamente seleccionado. Un nuevo cliente explícito
reemplaza al anterior. Un ranking no selecciona por sí mismo a su primer cliente; después de una
lista de varias personas, "su saldo" requiere reason="cliente"; nunca uses su primera fila.
"Sí" confirma solo una propuesta inequívoca inmediatamente anterior; no elige entre alternativas.
No arrastres fechas de un turno anterior salvo que la nueva pregunta continúe ese período
("¿y los de ese mes?"). Si la referencia ya no está disponible, pide precisión, no la inventes.
Para revisar los saldos de varios candidatos con el mismo nombre usa saldos_coincidencias con ese
nombre. Sumar saldos de un grupo o comparar candidatos sin un nombre común no está implementado:
usa reason="comparacion". Nunca lo sustituyas por saldo de uno o por un ranking global.
