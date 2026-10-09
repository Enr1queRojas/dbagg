"""Install the built wheel outside the checkout and exercise its packaged resources."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SMOKE = r"""
import hashlib
import hmac
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import dbagg
import pandas as pd
from fastapi.testclient import TestClient
from dbagg.api.app import create_app
from dbagg.config import Settings
from dbagg.context.loader import load_business_context
from dbagg.paths import project_root
from dbagg.reporting.html import generar_html

assert Path(dbagg.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
assert project_root() == Path.cwd()
assert 'customer_balance' in json.loads(load_business_context())['topics']
columns = ['cliente','D','n_pagos','fuente','segmento','P_tipico','d_tipico','t','C','R','score','dias_liquidar','semaforo']
output = generar_html(pd.DataFrame(columns=columns))
assert output == Path.cwd() / 'reportes' / 'score_riesgo.html'
assert 'function riskState' in output.read_text(encoding='utf-8')
settings = Settings('dummy','dummy','dummy','local-secret','verify','123','v25.0',frozenset({'5215555555555'}),frozenset({'proadel.demo'}),'unused')
assistant, deliver = Mock(), Mock()
with TestClient(create_app(settings, assistant, deliver)) as client:
    assert client.get('/health').json()['status'] == 'ok'
    assert client.get('/webhook', params={'hub.mode':'subscribe','hub.verify_token':'verify','hub.challenge':'42'}).text == '42'
    payload = {'object':'whatsapp_business_account','entry':[{'changes':[{'value':{'metadata':{'phone_number_id':'123'},'messages':[{'id':'smoke','from':'5215555555555','type':'text','text':{'body':'/diagnostico'}}]}}]}]}
    body = json.dumps(payload).encode()
    signature = 'sha256=' + hmac.new(b'local-secret', body, hashlib.sha256).hexdigest()
    assert client.post('/webhook', content=body, headers={'x-hub-signature-256':signature}).status_code == 200
    assert 'Diagnóstico del webhook correcto' in deliver.call_args.args[1]
    assistant.answer.assert_not_called()
print('Wheel OK: import, configuration root, context, HTML/JS and signed webhook.')
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    wheels = list(args.dist.glob("dbagg-*.whl"))
    if len(wheels) != 1:
        parser.error("Build exactly one dbagg wheel in the distribution directory first.")
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        target = root / "installed"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--target",
                str(target),
                str(wheels[0].resolve()),
            ],
            check=True,
        )
        environment = dict(os.environ, PYTHONPATH=str(target), DBAGG_HOME=str(root))
        subprocess.run(
            [sys.executable, "-c", SMOKE, str(target)], cwd=root, env=environment, check=True
        )


if __name__ == "__main__":
    main()
