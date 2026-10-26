import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from fastapi.testclient import TestClient

from dbagg.api.app import create_app
from dbagg.config import Settings
from dbagg.integrations.meta import MediaTooLarge, MetaClient
from dbagg.llm.config import TranscriptionConfig, load_llm_config
from dbagg.llm.transcription import Transcriber, ogg_opus_seconds
from dbagg.services.conversation import ConversationService
from dbagg.services.voice import DISABLED, EMPTY, FAILED, VoiceError, VoiceNotes

SENDER = "5215555555555"


def ogg_opus(seconds, pre_skip=312):
    """Minimal synthetic Ogg Opus: OpusHead page plus a last page carrying the granule."""
    header = b"OpusHead\x01\x01" + pre_skip.to_bytes(2, "little") + b"\x80\xbb\x00\x00\x00\x00\x00"
    first = b"OggS\x00\x02" + (0).to_bytes(8, "little") + b"\x00" * 12 + b"\x01\x13" + header
    granule = int(seconds * 48000) + pre_skip
    last = b"OggS\x00\x04" + granule.to_bytes(8, "little") + b"\x00" * 12 + b"\x01\x01x"
    return first + b"audio" * 20 + last


def settings(**extra):
    return Settings(
        "dummy",
        "dummy",
        "meta-token",
        "test-secret",
        "verify",
        "123",
        "v23.0",
        frozenset({SENDER}),
        frozenset({"proadel.gastos_data"}),
        "unused",
        **extra,
    )


class AudioFormatTests(unittest.TestCase):
    def test_duration_is_read_from_the_ogg_granule(self):
        self.assertAlmostEqual(ogg_opus_seconds(ogg_opus(10)), 10, places=2)
        self.assertAlmostEqual(ogg_opus_seconds(ogg_opus(75.5)), 75.5, places=2)

    def test_non_ogg_audio_has_unknown_duration(self):
        self.assertIsNone(ogg_opus_seconds(b"ID3 mp3 data"))
        self.assertIsNone(ogg_opus_seconds(b"OggS truncated"))


class TranscriberTests(unittest.TestCase):
    def test_posts_audio_to_the_configured_model_and_normalizes_text(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["auth"] = request.headers["authorization"]
            seen["body"] = request.read()
            return httpx.Response(200, json={"text": "  ¿cuánto debe\n Esparza? "})

        transcriber = Transcriber(
            TranscriptionConfig(), "stt-secret", transport=httpx.MockTransport(handler)
        )
        text = transcriber.transcribe(ogg_opus(3), "audio/ogg; codecs=opus")
        self.assertEqual(text, "¿cuánto debe Esparza?")
        self.assertEqual(seen["url"], "https://api.openai.com/v1/audio/transcriptions")
        self.assertEqual(seen["auth"], "Bearer stt-secret")
        self.assertIn(b"gpt-4o-mini-transcribe", seen["body"])
        self.assertIn(b'name="language"', seen["body"])

    def test_provider_errors_are_raised(self):
        transcriber = Transcriber(
            TranscriptionConfig(),
            "k",
            transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})),
        )
        with self.assertRaises(httpx.HTTPStatusError):
            transcriber.transcribe(b"x", "audio/ogg")


class MediaDownloadTests(unittest.TestCase):
    def client(self, size=None, body=b"audio-bytes"):
        def handler(request):
            if request.url.host == "graph.facebook.com":
                self.assertEqual(request.headers["authorization"], "Bearer meta-token")
                info = {"url": "https://media.example/file", "mime_type": "audio/ogg"}
                if size is not None:
                    info["file_size"] = size
                return httpx.Response(200, json=info)
            return httpx.Response(200, content=body)

        return MetaClient(settings(), transport=httpx.MockTransport(handler))

    def test_downloads_into_memory(self):
        data, mime = self.client().download_media("987", 1000)
        self.assertEqual((data, mime), (b"audio-bytes", "audio/ogg"))

    def test_declared_or_streamed_size_over_the_limit_is_rejected(self):
        with self.assertRaises(MediaTooLarge):
            self.client(size=5000).download_media("987", 1000)
        with self.assertRaises(MediaTooLarge):
            self.client(body=b"x" * 2000).download_media("987", 1000)


class VoiceNotesTests(unittest.TestCase):
    def voice(self, audio=None, text="saldo de Ana", error=None):
        media = Mock()
        media.download_media.return_value = (audio or ogg_opus(5), "audio/ogg")
        transcriber = Mock()
        if error:
            transcriber.transcribe.side_effect = error
        else:
            transcriber.transcribe.return_value = text
        return VoiceNotes(media, transcriber, 60), transcriber

    def test_short_note_returns_its_transcript(self):
        voice, _ = self.voice()
        self.assertEqual(voice.text("987"), "saldo de Ana")

    def test_long_note_is_rejected_before_transcription(self):
        voice, transcriber = self.voice(audio=ogg_opus(61))
        with self.assertRaisesRegex(VoiceError, "60 segundos"):
            voice.text("987")
        transcriber.transcribe.assert_not_called()

    def test_failures_and_silence_get_clear_messages(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")
        error = httpx.HTTPStatusError("x", request=request, response=httpx.Response(500))
        voice, _ = self.voice(error=error)
        with self.assertRaises(VoiceError) as caught:
            voice.text("987")
        self.assertEqual(str(caught.exception), FAILED)
        voice, _ = self.voice(text="")
        with self.assertRaises(VoiceError) as caught:
            voice.text("987")
        self.assertEqual(str(caught.exception), EMPTY)


class VoiceConversationTests(unittest.TestCase):
    def service(self, voice):
        assistant = Mock()
        assistant.answer.return_value = "Saldo actual de Ana Demo (código D1): 10.00."
        deliver = Mock()
        return ConversationService(settings(), assistant, deliver, voice=voice), assistant, deliver

    def test_answer_starts_with_what_was_heard(self):
        voice = Mock()
        voice.text.return_value = "cuál es el saldo de Ana"
        service, assistant, deliver = self.service(voice)
        service.process_voice(SENDER, "987")
        assistant.answer.assert_called_once_with("cuál es el saldo de Ana")
        sent = deliver.call_args.args[1]
        self.assertTrue(sent.startswith("🎙️ Entendí: «cuál es el saldo de Ana»"))
        self.assertIn("10.00", sent)
        history = service.memory.history(SENDER)
        self.assertNotIn("Entendí", history[-1]["content"])

    def test_spoken_words_never_run_control_commands(self):
        voice = Mock()
        voice.text.return_value = "/reiniciar"
        service, assistant, _ = self.service(voice)
        service.process_voice(SENDER, "987")
        assistant.answer.assert_called_once_with("/reiniciar")

    def test_refusals_are_sent_without_calling_the_agent(self):
        voice = Mock()
        voice.text.side_effect = VoiceError("La nota de voz dura más de 60 segundos.")
        service, assistant, deliver = self.service(voice)
        service.process_voice(SENDER, "987")
        assistant.answer.assert_not_called()
        self.assertIn("60 segundos", deliver.call_args.args[1])
        service, assistant, deliver = self.service(None)
        service.process_voice(SENDER, "987")
        self.assertEqual(deliver.call_args.args[1], DISABLED)


class VoiceWebhookTests(unittest.TestCase):
    def post(self, client, message):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {"value": {"metadata": {"phone_number_id": "123"}, "messages": [message]}}
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        return client.post("/webhook", content=body, headers={"x-hub-signature-256": signature})

    def test_authorized_voice_note_is_transcribed_and_answered(self):
        assistant, send, voice = Mock(), Mock(), Mock()
        assistant.answer.return_value = "Respuesta"
        voice.text.return_value = "saldo de Ana"
        client = TestClient(create_app(settings(), assistant, send, voice=voice))
        audio = {"id": "m1", "from": SENDER, "type": "audio", "audio": {"id": "987", "voice": True}}
        self.assertEqual(self.post(client, audio).status_code, 200)
        voice.text.assert_called_once_with("987")
        self.assertIn("Entendí: «saldo de Ana»", send.call_args.args[1])
        self.post(client, audio)
        voice.text.assert_called_once()

    def test_unauthorized_or_malformed_audio_is_ignored(self):
        assistant, send, voice = Mock(), Mock(), Mock()
        client = TestClient(create_app(settings(), assistant, send, voice=voice))
        for message in (
            {"id": "m2", "from": "5210000000000", "type": "audio", "audio": {"id": "987"}},
            {"id": "m3", "from": SENDER, "type": "audio", "audio": {"id": "../x"}},
            {"id": "m4", "from": SENDER, "type": "image", "image": {"id": "987"}},
        ):
            with self.subTest(message=message["id"]):
                self.assertEqual(self.post(client, message).status_code, 200)
        voice.text.assert_not_called()
        send.assert_not_called()


class TranscriptionConfigTests(unittest.TestCase):
    def load(self, extra):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "llm.toml"
        path.write_text(
            'version = 1\nactive_profile = "openai"\n[profiles.openai]\nprovider = "openai"\n'
            'model = "m"\napi_key_env = "OPENAI_API_KEY"\nbase_url = "https://api.openai.com/v1"\n'
            + extra,
            encoding="utf-8",
        )
        return load_llm_config(environ={"LLM_CONFIG": str(path)})

    def test_defaults_apply_when_the_section_is_absent(self):
        config = self.load("").transcription
        self.assertEqual((config.model, config.max_seconds), ("gpt-4o-mini-transcribe", 60))

    def test_section_is_validated(self):
        config = self.load('[transcription]\nmodel = "whisper-1"\nmax_seconds = 30\n')
        self.assertEqual(config.transcription.model, "whisper-1")
        for bad in ('api_key = "secret"', "max_seconds = 9999", 'base_url = "http://x"'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.load("[transcription]\n" + bad + "\n")

    def test_settings_without_key_disable_voice(self):
        from dbagg.services.voice import build_voice

        llm = SimpleNamespace(transcription=TranscriptionConfig())
        self.assertIsNone(build_voice(settings(llm=llm), Mock()))
        self.assertIsNotNone(build_voice(settings(llm=llm, transcription_key="k"), Mock()))


if __name__ == "__main__":
    unittest.main()
