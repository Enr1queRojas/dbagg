"""Explicit calendar for the business, independent of the hosting machine."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def business_today(timezone="America/Mexico_City"):
    return datetime.now(ZoneInfo(timezone)).date()


def period_bounds(period, today, start=None, end=None):
    if period != "rango" and (start is not None or end is not None):
        raise ValueError("Las fechas explícitas requieren periodo=rango.")
    if period == "todo":
        return None, None
    if period == "esta_semana":
        return today - timedelta(days=today.weekday()), today + timedelta(days=1)
    if period == "semana_pasada":
        monday = today - timedelta(days=today.weekday())
        return monday - timedelta(days=7), monday
    if period == "este_mes":
        return today.replace(day=1), today + timedelta(days=1)
    if period == "mes_pasado":
        end_month = today.replace(day=1)
        return (end_month - timedelta(days=1)).replace(day=1), end_month
    if period == "rango":
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last:
            raise ValueError("El inicio del período debe preceder al fin.")
        return first, last + timedelta(days=1)
    raise ValueError("Período no reconocido.")
