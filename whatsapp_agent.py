"""Compatibility entry point: uvicorn whatsapp_agent:create_app --factory."""

from dbagg.api.app import create_app

__all__ = ["create_app"]
