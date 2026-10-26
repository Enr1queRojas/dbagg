import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from sqlglot.errors import ParseError
from dbagg.agent.service import Assistant
from dbagg.business.customers import PROTECTED_SOURCES
from dbagg.config import Settings
from dbagg.database.catalog import load_catalog, parse_catalog
from dbagg.database.validation import validate_sql
from dbagg.llm.config import LLMConfig, ModelProfile

GASTOS = "proadel.gastos_data"
ALMACEN = "proadel.almacenes_disponibles_v"
CLIENTES = "proadel.catalogo_clientes_data_v"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()

    def test_first_domains_are_catalogued(self):
        domains = {s.domain for s in self.catalog.sources.values()}
        self.assertEqual(domains, {"clientes", "gastos", "inventarios"})
        self.assertTrue(self.catalog.version.startswith("catalog-v1:"))

    def test_business_tool_sources_match_verified_contracts(self):
        business = {n for n, s in self.catalog.sources.items() if s.access == "business_tool"}
        self.assertEqual(business, set(PROTECTED_SOURCES))

    def test_personal_columns_are_never_catalogued(self):
        for source in self.catalog.sources.values():
            with self.subTest(source=source.name):
                self.assertFalse(
                    source.columns & {"direccion", "telefono", "responsable", "chofer"}
                )

    def test_technical_objects_are_outside_the_catalogue(self):
        configured = {
            GASTOS,
            "proadel.carga_masiva_bancos_temporary",
            "proadel.override_ultimo_consumo_maiz",
            "proadel.lista_clientes_v",
            "proadel.rentas_data",
        }
        self.assertEqual(self.catalog.restrict(configured), {GASTOS})

    def test_invalid_catalogue_is_rejected(self):
        base = {
            "domains": {"gastos": "x"},
            "denied_columns": ["TELEFONO"],
            "sources": [
                {
                    "object": "demo.OBJETO",
                    "domain": "gastos",
                    "status": "candidate",
                    "access": "exploratory",
                    "description": "x",
                    "columns": ["A"],
                }
            ],
        }
        parse_catalog(json.dumps(base))
        for change in (
            {"columns": ["A", "TELEFONO"]},
            {"domain": "otro"},
            {"status": "confirmed"},
            {"object": "sin_esquema"},
            {"columns": []},
        ):
            with self.subTest(change=change):
                bad = json.loads(json.dumps(base))
                bad["sources"][0].update(change)
                with self.assertRaises(ValueError):
                    parse_catalog(json.dumps(bad))

    def test_cards_list_only_configured_objects_with_domain(self):
        cards = self.catalog.cards({GASTOS, "proadel.rentas_data"})
        self.assertEqual([c["objeto"] for c in cards["objetos"]], ["proadel.GASTOS_DATA"])
        self.assertEqual(set(cards["dominios"]), {"gastos"})
        self.assertEqual(cards["objetos"][0]["estado"], "candidate")


class CatalogValidationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.tables = self.catalog.restrict({GASTOS, ALMACEN, CLIENTES})

    def validate(self, sql):
        return validate_sql(sql, self.tables, self.catalog)

    def test_expense_total_by_category_and_period_is_allowed(self):
        sql = self.validate(
            "SELECT CATEGORIA, SUM(IMPORTE) AS total FROM proadel.GASTOS_DATA "
            "WHERE TRY_CONVERT(date, [FECHA], 103) >= '2026-10-01' "
            "GROUP BY CATEGORIA ORDER BY total DESC"
        )
        self.assertIn("TOP 50", sql)
        self.assertIn("GROUP BY CATEGORIA", sql)

    def test_inventory_columns_with_spaces_and_count_star_are_allowed(self):
        self.validate(
            "SELECT DESCRIPCION, UNIDAD, EXISTENCIAS, [INVENTARIO INICIAL] "
            "FROM proadel.ALMACENES_DISPONIBLES_V WHERE EXISTENCIAS > 0"
        )
        self.validate("SELECT COUNT(*) AS productos FROM proadel.ALMACENES_DISPONIBLES_V")

    def test_policy_violations_are_rejected(self):
        for sql in (
            "SELECT * FROM proadel.GASTOS_DATA",
            "SELECT DIRECCION FROM proadel.CATALOGO_CLIENTES_DATA_V",
            "SELECT RESPONSABLE, IMPORTE FROM proadel.GASTOS_DATA",
            "SELECT COLUMNA_INVENTADA FROM proadel.GASTOS_DATA",
            "SELECT g.IMPORTE FROM proadel.GASTOS_DATA g "
            "JOIN proadel.ALMACENES_DISPONIBLES_V a ON a.CODIGO = g.DESTINO",
            "SELECT IMPORTE FROM proadel.GASTOS_DATA WHERE DESTINO IN "
            "(SELECT CODIGO FROM proadel.ALMACENES_DISPONIBLES_V)",
            "SELECT IMPORTE FROM proadel.RENTAS_DATA",
        ):
            with self.subTest(sql=sql), self.assertRaises((ValueError, ParseError)):
                self.validate(sql)

    def test_declared_join_is_the_only_way_to_combine_objects(self):
        text = json.dumps(
            {
                "domains": {"gastos": "x"},
                "sources": [
                    {
                        "object": f"demo.{name}",
                        "domain": "gastos",
                        "status": "candidate",
                        "access": "exploratory",
                        "description": "x",
                        "columns": ["CODIGO", "TOTAL"],
                    }
                    for name in ("A", "B")
                ],
                "joins": [["demo.A", "demo.B"]],
            }
        )
        catalog = parse_catalog(text)
        validate_sql(
            "SELECT a.CODIGO, b.TOTAL FROM demo.A a JOIN demo.B b ON a.CODIGO = b.CODIGO",
            catalog.restrict({"demo.a", "demo.b"}),
            catalog,
        )


class CatalogAgentTests(unittest.TestCase):
    def completion(self, content=None, name=None, arguments=None):
        calls = (
            []
            if name is None
            else [
                SimpleNamespace(
                    id="c1", function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
                )
            ]
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls" if calls else "stop",
                    message=SimpleNamespace(content=content, tool_calls=calls),
                )
            ]
        )

    def test_prompt_shows_domain_cards_and_hides_uncatalogued_objects(self):
        assistant = Assistant(
            SimpleNamespace(model="test", tables={GASTOS, "proadel.rentas_data"}),
            db=Mock(),
            client=Mock(),
        )
        create = assistant.client.chat.completions.create
        create.return_value = self.completion(
            name="pedir_aclaracion", arguments={"reason": "criterio"}
        )
        assistant.answer("¿cuánto gastamos?")
        prompt = create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("proadel.GASTOS_DATA", prompt)
        self.assertIn('"dominio": "gastos"', prompt)
        self.assertNotIn("RENTAS_DATA", prompt)
        self.assertTrue(assistant.last_trace["catalog_version"].startswith("catalog-v1:"))

    def test_inventory_question_is_explored_and_marked_exploratory(self):
        assistant = Assistant(
            SimpleNamespace(model="test", tables={ALMACEN, CLIENTES}), db=Mock(), client=Mock()
        )
        assistant.db.schema.return_value = json.dumps(
            [
                {
                    "table": ALMACEN,
                    "columns": [
                        {"name": n, "type": "nvarchar"}
                        for n in ("CODIGO", "DESCRIPCION", "UNIDAD", "EXISTENCIAS", "PRECIO")
                    ],
                }
            ]
        )
        assistant.db.query.return_value = {
            "columns": ["DESCRIPCION", "UNIDAD", "EXISTENCIAS", "PRECIO"],
            "rows": [["MAIZ DEMO", "KG", "1200", "5.50"]],
        }
        create = assistant.client.chat.completions.create
        create.side_effect = [
            self.completion(name="describir_tablas", arguments={"tables": [ALMACEN]}),
            self.completion(
                name="consultar_sql",
                arguments={
                    "sql": "SELECT DESCRIPCION, UNIDAD, EXISTENCIAS, PRECIO "
                    "FROM proadel.ALMACENES_DISPONIBLES_V "
                    "WHERE UPPER(DESCRIPCION) LIKE '%MA_Z%'"
                },
            ),
            self.completion("Hay 1200 kg"),
        ]
        answer = assistant.answer("¿cuál es el monto del inventario del maíz?")
        self.assertIn("MAIZ DEMO", answer)
        self.assertIn("requiere revisión", answer)
        self.assertIn("TOP 50", assistant.db.query.call_args.args[0])
        self.assertEqual(assistant.last_trace["sources"], [ALMACEN])

    def test_described_columns_exclude_personal_data(self):
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CLIENTES, GASTOS}), db=Mock(), client=Mock()
        )
        assistant.db.schema.return_value = json.dumps(
            [
                {
                    "table": GASTOS,
                    "columns": [
                        {"name": "IMPORTE", "type": "decimal"},
                        {"name": "RESPONSABLE", "type": "nvarchar"},
                    ],
                }
            ]
        )
        create = assistant.client.chat.completions.create
        create.side_effect = [
            self.completion(name="describir_tablas", arguments={"tables": [GASTOS]}),
            self.completion(name="pedir_aclaracion", arguments={"reason": "criterio"}),
        ]
        assistant.answer("gastos")
        tool_message = create.call_args_list[1].kwargs["messages"][-1]["content"]
        self.assertIn("IMPORTE", tool_message)
        self.assertNotIn("RESPONSABLE", tool_message)


class CatalogSettingsTests(unittest.TestCase):
    def settings(self, tables):
        values = dict(
            OPENAI_API_KEY="test",
            META_ACCESS_TOKEN="test",
            META_APP_SECRET="test",
            META_VERIFY_TOKEN="test",
            META_PHONE_NUMBER_ID="123",
            META_GRAPH_VERSION="v25.0",
            WHATSAPP_ALLOWED_NUMBERS="5215555555555",
            SQL_ALLOWED_TABLES=tables,
            DB_CONNECTION_STRING="DRIVER={test};Encrypt=yes;TrustServerCertificate=no;",
        )
        with (
            patch.dict(os.environ, values, clear=True),
            patch("dbagg.config.load_dotenv"),
            patch("dbagg.config.load_llm_config", return_value=LLMConfig(ModelProfile())),
        ):
            return Settings.from_env()

    def test_environment_can_narrow_but_not_extend_the_catalogue(self):
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            settings = self.settings(
                "proadel.GASTOS_DATA,proadel.CARGA_MASIVA_BANCOS_TEMPORARY,proadel.RENTAS_DATA"
            )
        self.assertEqual(settings.tables, {GASTOS})
        self.assertIn("2 objetos fuera del catálogo", logs.output[0])

    def test_no_catalogued_object_fails_at_startup(self):
        with self.assertRaisesRegex(ValueError, "catálogo"):
            self.settings("proadel.RENTAS_DATA")


if __name__ == "__main__":
    unittest.main()
