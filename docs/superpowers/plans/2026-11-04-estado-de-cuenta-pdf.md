# Estado de cuenta en PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pedir por WhatsApp el estado de cuenta de un cliente o proveedor, aclarar persona y fechas, y recibir un PDF con el mismo contenido del ERP.

**Architecture:** `StatementQueries` calcula un `Statement` con datos puros, `statement_pdf` lo convierte en bytes y `MetaClient.send_document` lo entrega. El servicio reutiliza la decisión de entidad, deja el PDF disponible para la conversación, y la conversación lo envía antes del resumen de texto.

**Tech Stack:** Python 3.11, FastAPI, pyodbc y SQL Server, ReportLab, Graph API de WhatsApp; `unittest`, SQLite y pypdf para pruebas.

**Spec:** `docs/superpowers/specs/2026-11-04-estado-de-cuenta-pdf-design.md`

## Global Constraints

- Rama `feature/providers`. Sin push ni merge sin pedirlo.
- Los importes salen de SQL, nunca del modelo. Toda consulta va parametrizada.
- El PDF se genera en memoria y nunca se escribe en disco, logs, outbox ni historial.
- Solo filas con `ESTADO = 'ACTIVA'`. Clientes: créditos, cobranza y devoluciones, sin contado. Proveedores: compras y pagos.
- Importe de cargo: clientes `TOTAL`, proveedores `IMPORTE`. Nunca `SUM(TOTAL)` en proveedores.
- Tolerancia de cuadre: `Decimal("0.01")`. Máximo `2000` filas.
- Toda respuesta de proveedor sigue empezando con `Proveedor {nombre} (código {código})`.
- Correr antes de cada commit: `.\.venv\Scripts\python.exe -m ruff check src tests scripts` y `.\.venv\Scripts\python.exe -m ruff format --check src tests scripts`.
- No encadenar el commit a los tests: comprobar primero que pasan.
- Terminar cada commit con `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Período sin movimientos:** el PDF se entrega igual, con saldo final igual al anterior. Lo fija `test_period_without_rows_keeps_opening_balance` en Task 1.
2. **Período que termina antes de hoy:** el saldo final difiere del actual sin que parezca un descuadre. Lo fija `test_old_period_is_not_a_mismatch` en Task 1.
3. **Nombres con acentos, `Ñ`, códigos con espacios y muchas filas:** el PDF se lee completo, en varias páginas, con un nombre de archivo seguro. Lo fijan `test_accents_and_many_rows_survive` y `test_filename_is_safe` en Task 2.
4. **La carga funciona pero el mensaje falla:** nunca se dice que el PDF se entregó. Lo fija `test_failed_document_replaces_the_summary` en Task 4.
5. **Código en ambos catálogos al responder las fechas:** el reporte sale de la persona ya aclarada. Lo fija `test_dates_follow_the_cleared_supplier` en Task 4.

---

### Task 1: Datos del estado de cuenta

**Files:**
- Create: `src/dbagg/business/statements.py`
- Modify: `src/dbagg/database/client.py`
- Test: `tests/unit/test_statements.py`, `tests/unit/test_database_client.py`
- Modify: `tests/unit/test_supplier_queries.py` (`query_report` en la base sintética)

**Interfaces:**
- Consumes: `CatalogQueries`, `CUSTOMERS`, `PAYMENTS`, `CREDITS`, `RETURNS`, `date_expression`, `decimal_value` de `customers.py`; `SUPPLIERS`, `SUPPLIER_PAYMENTS`, `PURCHASES` de `suppliers.py`; `CUSTOMER`, `SUPPLIER` de `parties.py`.
- Produces:
  - `Database.query_report(sql: str, params: tuple = (), row_limit: int = 2001) -> dict`, con la misma forma que `query` (`columns`, `rows`, `has_more`). No recorta celdas ni aplica `MAX_RESULT_CHARS`. Rechaza `row_limit` fuera de 1 a 2001.
  - `STATEMENT_OPERATION = "estado_cuenta"`, `MAX_STATEMENT_ROWS = 2000`.
  - `StatementRow(kind: str, fecha: date, folio: str, cantidad: Decimal, codigo: str, concepto: str, precio: Decimal, total: Decimal, abono: Decimal, devolucion: Decimal, saldo: Decimal)`, dataclass congelada.
  - `Statement(entity: str, code: str, name: str, inactive: bool, start: date, end: date, opening: Decimal, rows: tuple, closing: Decimal, current: Decimal | None, warnings: tuple)`, dataclass congelada.
  - `StatementQueries(db, allowed_tables, today=None).resolve(entity: str, term: str, exact_code: bool = False) -> dict | BusinessAnswer`.
  - `StatementQueries.build(entity: str, code: str, start: date, end: date) -> Statement`. `end` es inclusivo.
  - `StatementTooLarge(BusinessError)`, con mensaje que pide fechas más cortas.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_customer_reproduces_the_erp_example(self):
    # SALDO_INICIAL 71888.60; antes del período: créditos 1503406.60, cobranza 1525809.68.
    # En el período: crédito 58415.40 el 29/09/2026, cobranza 48050.32 el 01/10/2026.
    s = self.build(CUSTOMER, "A.ESPARZA3", date(2026, 8, 30), date(2026, 11, 4))
    self.assertEqual(s.opening, Decimal("49485.52"))
    self.assertEqual([r.saldo for r in s.rows], [Decimal("107900.92"), Decimal("59850.60")])
    self.assertEqual(s.closing, Decimal("59850.60"))
    self.assertEqual(s.warnings, ())

def test_customer_rows_follow_the_erp_columns(self):
    credit, payment = self.build(CUSTOMER, "A.ESPARZA3", date(2026, 8, 30), date(2026, 11, 4)).rows
    self.assertEqual((credit.kind, credit.precio, credit.total), ("CRÉDITO", Decimal("8.30"), Decimal("58415.40")))
    self.assertEqual((payment.kind, payment.codigo, payment.concepto, payment.abono),
                     ("COBRANZA", "SANTANDER", "ABONO", Decimal("48050.32")))

def test_supplier_uses_importe_never_total(self):
    # Dos partidas con TOTAL distinto a IMPORTE.
    s = self.build(SUPPLIER, "ACME", date(2026, 10, 1), date(2026, 10, 31))
    self.assertEqual(s.rows[-1].saldo, Decimal("150.00"))
    self.assertEqual({r.kind for r in s.rows}, {"COMPRA"})

def test_cancelled_rows_are_excluded(self): ...      # no aparecen ni cambian opening
def test_cash_sales_are_never_queried(self): ...     # ningún SQL contiene contado_data
def test_returns_reduce_the_balance(self): ...       # devolucion y saldo
def test_same_day_charges_come_before_credits(self): ...
def test_end_date_is_inclusive(self): ...

def test_period_without_rows_keeps_opening_balance(self):
    s = self.build(SUPPLIER, "ACME", date(2020, 1, 1), date(2020, 1, 31))
    self.assertEqual((s.rows, s.closing), ((), s.opening))

def test_old_period_is_not_a_mismatch(self):
    s = self.build(SUPPLIER, "ACME", date(2026, 9, 1), date(2026, 9, 30))
    self.assertNotEqual(s.closing, s.current)
    self.assertEqual(s.warnings, ())

def test_mismatch_is_reported(self):
    self.assertIn("no cuadran", self.build(SUPPLIER, "DESC", D1, D2).warnings[0])

def test_undated_rows_are_counted_and_reported(self):
    self.assertIn("sin fecha válida", self.build(SUPPLIER, "SINF", D1, D2).warnings[0])

def test_more_than_the_limit_is_refused(self):
    with self.assertRaises(StatementTooLarge):
        self.build(SUPPLIER, "MUCHOS", D1, D2)

def test_inverted_dates_are_rejected(self):
    with self.assertRaisesRegex(BusinessError, "período"):
        self.build(SUPPLIER, "ACME", date(2026, 10, 2), date(2026, 10, 1))

def test_resolve_lists_homonyms_with_the_existing_wording(self):
    self.assertIn("Encontré varios proveedores:", self.queries.resolve(SUPPLIER, "Cuervo").text)

def test_unauthorized_source_is_rejected(self): ...

# tests/unit/test_database_client.py
def test_query_report_does_not_truncate_large_results(self):
    # Cursor simulado con 1,500 filas y una celda larga: todas vuelven completas.
    ...

def test_query_report_rejects_a_limit_above_the_maximum(self):
    with self.assertRaises(ValueError):
        db.query_report("SELECT 1", row_limit=2002)
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_statements tests.unit.test_database_client`
Expected: ERROR, `dbagg.business.statements` no existe.

- [ ] **Step 3: Implementar `query_report` y `statements.py`**

`StatementQueries` hereda de `CatalogQueries`. `resolve` reutiliza `CustomerQueries._resolve` o `SupplierQueries._resolve`, tras asignar `operation = STATEMENT_OPERATION` y `sources`. Lleva los movimientos en filas por partida, en una tabla por tipo, con `query_report`, y los ordena en Python por fecha, cargos antes que abonos, folio e ID. Saldo anterior y cuadre siguen la sección "Cálculo" del spec. Una cantidad o precio nulo se muestra como cero en la fila; un importe nulo agrega un aviso y cuenta como cero.

- [ ] **Step 4: Correr y verificar que pasan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_statements tests.unit.test_database_client`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add src/dbagg/business/statements.py src/dbagg/database/client.py tests/unit/test_statements.py tests/unit/test_database_client.py tests/unit/test_supplier_queries.py
git commit -m "feat: calculate customer and supplier statements"
```

### Task 2: Generador de PDF

**Files:**
- Create: `src/dbagg/reporting/statement_pdf.py`
- Modify: `pyproject.toml`, `requirements.txt`
- Test: `tests/unit/test_statement_pdf.py`

**Interfaces:**
- Consumes: `Statement`, `StatementRow` de Task 1.
- Produces: `statement_pdf(statement: Statement) -> bytes`, `statement_filename(statement: Statement) -> str`.

- [ ] **Step 1: Agregar dependencias**

`reportlab==5.1.0` en `dependencies`. La versión actual de `pypdf` en `dev`, solo para pruebas. Fijar en `requirements.txt` ReportLab y sus dependencias nuevas, con las versiones que `pip` instale.

Run: `.\.venv\Scripts\python.exe -m pip install -r requirements.txt -e ".[dev]"; .\.venv\Scripts\python.exe -m pip check`
Expected: `No broken requirements found.`

- [ ] **Step 2: Escribir los tests que fallan**

```python
def test_customer_pdf_shows_header_rows_and_balances(self):
    text = read_pdf(statement_pdf(customer_statement()))
    for expected in ("ESTADO DE CUENTA", "A.ESPARZA3", "30/08/2026", "04/11/2026",
                     "49,485.52", "107,900.92", "59,850.60", "CRÉDITO", "COBRANZA", "SANTANDER"):
        self.assertIn(expected, text)

def test_supplier_pdf_has_no_returns_column(self):
    self.assertNotIn("DEVOLUCIÓN", read_pdf(statement_pdf(supplier_statement())).upper())

def test_warnings_are_visible(self):
    self.assertIn("no cuadran", read_pdf(statement_pdf(mismatched_statement())))

def test_old_period_shows_both_balances(self):
    text = read_pdf(statement_pdf(old_statement()))
    self.assertIn("SALDO AL 30/09/2026", text)
    self.assertIn("SALDO ACTUAL", text)

def test_empty_period_says_so(self):
    self.assertIn("Sin movimientos", read_pdf(statement_pdf(empty_statement())))

def test_accents_and_many_rows_survive(self):
    pdf = statement_pdf(statement_with(name="PEÑA ÁVILA", rows=250))
    reader = PdfReader(BytesIO(pdf))
    self.assertGreater(len(reader.pages), 1)
    self.assertIn("PEÑA ÁVILA", reader.pages[-1].extract_text())
    self.assertIn("Saldo", reader.pages[-1].extract_text())

def test_long_concept_does_not_lose_the_balance_column(self): ...

def test_metadata_has_no_party_data(self):
    self.assertNotIn("PEÑA", str(PdfReader(BytesIO(statement_pdf(statement_with(name="PEÑA ÁVILA")))).metadata))

def test_filename_is_safe(self):
    self.assertEqual(statement_filename(statement_with(code="PEDRO 60 HECT")),
                     "estado-cuenta-PEDRO-60-HECT-2026-08-30-2026-11-04.pdf")
```

- [ ] **Step 3: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_statement_pdf`
Expected: ERROR, el módulo no existe.

- [ ] **Step 4: Implementar con `SimpleDocTemplate`, `landscape(letter)` y `LongTable(repeatRows=1)`**

Fuente estándar Helvetica, que incluye acentos y `Ñ`. Columnas según la tabla del spec. Encabezado de identidad y número de página en `onPage`, para que aparezcan en todas. Conceptos con `Paragraph` para que hagan salto de línea. `SimpleDocTemplate` con título genérico `Estado de cuenta`.

- [ ] **Step 5: Correr y verificar que pasan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_statement_pdf`
Expected: OK. Abrir también un PDF generado y revisarlo visualmente.

- [ ] **Step 6: Commit**

```bash
git add src/dbagg/reporting/statement_pdf.py pyproject.toml requirements.txt tests/unit/test_statement_pdf.py
git commit -m "feat: render statements as PDF"
```

### Task 3: Envío de documentos por Meta

**Files:**
- Modify: `src/dbagg/integrations/meta.py:77-135`
- Test: `tests/unit/test_meta.py`

**Interfaces:**
- Produces: `MetaClient.send_document(sender: str, content: bytes, filename: str) -> None`. Lanza la misma clase de excepción que `send` si falla.

- [ ] **Step 1: Escribir los tests que fallan**

Con `httpx.MockTransport`, como los tests actuales de `MetaClient`.

```python
def test_document_is_uploaded_then_sent_with_its_filename(self):
    client.send_document("521", b"%PDF", "estado.pdf")
    upload, message = requests
    self.assertTrue(upload.url.path.endswith("/123/media"))
    self.assertIn(b"application/pdf", upload.content)
    self.assertEqual(json.loads(message.content)["document"], {"id": "M1", "filename": "estado.pdf"})

def test_upload_retries_after_a_server_error(self): ...
def test_message_retry_does_not_upload_twice(self): ...

def test_client_error_is_not_retried(self):
    with self.assertRaises(httpx.HTTPStatusError):
        client.send_document("521", b"%PDF", "estado.pdf")
    self.assertEqual(len(requests), 1)

def test_upload_without_id_fails(self):
    with self.assertRaises(ValueError):
        client.send_document("521", b"%PDF", "estado.pdf")

def test_document_bytes_and_token_are_not_logged(self): ...
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_meta`
Expected: ERROR, `send_document` no existe.

- [ ] **Step 3: Implementar**

Extraer un helper privado de reintentos solo para `send_document`, sin tocar `send`: 3 intentos, espera de 1 s y 2 s, para 429, 5xx y timeouts. Carga: `POST /{phone_id}/media` multipart con `messaging_product=whatsapp` y `file`, tipo `application/pdf`, timeout 60 s. Mensaje: `POST /{phone_id}/messages` con `type=document`, timeout 15 s.

- [ ] **Step 4: Correr y verificar que pasan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_meta`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add src/dbagg/integrations/meta.py tests/unit/test_meta.py
git commit -m "feat: send documents through Meta"
```

### Task 4: Flujo de conversación

**Files:**
- Modify: `src/dbagg/agent/service.py`, `src/dbagg/agent/entity.py`, `src/dbagg/agent/prompts.py`
- Modify: `src/dbagg/business/customers.py:85`
- Modify: `src/dbagg/services/conversation.py`, `src/dbagg/api/app.py:53-73`
- Test: `tests/unit/test_statement_flow.py`, `tests/integration/test_business_webhook.py`

**Interfaces:**
- Consumes: Tasks 1, 2 y 3; `_party_entity` de `service.py`.
- Produces:
  - `DocumentAttachment(content: bytes, filename: str)`, dataclass congelada en `statements.py`.
  - `Assistant.pop_document() -> DocumentAttachment | None`. Devuelve el documento y lo limpia. `answer()` también lo limpia al empezar.
  - `ConversationService(..., deliver_document=None)`, `create_app(..., send_document=None)`.

`answer()` sigue devolviendo texto, de modo que el documento va por `pop_document`, igual que `last_trace`, y no dentro de `BusinessAnswer`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_without_dates_asks_and_generates_nothing(self):
    text = self.ask("estado_cuenta", customer="Cuervo")
    self.assertTrue(text.startswith("Proveedor Cuervo (código C1)"))
    self.assertIn("¿De qué fechas", text)
    self.assertIsNone(self.service.pop_document())

def test_period_todo_also_asks_for_dates(self): ...

def test_homonyms_are_cleared_before_dates(self):
    self.assertIn("Encontré varios proveedores:", self.ask("estado_cuenta", customer="Ana", entidad="proveedor"))

def test_name_in_both_catalogs_is_cleared_first(self):
    self.assertIn("como cliente y como proveedor", self.ask("estado_cuenta", customer="Ana"))

def test_dates_in_the_request_generate_the_document(self):
    text = self.ask("estado_cuenta", customer="Cuervo", period="rango", start="2026-08-30", end="2026-11-04")
    self.assertIn("Estado de cuenta del 30/08/2026 al 04/11/2026.", text)
    document = self.service.pop_document()
    self.assertTrue(document.content.startswith(b"%PDF"))
    self.assertIsNone(self.service.pop_document())

def test_dates_follow_the_cleared_supplier(self):
    # Código X en ambos catálogos, y la pregunta de fechas del proveedor en el historial.
    history = [assistant_message("Proveedor Ana Dos (código X)\n¿De qué fechas quieres el estado de cuenta?")]
    text = self.ask("estado_cuenta", customer="X", codigo_exacto=True, period="este_mes",
                    question="este mes", history=history)
    self.assertTrue(text.startswith("Proveedor Ana Dos (código X)"))

def test_customer_context_survives_the_dates_question(self):
    history = [assistant_message("Estado de cuenta de Robles (código R1). ¿De qué fechas lo quieres?")]
    self.assertEqual(conversation_entity("R1", history), CUSTOMER)

def test_new_answer_clears_an_unclaimed_document(self): ...
def test_too_many_rows_asks_for_shorter_dates_without_a_document(self): ...

# services/conversation.py
def test_document_goes_before_the_summary(self):
    self.assertEqual(calls, ["document", "text"])

def test_failed_document_replaces_the_summary(self):
    self.assertEqual(sent_text, ["No pude enviar el PDF del estado de cuenta. Intenta de nuevo."])
    self.assertNotIn("Estado de cuenta del", memory_answer)

def test_outbox_and_memory_hold_no_pdf_bytes(self): ...

# tests/integration/test_business_webhook.py
def test_statement_flow_over_the_webhook(self):
    # Turno 1 pide; turno 2 da fechas. send_document simulado recibe un PDF una sola vez.
    ...
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_statement_flow`
Expected: FAIL.

- [ ] **Step 3: Implementar**

- `prompts.py`: agregar `STATEMENT_OPERATION` al enum, también sin proveedores.
- `customers.py`: `validate_context` exige el tema `collections` también para `estado_cuenta`.
- `entity.py`: agregar `Estado de cuenta` a los prefijos de `CUSTOMER_PROSE`.
- `service.py`: tras decidir la entidad, si la operación es `estado_cuenta`, llamar a `_statement`. Valida el contexto, resuelve a la persona y, sin fechas o con `period="todo"`, pregunta. Con fechas, calcula `first` y `until` con `period_bounds`, usa `end = until - 1 día`, genera el PDF y guarda el `DocumentAttachment`. Una excepción de ReportLab se convierte en `BusinessError("No pude generar el PDF del estado de cuenta.")`.
- Textos fijos:
  - Proveedor sin fechas: `Proveedor {nombre} (código {código})\n¿De qué fechas quieres el estado de cuenta?`
  - Cliente sin fechas: `Estado de cuenta de {nombre} (código {código}). ¿De qué fechas lo quieres?`
  - Resumen: identidad, `Estado de cuenta del {inicio} al {fin}.`, `Saldo anterior: …`, `Saldo al {fin}: …`, `Movimientos: N.`, avisos e `Importes sin moneda confirmada.`
- `conversation.py`: en `_handle`, después de `answer`, tomar el documento con `pop_document` si es un `DocumentAttachment`. Enviarlo con `deliver_document`; si falla o no hay remitente, registrar `dbagg stage=document_failed` y reemplazar la respuesta por `No pude enviar el PDF del estado de cuenta. Intenta de nuevo.` Si funciona, registrar `dbagg stage=document_ok`.
- `app.py`: `deliver_document = send_document or (meta.send_document if send_message is None else None)`.

- [ ] **Step 4: Correr toda la suite**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add src/dbagg/agent src/dbagg/business/customers.py src/dbagg/business/statements.py src/dbagg/services/conversation.py src/dbagg/api/app.py tests/unit/test_statement_flow.py tests/integration/test_business_webhook.py
git commit -m "feat: deliver statements through the conversation"
```

### Task 5: Prompts, regresiones y documentación

**Files:**
- Modify: `src/dbagg/agent/templates/system.md`, `src/dbagg/agent/templates/tools.json`
- Modify (local, ignorado por git): `config/prompts/agent-v7.md`, `config/prompts/tools.json`
- Modify: `evals/cases/intent-routing.json`, `tests/unit/test_eval_cases.py:41-50`
- Modify: `docs/business-queries.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: `STATEMENT_OPERATION` de Task 1.

- [ ] **Step 1: Agregar casos de enrutamiento con datos sintéticos**

Con `consultar_negocio` y `operation="estado_cuenta"`: «Estado de cuenta de Cuervo», «Reporte del proveedor Cuervo de este mes», y una respuesta de fechas tras `¿De qué fechas…?`, con el código y `codigo_exacto=true`.

Run: `.\.venv\Scripts\python.exe -m unittest tests.unit.test_eval_cases`
Expected: FAIL, `estado_cuenta` no está entre las operaciones admitidas.

- [ ] **Step 2: Actualizar el test y los prompts**

`test_eval_cases.py` admite `STATEMENT_OPERATION`. `tools.json`: «doce» pasa a «trece» y describe `estado_cuenta`. `system.md`: `estado_cuenta` nunca se responde con otra operación; sin fechas, `period` se omite y el servicio las pide. Respaldar `config/prompts` en `backup/` y aplicar los mismos cambios. En `agent-v7.md` no agregar más de 300 caracteres.

- [ ] **Step 3: Verificar**

Run: `.\.venv\Scripts\python.exe -c "from dbagg.config import Settings; Settings.from_env(); print('OK')"`
Expected: `OK`.

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests`
Expected: OK.

- [ ] **Step 4: Documentar**

`docs/business-queries.md`: flujo, fuentes y cálculo. `CHANGELOG.md`: la migración del spec.

- [ ] **Step 5: Commit**

```bash
git add src/dbagg/agent/templates evals/cases/intent-routing.json tests/unit/test_eval_cases.py docs/business-queries.md CHANGELOG.md
git commit -m "feat: expose account statements to the model"
```

### Task 6: Validación real

Los tests usan SQLite y Meta simulado. Esta tarea comprueba T-SQL, el PDF real y el envío real.

**Files:**
- Ninguno, salvo correcciones de lo que aparezca, con su test de regresión.

**Interfaces:**
- Consumes: el bot en dev, con el `.env` de `dbagg-dev`.

- [ ] **Step 1: Comprobar contra SQL Server**

Con solo lecturas, generar `Statement` de `A.ESPARZA3` del 30/08/2026 al 04/11/2026 y de `CUERVO`, en proveedores.

Expected: `A.ESPARZA3` da saldo anterior `49,485.52`, créditos `58,415.40`, cobranza `48,050.32` y saldo final `59,850.60`. Ambos sin avisos de descuadre. El saldo final de `CUERVO` con fecha final de hoy coincide con `[SALDO ACTUAL]`.

- [ ] **Step 2: Comprobar en la imagen de Docker, si Docker está disponible**

Construir la imagen y generar un PDF dentro del contenedor. Si no lo está, anotarlo como pendiente para el despliegue.

- [ ] **Step 3: Comprobar que `.env` no es de producción**

La app debe ser `dbagg-dev`, y el callback no debe apuntar a `proadel.app`.

- [ ] **Step 4: Probar en WhatsApp con el responsable**

| Mensaje | Debe pasar |
|---|---|
| `Estado de cuenta de Cuervo` | Lista proveedores, sin PDF |
| `JOHAN WIEBE` | Pregunta fechas, sin PDF |
| `del 1 de octubre a hoy` | Llega un PDF que se abre, y después el resumen |
| `Reporte de A.Esparza3 del 30 de agosto al 4 de noviembre` | PDF sin volver a preguntar, igual a la captura del ERP |
| `Estado de cuenta de ABEL CARRAZCO` | Pregunta cliente o proveedor |
| `el proveedor`, luego `este mes` | PDF del proveedor, o sin movimientos |

Revisar también las pruebas de proveedores que siguen pendientes.

- [ ] **Step 5: Confirmar con el responsable**

Publicar solo si queda conforme con el PDF y los mensajes.
