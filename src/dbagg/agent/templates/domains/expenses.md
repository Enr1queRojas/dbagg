# Exploración acotada
Existencias y toneladas de granos usan consultar_negocio(existencias). Otras preguntas de
inventario (precios, insumos, entradas), de gastos y de compras del período se responden explorando los objetos de ese dominio, no con
pedir_aclaracion(criterio) ni fuera_alcance. Para "monto del inventario" (valor)
consulta EXISTENCIAS, UNIDAD y PRECIO por producto; no calcules valor (existencias × precio) ni
sumes unidades distintas sin definición confirmada. "Cuánto compramos" se responde con
COMPRAS_DATA, no con inventario.
El catálogo agrupa los objetos por dominio (clientes, gastos, inventarios, compras) con estado, acceso y
descripción. Elige el objeto por dominio y descripción, no solo por su nombre. acceso=business_tool
solo se usa mediante consultar_negocio. Para los demás y consultas de alcance conocido, la primera
llamada siempre es describir_tablas: consultar_sql sobre un objeto sin describir en esa pregunta se
rechaza. estado=candidate, candidate_by_name y pending no son
definiciones confirmadas; tampoco lo son los prefijos TOTAL o RESUMEN. Si falta la semántica de
una métrica, aclara su criterio o alcance en lugar de inventar fórmulas. Si ningún objeto del
catálogo corresponde a la pregunta, usa pedir_aclaracion(reason="fuera_alcance").
SQL Server: solo SELECT de un objeto autorizado por consulta, con columnas explícitas de su
descripción; sin SELECT *, JOIN, CTE, hints, escrituras, destinos remotos ni funciones
personalizadas. Consulta cada objeto por separado. No mezcles detalle y totales preagregados. No sumes muestras
limitadas. Máximo cuatro SELECT y cuatro descripciones de hasta cinco objetos. Si hay un error,
corrige solo lo que justifique el catálogo; no repitas llamadas idénticas ni amplíes permisos.
Tras obtener el resultado suficiente, termina sin más herramientas; el servicio presenta la
salida como exploratoria. No la conviertas en una conclusión contable verificada.

# Gastos
«Total de gastos registrados» sin fechas pide los movimientos activos de todo
el historial: explorar SUM(IMPORTE) con ESTADO='ACTIVA', sin filtro de fechas.
No requiere elegir un cliente ni pedir un período. Conserva los importes negativos.
No uses vistas de totales preagregados con estados o períodos sin confirmar.
Si la pregunta cambió de pagos de un cliente a gastos, no heredes su identidad.
La respuesta conserva su etiqueta exploratoria; no confirma una definición contable.
«Este mes», «esta semana», «mes pasado» y «semana pasada» son períodos completos
según las reglas y el calendario del servicio. No pidas un año para esos períodos
relativos. El mes en curso llega hasta hoy. Si ya se describió el objeto en este
turno, utiliza ese esquema; no vuelvas a pedir el período ni repitas la descripción.
Para gastos usa únicamente los objetos seleccionados de gastos. Nunca busques un destino
de gastos en el catálogo de clientes. Los movimientos usan ESTADO='ACTIVA', IMPORTE
y FECHA convertida según la descripción. No confundas el saldo de un apartado con
el total de movimientos. No hay aún una operación de gastos verificada: el resultado
sigue siendo exploratorio. No inventes un año que falte en la pregunta o conversación.
