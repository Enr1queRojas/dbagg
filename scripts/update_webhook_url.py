"""Point the Meta WhatsApp webhook (app and phone number) at the current tunnel URL.

Quick Cloudflare tunnels change hostname on every restart. Meta keeps two callbacks:
the app subscription (visible in the dashboard) and a phone-number override that
takes precedence and is not shown in the webhook form. This updates both.

    .\\.venv\\Scripts\\python.exe scripts\\update_webhook_url.py            # URL from cloudflared
    .\\.venv\\Scripts\\python.exe scripts\\update_webhook_url.py https://x.trycloudflare.com

Run it with Uvicorn already up: Meta verifies the callback against /webhook.
"""

import argparse
import json
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]


def tunnel_host() -> str:
    for port in range(20241, 20246):
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/quicktunnel", timeout=2)
            hostname = response.json().get("hostname")
        except (httpx.HTTPError, ValueError):
            continue
        if hostname:
            return f"https://{hostname}"
    sys.exit("No se encontró cloudflared; pasa la URL como argumento.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "url", nargs="?", help="URL base del túnel (por defecto, la de cloudflared)"
    )
    args = parser.parse_args()

    env = dotenv_values(ROOT / ".env")
    token = env["META_ACCESS_TOKEN"].strip()
    secret = env["META_APP_SECRET"].strip()
    verify = env["META_VERIFY_TOKEN"].strip()
    phone_id = env["META_PHONE_NUMBER_ID"].strip()
    graph = f"https://graph.facebook.com/{env.get('META_GRAPH_VERSION', 'v25.0').strip()}"

    callback = (args.url or tunnel_host()).rstrip("/").removesuffix("/webhook") + "/webhook"
    print(f"Callback: {callback}")

    # The same handshake Meta runs; a route may publish only /webhook, so /health can be a 404.
    challenge = "1234567"
    check = httpx.get(
        callback,
        params={"hub.mode": "subscribe", "hub.verify_token": verify, "hub.challenge": challenge},
        timeout=15,
    )
    if check.status_code != 200 or check.text != challenge:
        sys.exit(f"/webhook respondió {check.status_code}; levanta Uvicorn antes de continuar.")

    token_info = httpx.get(
        f"{graph}/debug_token", params={"input_token": token, "access_token": token}, timeout=20
    ).json()
    if "data" not in token_info:
        error = token_info.get("error", {}).get("message", token_info)
        sys.exit(f"Meta rechazó META_ACCESS_TOKEN: {error}")
    app_id = token_info["data"]["app_id"]
    app_token = f"{app_id}|{secret}"

    app_update = httpx.post(
        f"{graph}/{app_id}/subscriptions",
        data={
            "object": "whatsapp_business_account",
            "callback_url": callback,
            "verify_token": verify,
            "fields": "messages",
            "access_token": app_token,
        },
        timeout=30,
    )
    print(f"App: {app_update.status_code} {app_update.text}")

    phone_update = httpx.post(
        f"{graph}/{phone_id}",
        data={
            "webhook_configuration": json.dumps(
                {"override_callback_uri": callback, "verify_token": verify}
            ),
            "access_token": token,
        },
        timeout=30,
    )
    print(f"Número: {phone_update.status_code} {phone_update.text}")

    current = httpx.get(
        f"{graph}/{phone_id}",
        params={"fields": "webhook_configuration", "access_token": token},
        timeout=20,
    ).json()
    print(f"Configuración actual: {json.dumps(current.get('webhook_configuration', current))}")
    if not (app_update.is_success and phone_update.is_success):
        sys.exit(1)


if __name__ == "__main__":
    main()
