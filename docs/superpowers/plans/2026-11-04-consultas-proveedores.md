# Consultas de proveedores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consultar por WhatsApp lo que se le debe a un proveedor, su última nota y sus movimientos, y decidir por nombre si se habla de un cliente o de un proveedor, conservando ese contexto en la conversación.

**Architecture:** Un módulo nuevo, `SupplierQueries`, ejecuta consultas verificadas con valores parametrizados, como `CustomerQueries`. Un directorio busca el nombre en ambos catálogos, y la decisión de entidad la toma el servicio, no el modelo. El contexto se recupera del texto de las respuestas anteriores, igual que hoy se hace con los códigos.

**Tech Stack:** Python 3.11, FastAPI, pyodbc y SQL Server; `unittest` con SQLite para las pruebas.

**Spec:** La sección "Decisiones y evidencia" de este plan. La pidió el responsable en conversación, el 2026-11-04.

## Decisiones y evidencia

### Evidencia de la base (2026-11-04)

| Hecho | Resultado |
|---|---|
| `[SALDO ACTUAL]` = `SALDO_INICIAL` + `IMPORTE` − `ABONO` | 228 de 228 proveedores |
| `IMPORTE` del catálogo = `SUM(COMPRAS_DATA.IMPORTE)` activas, por `PROVEEDOR = CODIGO` | 228 de 228 |
| `ABONO` = `SUM(PAGO_PROVEEDOR_DATA.IMPORTE)` activos, por `PROVEEDOR = CODIGO` | 228 de 228 |
| Claves duplicadas en el catálogo | 0 |
| Estados del catálogo | `ACTIVO` 90, `INACTIVO` 138 |
| Saldos | 11 positivos, 5 negativos, 212 en cero |
| Fechas (`FECHA`, texto dd/MM/yyyy) inválidas en filas activas | 0 en compras, 0 en pagos |
| Filas de compra activas | Varias por `FOLIO`; `SUM(IMPORTE)` = `MAX(TOTAL)` en los 2660 folios |
| `TOTAL` dentro de un folio | Varía en 30 folios: no usar `SUM(TOTAL)` |
| Folios de pago activos | Únicos (1594 de 1594) |
| Códigos presentes en ambos catálogos | 45; 32 con otro nombre, es decir, otra persona |
| Nombres presentes en ambos catálogos | 17 |
| «CUERVO» | 4 proveedores (1 activo, 1 con saldo) y 0 clientes |

### Reglas de negocio

- **Saldo positivo:** le debemos al proveedor. **Negativo:** saldo a nuestro favor. *Pendiente de confirmar por el responsable (Task 1).*
- **Fuentes:** `CATALOGO_PROVEEDORES_DATA_V`, `COMPRAS_DATA`, `PAGO_PROVEEDOR_DATA`. Solo filas con `ESTADO = 'ACTIVA'`.
- **Importe de una nota:** `SUM([IMPORTE])` por `FOLIO`.
- **Saldo:** siempre de `[SALDO ACTUAL]`, nunca reconstruido.
- **Proveedor inactivo:** se puede consultar y aparece marcado. Nunca se elige solo por estar activo.

### Decisión de entidad, en este orden

1. **Fila elegida** de una lista («el segundo»): la entidad de esa fila.
2. **Palabra explícita** en la pregunta:
   - Proveedor: `proveedor(es)`, `le debemos`, `debemos a`, `le compramos`, `le pagamos`, `nos vendió`.
   - Cliente: `cliente(s)`, `nos debe`, `me debe`, `nos pagó`, `le vendimos`.
   - «Cuánto debe X» no decide.
3. **Conversación:** la entidad de la respuesta más reciente del asistente que mencione ese nombre o código.
4. **Búsqueda en ambos catálogos:**
   - Solo en uno: se usa ese.
   - En ambos: se pide elegir.
   - En ninguno: la pista de "no encontrado" actual.
5. **`entidad` del modelo:** solo reduce la búsqueda si el término existe en esa entidad. Si la operación no lleva nombre, como el ranking, decide el modelo; si tampoco lo indica, se usa cliente, como hoy.

## Global Constraints

- Los argumentos del modelo son valores, nunca fragmentos SQL; toda consulta va parametrizada.
- Ninguna operación de proveedor modifica datos.
- Las respuestas actuales de clientes no cambian de texto. Todo test existente debe seguir pasando sin editarlo, salvo donde una tarea lo indique.
- Un servicio sin las fuentes de proveedores autorizadas se comporta exactamente como hoy.
- Toda respuesta sobre un solo proveedor empieza con la línea `Proveedor {nombre} (código {código})`. De esa línea depende la continuidad.
- Respuestas de hasta 2800 caracteres, como en clientes. Si se recorta, se informa.
- Los logs no incluyen nombres, teléfonos, preguntas ni importes.
- Correr antes de cada commit: `.\.venv\Scripts\python.exe -m ruff check src tests scripts` y `.\.venv\Scripts\python.exe -m ruff format --check src tests scripts`.
- Terminar cada commit con `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Código en ambos catálogos, con personas distintas:** tras hablar del proveedor con código X, «¿y sus pagos?» debe seguir con el proveedor, aunque X también sea un cliente. Lo fija `test_code_in_both_catalogs_keeps_conversation_entity` en Task 6.
2. **Cliente cuyo nombre contiene «Proveedor»:** una respuesta sobre «Proveedora del Norte» no debe convertirse en contexto de proveedor. Lo fija `test_customer_named_like_supplier_stays_customer` en Task 5.
3. **Lista mixta y «el segundo»:** debe usar la entidad y el código de esa fila, no los de la primera. Lo fija `test_mixed_list_position_uses_row_entity` en Task 6.
4. **Saldo negativo o nulo de un proveedor:** nunca debe decir «le debemos» con un negativo ni inventar cero. Lo fijan `test_negative_balance_is_in_our_favor` y `test_unknown_balance_is_unavailable` en Task 2.
5. **Producción sin permisos o sin la vista en `SQL_ALLOWED_TABLES`:** las consultas de clientes siguen igual. Lo fija `test_without_supplier_sources_customers_are_unchanged` en Task 6.

---

### Task 1: Fuentes autorizadas y contrato

**Files:**
- Modify: `src/dbagg/database/catalog.json`
- Modify: `scripts/sql/dbagg_readonly.sql`
- Modify: `src/dbagg/context/default.json:102-109`
- Create: `src/dbagg/business/suppliers.py`
- Modify: `src/dbagg/agent/service.py:30-35`, `src/dbagg/agent/service.py:367-381`
- Test: `tests/unit/test_supplier_queries.py`

**Interfaces:**
- Produces: `SUPPLIERS = "proadel.catalogo_proveedores_data_v"`, `SUPPLIER_PAYMENTS = "proadel.pago_proveedor_data"`, `PURCHASES = "proadel.compras_data"`, `SUPPLIER_PROTECTED_SOURCES = frozenset({SUPPLIERS, SUPPLIER_PAYMENTS})`, `validate_supplier_context(context: str) -> None`.

- [ ] **Step 1: Pedir al responsable confirmar la regla del signo**

Confirmar que saldo positivo significa «le debemos». Sin esa confirmación, no marcar el contrato como `confirmed_by_owner`.

- [ ] **Step 2: Escribir los tests que fallan**

```python
def test_supplier_sources_are_business_tool(self):
    catalog = load_catalog()
    for source in (SUPPLIERS, SUPPLIER_PAYMENTS):
        self.assertEqual(catalog.sources[source].access, "business_tool")

def test_purchases_stay_exploratory(self):
    self.assertEqual(load_catalog().sources[PURCHASES].access, "exploratory")

def test_default_context_matches_supplier_contract(self):
    validate_supplier_context(load_business_context())

def test_changed_supplier_column_is_rejected(self):
    # Cambiar current_balance en una copia del contexto.
    with self.assertRaisesRegex(BusinessError, "contrato"):
        validate_supplier_context(changed_context)
```

- [ ] **Step 3: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_supplier_queries -v`
Expected: ERROR, `dbagg.business.suppliers` no existe.

- [ ] **Step 4: Implementar**

- `catalog.json`, dominio `compras`, estado `verified`, acceso `business_tool`:
  - `CATALOGO_PROVEEDORES_DATA_V`: `CODIGO, NOMBRE, SALDO ACTUAL, IMPORTE, ABONO, SALDO_INICIAL, ESTADO`. Sin `DIRECCION` ni `TELEFONO`.
  - `PAGO_PROVEEDOR_DATA`: `ID, FECHA, FOLIO, PROVEEDOR, IMPORTE, ESTADO`.
- `dbagg_readonly.sql`: un `GRANT SELECT` por cada objeto nuevo.
- `default.json`: nuevo tema `supplier_balance` (`supplier_key: CODIGO`, `supplier_name: NOMBRE`, `current_balance: SALDO ACTUAL`) y nuevo tema `supplier_payments` (`supplier_key: PROVEEDOR`, `amount: IMPORTE`, `date: FECHA`, `status: ESTADO`, `record_id: ID`, `receipt: FOLIO`), con `confirmed_filters: {"ESTADO": "ACTIVA"}`. Ambos `confirmed_by_owner`. Quitar «Saldo oficial del proveedor y clave de relación» de `pending` en `suppliers_and_purchases`.
- `suppliers.py`: las constantes y `validate_supplier_context`, con el mismo criterio de `validate_context`.
- `service.py`: `_query_sql` rechaza también `SUPPLIER_PROTECTED_SOURCES`.

- [ ] **Step 5: Correr y verificar que pasan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_supplier_queries tests.unit.test_readonly_grants tests.unit.test_catalog -v`
Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add src/dbagg/database/catalog.json scripts/sql/dbagg_readonly.sql src/dbagg/context/default.json src/dbagg/business/suppliers.py src/dbagg/agent/service.py tests/unit/test_supplier_queries.py
git commit -m "feat: authorize verified supplier sources"
```

### Task 2: Saldos, coincidencias y ranking

**Files:**
- Modify: `src/dbagg/business/suppliers.py`
- Test: `tests/unit/test_supplier_queries.py`

**Interfaces:**
- Consumes: `like_literal`, `date_expression`, `decimal_value` de `dbagg.business.customers`; `BusinessAnswer`, `BusinessError`, `amount`, `label`, `records` de `dbagg.business.evidence`.
- Produces:
  - `SUPPLIER_OPERATIONS = ("saldo", "saldos_coincidencias", "contar_coincidencias", "ranking_deuda", "pagos", "ultimo_pago", "total_pagos", "notas_pendientes", "ultima_nota", "movimientos")`
  - `SUPPLIER_ONLY_OPERATIONS = ("ultima_nota", "movimientos")`
  - `SUPPLIER_NOT_FOUND`, `SUPPLIER_MATCHES_NOT_FOUND`
  - `SupplierQueries(db, allowed_tables, today=None).execute(operation, supplier=None, period="todo", start=None, end=None, limit=5, codigo_exacto=False) -> BusinessAnswer`

- [ ] **Step 1: Escribir los tests que fallan**

Base SQLite sintética, con el patrón de `SyntheticDatabase` en `tests/unit/test_customer_queries.py`.

```python
def test_positive_balance_says_we_owe(self):
    text = run("saldo", "ACME").text
    self.assertEqual(text.splitlines()[0], "Proveedor Acme SA (código ACME)")
    self.assertIn("Le debemos: $1,500.00", text)

def test_negative_balance_is_in_our_favor(self):
    text = run("saldo", "FAVOR").text
    self.assertIn("Saldo a nuestro favor: $200.00", text)
    self.assertNotIn("Le debemos", text)

def test_zero_balance_has_nothing_pending(self):
    self.assertIn("Sin saldo pendiente", run("saldo", "CERO").text)

def test_unknown_balance_is_unavailable(self):
    self.assertEqual(run("saldo", "NULO").status, "unavailable")

def test_inactive_supplier_is_marked(self):
    self.assertIn("Proveedor inactivo en el catálogo.", run("saldo", "VIEJO").text)

def test_several_matches_ask_and_mark_inactive(self):
    answer = run("saldo", "Cuervo")
    self.assertEqual(answer.status, "needs_clarification")
    self.assertTrue(answer.text.startswith("Encontré varios proveedores:"))
    self.assertIn("(código CUE2) · inactivo", answer.text)

def test_ranking_lists_only_positive_balances(self):
    self.assertNotIn("FAVOR", run("ranking_deuda", None, limit=5).text)

def test_duplicate_code_is_rejected(self):
    with self.assertRaisesRegex(BusinessError, "repite una clave"):
        run("saldo", "DOBLE")
```

Ajustar el formato de `$1,500.00` a lo que realmente produce `amount`.

- [ ] **Step 2: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_supplier_queries -v`
Expected: FAIL, `SupplierQueries` no existe.

- [ ] **Step 3: Implementar `SupplierQueries`, `_resolve`, `_matching_balances`, `_count_matches` y `_ranking` en `suppliers.py`**

Seguir el comportamiento de los métodos homónimos de `CustomerQueries`, con estos cambios:

- Títulos en términos de proveedor. Ranking: `Proveedores a los que más les debemos (hasta {limit}):`.
- Agregar `[ESTADO]` a las columnas y marcar `· inactivo` en cada fila de lista cuyo estado sea `INACTIVO`.
- No filtrar por estado.

Marcar `pagos`, `ultimo_pago`, `total_pagos`, `notas_pendientes`, `ultima_nota` y `movimientos` con `BusinessError("Operación no soportada.")` hasta las Tasks 3 y 4.

- [ ] **Step 4: Correr y verificar que pasan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_supplier_queries -v`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add src/dbagg/business/suppliers.py tests/unit/test_supplier_queries.py
git commit -m "feat: add verified supplier balance queries"
```

### Task 3: Pagos a proveedores

**Files:**
- Modify: `src/dbagg/business/suppliers.py`
- Test: `tests/unit/test_supplier_queries.py`

**Interfaces:**
- Consumes: `SupplierQueries` de Task 2.
- Produces: `pagos`, `ultimo_pago` y `total_pagos` en `execute`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_total_payments_excludes_cancelled(self):
    self.assertIn("$300.00", run("total_pagos", "ACME", period="este_mes").text)

def test_last_payment_with_invalid_date_is_unavailable(self):
    self.assertEqual(run("ultimo_pago", "SINFECHA").status, "unavailable")

def test_payment_list_beyond_limit_is_partial(self):
    answer = run("pagos", "MUCHOS", limit=1)
    self.assertEqual(answer.status, "partial")
    self.assertIn("No es una lista completa", answer.text)

def test_invalid_period_is_rejected(self):
    with self.assertRaisesRegex(BusinessError, "período válido"):
        run("pagos", "ACME", period="rango", start="ayer", end="hoy")
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_supplier_queries -v`
Expected: FAIL, `Operación no soportada.`

- [ ] **Step 3: Implementar con `[PROVEEDOR] = ? AND [ESTADO] = 'ACTIVA'`**

Mismo manejo de fechas, faltantes y mensajes parciales que `CustomerQueries.execute` (`customers.py:267-367`). Primera línea según la Global Constraint; títulos `Pagos que le hicimos` y `Total pagado`. Extraer un helper `_payment_scope` dentro de `suppliers.py`.

- [ ] **Step 4: Correr y verificar que pasan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_supplier_queries -v`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add src/dbagg/business/suppliers.py tests/unit/test_supplier_queries.py
git commit -m "feat: add supplier payment queries"
```

### Task 4: Última nota, movimientos y notas pendientes

**Files:**
- Modify: `src/dbagg/business/suppliers.py`
- Test: `tests/unit/test_supplier_queries.py`

**Interfaces:**
- Consumes: `SupplierQueries` de Task 2.
- Produces: `ultima_nota`, `movimientos` y `notas_pendientes` en `execute`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_last_note_sums_lines_not_total(self):
    # Folio 7 con dos partidas de 100 y TOTAL distinto en una de ellas.
    text = run("ultima_nota", "ACME").text
    self.assertIn("folio 7", text)
    self.assertIn("$200.00", text)

def test_last_note_ignores_cancelled(self):
    self.assertNotIn("folio 9", run("ultima_nota", "ACME").text)

def test_same_date_notes_warn_about_order(self):
    self.assertIn("Comparten fecha", run("ultima_nota", "EMPATE").text)

def test_movements_merge_purchases_and_payments_newest_first(self):
    lines = run("movimientos", "ACME", limit=3).text.splitlines()
    self.assertIn("compra", lines[1])
    self.assertIn("pago", lines[2])

def test_movements_never_look_like_a_party_list(self):
    self.assertEqual(listed_codes(run("movimientos", "ACME").text), [])

def test_movements_beyond_limit_are_partial(self):
    self.assertEqual(run("movimientos", "ACME", limit=1).status, "partial")

def test_pending_notes_square_with_balance(self):
    self.assertIn("Cuadra con el saldo actual", run("notas_pendientes", "ACME").text)

def test_pending_notes_mismatch_is_reported(self):
    answer = run("notas_pendientes", "DESCUADRE")
    self.assertEqual(answer.status, "partial")
    self.assertIn("no cuadran", answer.text)

def test_favor_balance_has_no_pending_notes(self):
    self.assertIn("a nuestro favor", run("notas_pendientes", "FAVOR").text)
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_supplier_queries -v`
Expected: FAIL, `Operación no soportada.`

- [ ] **Step 3: Implementar**

- **`ultima_nota`:** no admite período. Toma la fecha máxima de las compras activas; entre las notas de esa fecha, el mayor `FOLIO`. Muestra fecha, folio, importe (`SUM([IMPORTE])`) y hasta 10 partidas: `CONCEPTO · CANTIDAD · PRECIO · IMPORTE`. Si hay más de una nota en esa fecha, agrega `Comparten fecha; el folio no prueba cuál ocurrió después.`
- **`movimientos`:** admite período y `limit`. Una sola consulta `UNION ALL`: compras agrupadas por `FOLIO` y pagos activos. Orden: fecha descendente, luego folio descendente. Formato: `• {fecha} · compra · folio {folio} · +{importe}` y `• {fecha} · pago · folio {folio} · −{importe}`. Termina con `Saldo actual: {importe}.` y `No es un estado de cuenta completo.` si se recortó.
- **`notas_pendientes`:** mismo método FIFO de `_pending_notes` (`customers.py:369-475`), con `SUM([IMPORTE])` por `FOLIO`, pagos activos como único crédito, y sin devoluciones. El texto cierra con `Método: los pagos activos liquidan primero el saldo inicial y luego las notas más antiguas (FIFO).`

- [ ] **Step 4: Correr y verificar que pasan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_supplier_queries -v`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add src/dbagg/business/suppliers.py tests/unit/test_supplier_queries.py
git commit -m "feat: add supplier notes and movements"
```

### Task 5: Directorio y detección de entidad

**Files:**
- Create: `src/dbagg/business/parties.py`
- Create: `src/dbagg/agent/entity.py`
- Modify: `src/dbagg/agent/intent.py:85-92`
- Test: `tests/unit/test_entity.py`

**Interfaces:**
- Produces:
  - `parties.py`: `CUSTOMER = "cliente"`, `SUPPLIER = "proveedor"`, `PartyMatch(entity: str, code: str, name: str, inactive: bool)` (dataclass congelada), `PartyDirectory(db, allowed_tables).find(term: str, exact_code: bool = False) -> dict[str, list[PartyMatch]]`, `PartyDirectory.supports_suppliers -> bool`, `ambiguous_answer(term: str, matches: dict) -> str`.
  - `entity.py`: `explicit_entity(question: str) -> str | None`, `entity_in_answer(text: str) -> str`, `conversation_entity(party: str, history: list | None) -> str | None`, `listed_row(history: list | None, position: int) -> tuple[str, str] | None`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_explicit_supplier_phrases(self):
    for q in ("¿cuánto le debemos a Cuervo?", "saldo del proveedor Cuervo", "le compramos a Cuervo"):
        self.assertEqual(explicit_entity(q), SUPPLIER)

def test_explicit_customer_phrases(self):
    for q in ("¿cuánto nos debe Cuervo?", "saldo del cliente Cuervo"):
        self.assertEqual(explicit_entity(q), CUSTOMER)

def test_bare_debe_decides_nothing(self):
    self.assertIsNone(explicit_entity("¿Cuánto debe Cuervo?"))

def test_supplier_answer_is_recognized(self):
    self.assertEqual(entity_in_answer("Proveedor Acme (código ACME)\nLe debemos: $1"), SUPPLIER)

def test_customer_named_like_supplier_stays_customer(self):
    text = "Saldo actual de Proveedora del Norte (código PN): $1."
    self.assertEqual(entity_in_answer(text), CUSTOMER)

def test_conversation_uses_latest_mention(self):
    history = [assistant("Saldo actual de Ana (código X): $1."),
               assistant("Proveedor Ana (código X)\nLe debemos: $1")]
    self.assertEqual(conversation_entity("X", history), SUPPLIER)

def test_unmentioned_party_has_no_entity(self):
    self.assertIsNone(conversation_entity("Otro", [assistant("Proveedor Ana (código X)")]))

def test_listed_codes_reads_mixed_rows(self):
    text = "• Ana (cliente, código A1)\n• Ana (proveedor, código P1)\n¿Cuál quieres consultar?"
    self.assertEqual(listed_codes(text), ["A1", "P1"])

def test_inactive_rows_stay_listed(self):
    self.assertEqual(listed_codes("• Ana (código P1) · inactivo\n• Luis (código P2)"), ["P1", "P2"])

def test_directory_finds_both_sides_with_an_escaped_term(self):
    found = directory.find("Cu%rvo")  # SQLite sintético
    self.assertEqual(found[CUSTOMER], [])
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_entity -v`
Expected: ERROR, los módulos no existen.

- [ ] **Step 3: Implementar**

- **`entity_in_answer`:** devuelve `SUPPLIER` si una línea empieza con `Proveedor `, si contiene `varios proveedores`, `Proveedores a los que` o si la fila en cuestión lleva `(proveedor, `. En otro caso, `CUSTOMER`. Distingue mayúsculas para no confundir nombres.
- **`conversation_entity`:** recorre mensajes del asistente del más reciente al más antiguo, y compara con los mismos límites de palabra y normalización que `customer_in_conversation`. Excluye la aclaración de ambos catálogos.
- **`listed_codes`:** agregar el prefijo opcional `(?:cliente,\s*|proveedor,\s*)?` antes de `c[oó]digo`, y aceptar el sufijo opcional ` · inactivo`. Las líneas de movimientos deben seguir sin coincidir.
- **`PartyDirectory.find`:** busca en cada catálogo autorizado, con `TOP 6` y `like_literal`, ordenando primero las coincidencias exactas. Con `exact_code`, usa `[CODIGO] = ?`. Si los proveedores no están autorizados, devuelve esa lista vacía sin consultar y `supports_suppliers` es `False`.
- **`ambiguous_answer`:** texto exacto:

```text
Encontré «{término}» como cliente y como proveedor:
• {nombre} (cliente, código {código})
• {nombre} (proveedor, código {código})
¿Cuál quieres consultar?
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_entity tests.unit.test_list_selection -v`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add src/dbagg/business/parties.py src/dbagg/agent/entity.py src/dbagg/agent/intent.py tests/unit/test_entity.py
git commit -m "feat: detect customer or supplier from conversation"
```

### Task 6: Enrutamiento en el servicio

**Files:**
- Modify: `src/dbagg/agent/service.py:295-341`
- Modify: `src/dbagg/agent/intent.py:21-26`
- Test: `tests/unit/test_supplier_routing.py`

**Interfaces:**
- Consumes: `SupplierQueries`, `SUPPLIER_OPERATIONS`, `SUPPLIER_ONLY_OPERATIONS`, `validate_supplier_context`, `SUPPLIER_NOT_FOUND`, `SUPPLIER_MATCHES_NOT_FOUND` de Tasks 1 y 2; `PartyDirectory`, `ambiguous_answer`, `explicit_entity`, `conversation_entity`, `listed_row` de Task 5.
- Produces: `Assistant._party_entity(arguments: dict, question: str, history: list | None) -> str | BusinessAnswer`.

- [ ] **Step 1: Escribir los tests que fallan**

Usar un proveedor LLM simulado, con el patrón de `assistant(provider)` en `tests/unit/test_routing_eval.py`, y SQLite sintético.

```python
def test_only_supplier_routes_to_supplier(self):
    self.assertIn("Proveedor Cuervo", ask("saldo", customer="Cuervo"))

def test_only_customer_routes_to_customer(self):
    self.assertIn("Saldo actual de Robles", ask("saldo", customer="Robles"))

def test_name_in_both_asks_which(self):
    self.assertIn("como cliente y como proveedor", ask("saldo", customer="Ana"))

def test_explicit_word_skips_the_question(self):
    text = ask("saldo", customer="Ana", question="¿Cuánto le debemos a Ana?")
    self.assertTrue(text.startswith("Proveedor Ana"))

def test_code_in_both_catalogs_keeps_conversation_entity(self):
    history = [assistant("Proveedor Ana (código X)\nLe debemos: $1")]
    text = ask("pagos", customer="X", codigo_exacto=True, question="¿y sus pagos?", history=history)
    self.assertIn("Pagos que le hicimos", text)

def test_explicit_word_overrides_conversation(self):
    history = [assistant("Proveedor Ana (código X)\nLe debemos: $1")]
    text = ask("saldo", customer="Ana", question="¿y como cliente?", history=history)
    self.assertIn("Saldo actual de Ana", text)

def test_mixed_list_position_uses_row_entity(self):
    history = [assistant("Encontré «Ana» como cliente y como proveedor:\n"
                         "• Ana (cliente, código A1)\n• Ana (proveedor, código P1)\n¿Cuál quieres consultar?")]
    text = ask("saldo", customer="Ana", question="el segundo", history=history)
    self.assertIn("código P1", text)

def test_explicit_supplier_missing_but_customer_exists_says_so(self):
    text = ask("saldo", customer="Robles", question="saldo del proveedor Robles")
    self.assertIn("Robles sí aparece como cliente", text)

def test_supplier_only_operation_on_customer_is_clear(self):
    self.assertIn("todavía solo está disponible para proveedores", ask("movimientos", customer="Robles"))

def test_ranking_uses_explicit_supplier(self):
    text = ask("ranking_deuda", question="¿A quién le debemos más?")
    self.assertIn("Proveedores a los que más les debemos", text)

def test_unnamed_ranking_stays_with_customers(self):
    self.assertIn("Clientes con mayor saldo", ask("ranking_deuda", question="¿Quiénes deben más?"))

def test_without_supplier_sources_customers_are_unchanged(self):
    # Settings sin las fuentes de proveedores: mismas consultas y texto que hoy.
    self.assertEqual(ask_without_suppliers("saldo", customer="Robles"), EXPECTED_CUSTOMER_TEXT)
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_supplier_routing -v`
Expected: FAIL.

- [ ] **Step 3: Implementar `_party_entity` e integrarlo en `_consult_business`**

Aplicar el orden de "Decisión de entidad". Conservar las comprobaciones actuales de `customer_in_conversation` y `customer_left_unselected`. Si el resultado es `BusinessAnswer`, entregarlo. Si es proveedor: llamar a `validate_supplier_context` y a `SupplierQueries.execute`, con `customer` convertido a `supplier` y sin `entidad`. Si es cliente: el flujo actual sin `entidad`. Una operación de `SUPPLIER_ONLY_OPERATIONS` sobre un cliente devuelve `Ese reporte todavía solo está disponible para proveedores.`, con estado `needs_clarification`.

Actualizar `NOT_A_CUSTOMER_HINT` para decir «clientes ni proveedores». Los mensajes `SUPPLIER_NOT_FOUND` y `SUPPLIER_MATCHES_NOT_FOUND` activan el mismo reintento único de `NOT_FOUND_ANSWERS`. Si el test existente de ese texto falla, actualizar solo esa cadena.

- [ ] **Step 4: Correr toda la suite**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add src/dbagg/agent/service.py src/dbagg/agent/intent.py tests/unit/test_supplier_routing.py
git commit -m "feat: route party questions to customers or suppliers"
```

### Task 7: Herramienta y prompts

**Files:**
- Modify: `src/dbagg/agent/prompts.py:56-107`
- Modify: `src/dbagg/agent/templates/system.md`, `src/dbagg/agent/templates/tools.json`
- Modify (local, ignorado por git): `config/prompts/agent-v7.md`, `config/prompts/tools.json`
- Modify: `src/dbagg/agent/service.py:50-62`
- Test: `tests/unit/test_supplier_routing.py`

`config/llm.toml` usa `prompts/agent-v7.md` y `prompts/tools.json`, no las plantillas de `src/`. Sin actualizar esas copias, el modelo no conoce las operaciones nuevas, y esas copias viajan en el paquete de despliegue.

**Interfaces:**
- Consumes: `SUPPLIER_ONLY_OPERATIONS` de Task 2.
- Produces: argumento `entidad` con enum `["cliente", "proveedor"]`; `operation` también admite `ultima_nota` y `movimientos`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_tool_offers_supplier_operations_and_entity(self):
    props = tool_properties("consultar_negocio")
    self.assertIn("movimientos", props["operation"]["enum"])
    self.assertEqual(props["entidad"]["enum"], ["cliente", "proveedor"])

def test_tool_description_requires_no_table_names(self):
    self.assertNotIn("PAGO_PROVEEDOR", tool_description("consultar_negocio"))
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_supplier_routing -v`
Expected: FAIL, `movimientos` no está en el enum.

- [ ] **Step 3: Implementar**

- `prompts.py`: agregar los dos valores y `entidad`, con la descripción «Solo si la pregunta indica si es cliente o proveedor; si no, omitir».
- `customer`: la descripción pasa a «Nombre o código del cliente o proveedor presente en la conversación».
- `system.md` y `tools.json`: describir `ultima_nota` y `movimientos`. Indicar que el modelo no adivina la entidad, que `saldo` de un nombre siempre pasa por `consultar_negocio`, y que el servicio pregunta si hay ambigüedad. Agregar ejemplos: «¿Cuánto le debemos a Cuervo?», «¿Cuál es el saldo de Cuervo?» y «¿Y su última nota?». En `tools.json`, «diez operaciones» pasa a «doce».
- Quitar de `system.md` la frase que manda las consultas de proveedores a exploración, pero conservar la de compras del período.
- `service.py`: actualizar `CLARIFICATIONS["cliente"]` y `["criterio"]` para mencionar proveedores. Si un test fija esos textos, actualizar solo esa cadena.
- Aplicar los mismos cambios en `config/prompts/agent-v7.md` y `config/prompts/tools.json`. Respaldar antes en `config/prompts/backup/`, como ya se hizo con v7.

- [ ] **Step 4: Correr toda la suite**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests`
Expected: OK.

- [ ] **Step 5: Commit**

`config/prompts/` queda fuera del commit porque git lo ignora.

```bash
git add src/dbagg/agent/prompts.py src/dbagg/agent/templates src/dbagg/agent/service.py tests/unit/test_supplier_routing.py
git commit -m "feat: expose supplier operations to the model"
```

### Task 8: Regresiones, documentación y migración

**Files:**
- Modify: `evals/cases/intent-routing.json`
- Modify: `tests/integration/test_business_webhook.py`
- Modify: `docs/business-queries.md`, `docs/architecture.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: el flujo completo de Tasks 1 a 7.

- [ ] **Step 1: Escribir el test de integración que falla**

Webhook firmado con base sintética y proveedor LLM simulado, en dos turnos: `«¿Cuál es el saldo de Cuervo?»`, luego `«¿Y su última nota?»`.

```python
def test_supplier_context_survives_two_webhook_turns(self):
    first, second = conversation("¿Cuál es el saldo de Cuervo?", "¿Y su última nota?")
    self.assertTrue(first.startswith("Proveedor Cuervo"))
    self.assertTrue(second.startswith("Proveedor Cuervo"))
    self.assertIn("folio", second)
```

- [ ] **Step 2: Correr y verificar el resultado**

Run: `.\.venv\Scripts\python.exe -m unittest tests.integration.test_business_webhook -v`
Expected: OK. Si falla, corregir el flujo, no el test.

- [ ] **Step 3: Agregar casos de enrutamiento en `intent-routing.json`**

Con datos sintéticos y el formato existente: `saldo` sin entidad; «¿Cuánto le debemos a…?»; `ultima_nota` y `movimientos` tras un proveedor; «¿A quién le debemos más?»; y una pregunta de compras del mes, que debe seguir siendo exploratoria.

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_routing_eval tests.unit.test_eval_cases -v`
Expected: OK.

- [ ] **Step 4: Documentar**

- `docs/business-queries.md`: operaciones, reglas y orden de decisión de entidad.
- `docs/architecture.md`: el directorio y el contexto por texto.
- `CHANGELOG.md`, con esta migración para producción:
  1. Aplicar los `GRANT` nuevos a `dbagg_lectura`, si el servidor usa ese script.
  2. Confirmar que `SQL_ALLOWED_TABLES` de la VM incluye `proadel.CATALOGO_PROVEEDORES_DATA_V` y `proadel.PAGO_PROVEEDOR_DATA`.
  3. Publicar los prompts actualizados con el paquete.

- [ ] **Step 5: Correr la verificación del CI**

Run: `.\.venv\Scripts\python.exe -m ruff check src tests scripts whatsapp_agent.py score_riesgo.py business_context.py`
Expected: `All checks passed!`

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests`
Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add evals/cases/intent-routing.json tests/integration/test_business_webhook.py docs/business-queries.md docs/architecture.md CHANGELOG.md
git commit -m "docs: document supplier queries and migration"
```

### Task 9: Validación real en dev

Los tests con SQLite no prueban compatibilidad con SQL Server ni la decisión del modelo real. Esta tarea es la condición de publicación que fijó el responsable: quedar conforme en el WhatsApp de prueba.

**Files:**
- Ninguno, salvo correcciones de lo que aparezca, con su test de regresión.

**Interfaces:**
- Consumes: el bot en dev, con el `.env` de `dbagg-dev`.

- [ ] **Step 1: Comprobar que `.env` no es de producción**

La app debe ser `dbagg-dev`, y el callback no debe apuntar a `proadel.app`.

- [ ] **Step 2: Reiniciar el backend en el puerto 8001 y probar**

Enviar al número de prueba, en este orden:

| Mensaje | Debe pasar |
|---|---|
| `¿Cuál es el saldo de Cuervo?` | Lista los 4 proveedores, con los inactivos marcados |
| El nombre de uno, o `el segundo` | Saldo de ese proveedor |
| `¿Y su última nota?` | Sigue con ese proveedor |
| `¿Y sus movimientos?` | Compras y pagos del mismo proveedor |
| `¿Cuánto le hemos pagado este mes?` | `total_pagos` del mismo proveedor |
| Un nombre que está en ambos catálogos | Pregunta cuál |
| `el proveedor` | Sigue con el proveedor |
| `¿Y como cliente?` | Cambia a cliente |
| `¿A quién le debemos más?` | Ranking de proveedores |
| `¿Quiénes nos deben más?` | Ranking de clientes |
| `¿Cuánto compramos de maíz este mes?` | Sigue por exploración de compras |

- [ ] **Step 3: Contrastar con SQL de referencia**

Para 3 proveedores, 1 de cada tipo de saldo (positivo, negativo y cero), comparar con consultas directas:

```sql
SELECT [SALDO ACTUAL], SALDO_INICIAL, IMPORTE, ABONO FROM proadel.CATALOGO_PROVEEDORES_DATA_V WHERE CODIGO = ?;
SELECT TOP 1 FOLIO, SUM(IMPORTE) FROM proadel.COMPRAS_DATA WHERE PROVEEDOR = ? AND ESTADO = 'ACTIVA'
  GROUP BY FOLIO ORDER BY MAX(TRY_CONVERT(date, FECHA, 103)) DESC, FOLIO DESC;
SELECT SUM(IMPORTE) FROM proadel.PAGO_PROVEEDOR_DATA WHERE PROVEEDOR = ? AND ESTADO = 'ACTIVA';
```

Expected: las cifras coinciden con las respuestas del bot.

- [ ] **Step 4: Confirmar con el responsable**

Publicar solo si el responsable queda conforme con los mensajes. Abrir el PR con la plantilla del repo, incluyendo la migración de Task 8.
