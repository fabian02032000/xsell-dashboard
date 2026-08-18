#!/usr/bin/env python3
"""
Lee el gasto (inversión), las creatividades activas, el detalle semanal
(gasto, frecuencia) y los hitos de cambios de campaña desde Meta Ads,
y genera meta_data.json para el dashboard.
Se ejecuta automáticamente vía GitHub Actions, junto con fetch_hubspot.py.
"""
import os
import sys
import json
import datetime
import urllib.request
import urllib.parse
import urllib.error

# ---------------------- CONFIGURACIÓN ----------------------
CAMPAIGN_START_DATE = os.environ.get("CAMPAIGN_START_DATE", "2026-07-25")
AD_ACCOUNT_ID = os.environ.get("AD_ACCOUNT_ID", "act_1550453590204187")
MONTHLY_BUDGET_GOAL = float(os.environ.get("MONTHLY_BUDGET_GOAL", "1500"))
# Nombre (o parte del nombre) de la campaña de leads, para identificarla entre
# todas las campañas de la cuenta.
LEADS_CAMPAIGN_MATCH = os.environ.get("LEADS_CAMPAIGN_MATCH", "prospectos b2b")

META_TOKEN = os.environ.get("META_TOKEN")
API_VERSION = "v21.0"
API_BASE = f"https://graph.facebook.com/{API_VERSION}"


def meta_get(path, params):
    params = dict(params)
    params["access_token"] = META_TOKEN
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_campaign_totals(since_date, until_date):
    """Gasto total por campaña, en el rango dado."""
    data = meta_get(
        f"/{AD_ACCOUNT_ID}/insights",
        {
            "level": "campaign",
            "fields": "spend,campaign_name",
            "time_range": json.dumps({"since": since_date, "until": until_date}),
        },
    )
    return data.get("data", [])


def fetch_daily_spend(since_date, until_date):
    """Gasto diario de toda la cuenta, para el gráfico."""
    data = meta_get(
        f"/{AD_ACCOUNT_ID}/insights",
        {
            "level": "account",
            "fields": "spend",
            "time_increment": 1,
            "time_range": json.dumps({"since": since_date, "until": until_date}),
        },
    )
    return [{"date": row["date_start"], "spend": float(row.get("spend", 0))} for row in data.get("data", [])]


def build_week_ranges(campaign_start_str, today):
    """Semanas de lunes a domingo, desde la semana de inicio de campaña
    hasta la semana actual (incluida, aunque esté en curso). Debe coincidir
    exactamente con la misma lógica en fetch_hubspot.py para poder cruzar
    los datos en el dashboard."""
    campaign_start = datetime.date.fromisoformat(campaign_start_str)
    first_monday = campaign_start - datetime.timedelta(days=campaign_start.weekday())
    weeks = []
    cursor = first_monday
    while cursor <= today:
        week_end = cursor + datetime.timedelta(days=6)
        weeks.append({"week_start": cursor.isoformat(), "week_end": week_end.isoformat()})
        cursor += datetime.timedelta(days=7)
    return weeks


def fetch_spend_by_week(weeks):
    """Gasto, impresiones, alcance y frecuencia por semana. Si una semana
    falla (p.ej. está fuera de rango de datos disponibles en la cuenta), esa
    semana queda con ceros en vez de romper el resto."""
    result = []
    today = datetime.date.today()
    for w in weeks:
        until = min(datetime.date.fromisoformat(w["week_end"]), today).isoformat()
        try:
            data = meta_get(
                f"/{AD_ACCOUNT_ID}/insights",
                {
                    "level": "account",
                    "fields": "spend,impressions,reach,frequency",
                    "time_range": json.dumps({"since": w["week_start"], "until": until}),
                },
            )
            rows = data.get("data", [])
            row = rows[0] if rows else {}
            result.append({
                **w,
                "spend": round(float(row.get("spend", 0)), 2),
                "impressions": int(float(row.get("impressions", 0))),
                "reach": int(float(row.get("reach", 0))),
                "frequency": round(float(row.get("frequency", 0)), 2),
            })
        except Exception as e:
            print(f"AVISO: no se pudo traer el detalle de la semana {w['week_start']}: {e}", file=sys.stderr)
            result.append({**w, "spend": 0, "impressions": 0, "reach": 0, "frequency": 0})
    return result


def fetch_active_creatives():
    """
    Trae los anuncios activos de la cuenta junto con la imagen real de su
    creatividad, y los separa entre "campaña de leads" y "otros".
    Si algo falla (permisos, formato, etc.) devuelve listas vacías en vez de
    romper todo el script — el gasto sigue actualizándose igual.
    """
    try:
        data = meta_get(
            f"/{AD_ACCOUNT_ID}/ads",
            {
                "fields": "name,effective_status,campaign{name},creative{image_url,thumbnail_url,name}",
                "effective_status": json.dumps(["ACTIVE"]),
                "limit": 200,
            },
        )
    except urllib.error.HTTPError as e:
        print(f"AVISO: no se pudieron traer las creatividades: {e.code} {e.read().decode()}", file=sys.stderr)
        return [], [], False
    except Exception as e:
        print(f"AVISO: no se pudieron traer las creatividades: {e}", file=sys.stderr)
        return [], [], False

    leads_creatives = []
    other_creatives = []
    seen_images = set()

    for ad in data.get("data", []):
        creative = ad.get("creative") or {}
        image_url = creative.get("image_url") or creative.get("thumbnail_url")
        if not image_url:
            continue
        if image_url in seen_images:
            continue
        seen_images.add(image_url)

        campaign_name = (ad.get("campaign") or {}).get("name", "")
        entry = {
            "ad_name": ad.get("name"),
            "campaign_name": campaign_name,
            "image_url": image_url,
        }
        if LEADS_CAMPAIGN_MATCH.lower() in campaign_name.lower():
            leads_creatives.append(entry)
        else:
            other_creatives.append(entry)

    return leads_creatives, other_creatives, True


def fetch_milestones(since_date):
    """
    Hitos: cambios hechos en la cuenta de Meta Ads (presupuesto, creatividad,
    pausas, etc.), para marcarlos en los gráficos del dashboard.
    Requiere que el token tenga permiso para ver el historial de cambios de
    la cuenta — si no lo tiene, devuelve una lista vacía sin romper nada.
    """
    try:
        data = meta_get(
            f"/{AD_ACCOUNT_ID}/activities",
            {
                "fields": "event_type,event_time,translated_event_type",
                "since": since_date,
                "limit": 100,
            },
        )
        milestones = []
        for row in data.get("data", []):
            event_time = row.get("event_time")
            if not event_time:
                continue
            date_only = event_time[:10]
            milestones.append({
                "date": date_only,
                "label": row.get("translated_event_type") or row.get("event_type") or "Cambio en la cuenta",
            })
        return milestones, True
    except urllib.error.HTTPError as e:
        print(f"AVISO: no se pudo traer el historial de cambios (hitos): {e.code} {e.read().decode()}", file=sys.stderr)
        return [], False
    except Exception as e:
        print(f"AVISO: no se pudo traer el historial de cambios (hitos): {e}", file=sys.stderr)
        return [], False


def main():
    if not META_TOKEN:
        print("ERROR: falta la variable de entorno META_TOKEN", file=sys.stderr)
        sys.exit(1)

    today = datetime.date.today()
    month_start = today.replace(day=1).isoformat()
    today_str = today.isoformat()

    try:
        campaigns = fetch_campaign_totals(CAMPAIGN_START_DATE, today_str)
        campaigns_month = fetch_campaign_totals(month_start, today_str)
        daily = fetch_daily_spend(CAMPAIGN_START_DATE, today_str)
    except urllib.error.HTTPError as e:
        print(f"ERROR al llamar a Meta: {e.code} {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

    leads_creatives, other_creatives, creatives_available = fetch_active_creatives()

    weeks = build_week_ranges(CAMPAIGN_START_DATE, today)
    spend_by_week = fetch_spend_by_week(weeks)

    milestones, milestones_available = fetch_milestones(CAMPAIGN_START_DATE)

    total_spend = sum(float(c.get("spend", 0)) for c in campaigns)
    month_spend = sum(float(c.get("spend", 0)) for c in campaigns_month)

    leads_campaign_spend_total = sum(
        float(c.get("spend", 0)) for c in campaigns
        if LEADS_CAMPAIGN_MATCH.lower() in c.get("campaign_name", "").lower()
    )
    leads_campaign_spend_month = sum(
        float(c.get("spend", 0)) for c in campaigns_month
        if LEADS_CAMPAIGN_MATCH.lower() in c.get("campaign_name", "").lower()
    )

    data = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "campaign_start": CAMPAIGN_START_DATE,
        "monthly_budget_goal": MONTHLY_BUDGET_GOAL,
        "total_spend": round(total_spend, 2),
        "month_spend": round(month_spend, 2),
        "leads_campaign_spend_total": round(leads_campaign_spend_total, 2),
        "leads_campaign_spend_month": round(leads_campaign_spend_month, 2),
        "campaigns": [
            {"name": c.get("campaign_name"), "spend": round(float(c.get("spend", 0)), 2)}
            for c in campaigns
        ],
        "daily_spend": daily,
        "spend_by_week": spend_by_week,
        "creatives_available": creatives_available,
        "leads_creatives": leads_creatives,
        "other_creatives": other_creatives,
        "milestones_available": milestones_available,
        "milestones": milestones,
    }

    with open("meta_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(
        f"OK: gasto total S/{total_spend:.2f}, este mes S/{month_spend:.2f}, "
        f"{len(leads_creatives)} creatividades de leads, {len(milestones)} hitos"
    )


if __name__ == "__main__":
    main()
