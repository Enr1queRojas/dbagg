import hashlib
import hmac
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from sqlglot.errors import ParseError
from whatsapp_agent import Assistant, Settings, create_app, validate_sql, _odbc_options


class SQLTests(unittest.TestCase):
    def test_select_is_bounded(self):
        sql = validate_sql('SELECT TOP 1000 CLIENTE FROM proadel.vw_AlertasCobranza',
                           {'proadel.vw_alertascobranza'})
        self.assertIn('TOP 50', sql)
        self.assertNotIn('1000', sql)

    def test_rejects_writes_remote_and_unlisted_objects(self):
        for sql in [
            'DELETE FROM proadel.vw_AlertasCobranza',
            'SELECT * FROM proadel.vw_AlertasCobranza; DROP TABLE x',
            'SELECT * INTO backup FROM proadel.vw_AlertasCobranza',
            'SELECT * FROM otra.tabla',
            'SELECT * FROM otraDB.proadel.vw_AlertasCobranza',
            "SELECT * FROM OPENROWSET('x','y','z')",
            'SELECT dbo.secret(CLIENTE) FROM proadel.vw_AlertasCobranza',
            'SELECT * FROM proadel.vw_AlertasCobranza WITH (UPDLOCK)',
            'WITH c AS (SELECT * FROM proadel.vw_AlertasCobranza) SELECT * FROM c',
        ]:
            with self.subTest(sql=sql), self.assertRaises((ValueError, ParseError)):
                validate_sql(sql, {'proadel.vw_alertascobranza'})

    def test_odbc_password_cannot_override_tls(self):
        options = _odbc_options('PWD={x;TrustServerCertificate=yes;z}}x};'
                                'Encrypt=yes;TrustServerCertificate=no;')
        self.assertEqual(options['trustservercertificate'], 'no')
        with self.assertRaises(ValueError):
            _odbc_options('Encrypt=yes;Encrypt=no;')


class TLSSettingsTests(unittest.TestCase):
    def settings(self, encrypt='yes', trust='yes', opt_in='false'):
        values = dict(OPENAI_API_KEY='test', META_ACCESS_TOKEN='test', META_APP_SECRET='test',
                      META_VERIFY_TOKEN='test', META_PHONE_NUMBER_ID='123', META_GRAPH_VERSION='v25.0',
                      WHATSAPP_ALLOWED_NUMBERS='5215555555555', SQL_ALLOWED_TABLES='proadel.demo',
                      DB_CONNECTION_STRING=f'DRIVER={{test}};Encrypt={encrypt};TrustServerCertificate={trust};',
                      DB_ALLOW_UNVERIFIED_TLS=opt_in)
        with patch.dict(os.environ, values, clear=True), patch('whatsapp_agent.load_dotenv'):
            return Settings.from_env()

    def test_default_rejects_unverified_tls(self):
        with self.assertRaises(ValueError):
            self.settings()

    def test_demo_requires_explicit_opt_in_and_warns(self):
        with self.assertLogs('uvicorn.error', level='WARNING'):
            self.settings(opt_in='true')

    def test_demo_still_rejects_disabled_encryption(self):
        with self.assertRaises(ValueError):
            self.settings(encrypt='no', opt_in='true')

    def test_verified_tls_needs_no_exception(self):
        self.settings(trust='no')


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings('dummy', 'dummy', 'dummy', 'test-secret', 'verify',
                                 '123', 'v23.0', frozenset({'5215555555555'}),
                                 frozenset({'proadel.vw_alertascobranza'}), 'unused')
        self.assistant = Mock()
        self.assistant.answer.return_value = 'Respuesta de prueba'
        self.send = Mock()
        self.client = TestClient(create_app(self.settings, self.assistant, self.send))

    def payload(self, sender='5215555555555', phone='123'):
        return {'object': 'whatsapp_business_account', 'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': phone},
            'messages': [{'id': 'msg1', 'from': sender, 'type': 'text',
                          'text': {'body': 'saldo del cliente A'}}]}}]}]}

    def post(self, payload, signed=True):
        body = json.dumps(payload).encode()
        sig = 'sha256=' + hmac.new(b'test-secret', body, hashlib.sha256).hexdigest()
        return self.client.post('/webhook', content=body,
                                headers={'x-hub-signature-256': sig if signed else 'bad'})

    def test_meta_verification(self):
        response = self.client.get('/webhook', params={
            'hub.mode': 'subscribe', 'hub.verify_token': 'verify', 'hub.challenge': '42'})
        self.assertEqual(response.text, '42')
        self.assertEqual(self.client.get('/webhook').status_code, 403)

    def test_unsigned_or_unauthorized_messages_never_reach_llm(self):
        self.assertEqual(self.post(self.payload(), signed=False).status_code, 403)
        self.assertEqual(self.post(self.payload(sender='9999999999')).status_code, 200)
        self.assertEqual(self.post(self.payload(phone='999')).status_code, 200)
        self.assistant.answer.assert_not_called()
        self.send.assert_not_called()

    def test_authorized_message_processed_once(self):
        self.assertEqual(self.post(self.payload()).status_code, 200)
        self.assertEqual(self.post(self.payload()).status_code, 200)
        self.assistant.answer.assert_called_once_with('saldo del cliente A')
        self.send.assert_called_once_with('5215555555555', 'Respuesta de prueba')

    def test_errors_do_not_expose_credentials(self):
        self.assistant.answer.side_effect = RuntimeError('PWD=secret')
        with self.assertLogs('uvicorn.error', level='INFO') as logs:
            self.post(self.payload())
        self.assertNotIn('PWD=secret', '\n'.join(logs.output))
        self.assertNotIn('secret', self.send.call_args.args[1])

    def test_diagnostic_identifies_local_agent_without_llm(self):
        payload = self.payload()
        payload['entry'][0]['changes'][0]['value']['messages'][0]['text']['body'] = '/diagnostico'
        self.post(payload)
        self.assistant.answer.assert_not_called()
        self.assertIn('dbagg:', self.send.call_args.args[1])


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.assistant = Assistant.__new__(Assistant)
        self.assistant.settings = SimpleNamespace(model='test', tables={'proadel.vw_alertascobranza'})
        self.assistant.db = Mock()
        self.assistant.db.schema.return_value = '[{"table":"proadel.vw_alertascobranza"}]'
        self.assistant.db.query.return_value = {'columns': ['saldo'], 'rows': [['100']]}
        self.assistant.client = Mock()

    def completion(self, content):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    def test_validated_query_then_answer(self):
        self.assistant.client.chat.completions.create.side_effect = [
            self.completion(json.dumps({'sql': 'SELECT SUM(SALDO_PENDIENTE) AS saldo '
                                        'FROM proadel.vw_AlertasCobranza', 'clarification': None})),
            self.completion('Saldo: 100')]
        self.assertEqual(self.assistant.answer('saldo total'), 'Saldo: 100')
        self.assertIn('TOP 50', self.assistant.db.query.call_args.args[0])
        self.assertEqual(self.assistant.client.chat.completions.create.call_count, 2)

    def test_model_generated_write_never_reaches_database(self):
        self.assistant.client.chat.completions.create.return_value = self.completion(
            json.dumps({'sql': 'DELETE FROM proadel.vw_AlertasCobranza', 'clarification': None}))
        with self.assertRaises(ValueError):
            self.assistant.answer('elimina todos los clientes')
        self.assistant.db.query.assert_not_called()

    def test_clarification_does_not_query_database(self):
        self.assistant.client.chat.completions.create.return_value = self.completion(
            json.dumps({'sql': None, 'clarification': '¿Qué cliente?'}))
        self.assertEqual(self.assistant.answer('su saldo'), '¿Qué cliente?')
        self.assistant.db.query.assert_not_called()


if __name__ == '__main__':
    unittest.main()
