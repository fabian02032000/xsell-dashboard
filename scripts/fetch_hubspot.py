#!/usr/bin/env python3
"""
Lee leads (contactos) desde HubSpot y genera data.json para el dashboard.
No requiere conocimientos de programación para usarlo: se ejecuta solo,
vía GitHub Actions, cada hora. Solo necesitas configurar el token una vez
(ver README.md).

Filtra los contactos para quedarse solo con los que vinieron de la campaña
de Meta (Facebook Lead Ads / "prospectos b2b"), usando las propiedades de
"Original source" de HubSpot — así no se cuentan contactos que entren por
otras vías (email marketing, ingresados a mano, etc.).
"""
import os
import sys
import json
import datetime
import urllib.request
import urllib.error

# ---------------------- CONFIGURACIÓN ----------------------
# Puedes ajustar estos valores sin tocar el resto del código.
CAMPAIGN_START_DATE = os.environ.get("CAMPAIGN_START_DATE", "2026-07-25")  # YYYY-MM-DD
MONTHLY_GOAL = float(os.environ.get("MONTHLY_GOAL", "74"))
WEEKLY_GOAL = round(MONTHLY_GOAL * 7 / 30, 1)
CLOSED_DEALS_GOAL = int(os.environ.get("CLOSED_DEALS_GOAL", "3"))
# Debe coincidir con el "internal name" de tu etapa de "Cerrado ganado" en HubSpot.
# Por defecto en portales nuevos suele ser "closedwon". Si tu pipeline es
# personalizado, HubSpot > Configuración > Objetos > Negocios > Pipelines te
# muestra el nombre interno de cada etapa.
CLOSEDWON_STAGE = os.environ.get("CLOSEDWON_STAGE", "closedwon")

# Palabras clave (separadas por coma) que deben aparecer en la fuente del
# contacto (Original source / drill-down) para considerarlo un lead de la
# campaña de Meta. No distingue mayúsculas/minúsculas.
SOURCE_MATCH_KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("SOURCE_MATCH_KEYWORDS", "facebook,prospectos b2b").split(",")
    if k.strip()
]

HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN")
API_BASE = "https://api.hubapi.com"

SOURCE_PROPERTIES = [
    "createdate",
    "email",
    "hs_analytics_source",
    "hs_analytics_source_data_1",
    "hs_analytics_source_data_2",
]


def hubspot_post(path, body):
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {HUBSPOT_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def date_to_epoch_ms(date_str):
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc
    )
    return int(dt.timestamp() * 1000)


def fetch_all_contacts(since_date_str):
    """Trae todos los contactos creados desde since_date_str (paginado)."""
    contacts = []
    after = None
    since_epoch = date_to_epoch_ms(since_date_str)
    while True:
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "createdate",
                            "operator": "GTE",
                            "value": str(since_epoch),
                        }
                    ]
                }
            ],
            "properties": SOURCE_PROPERTIES,
            "limit": 100,
            "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
        }
        if after:
            body["after"] = after
        data = hubspot_post("/crm/v3/objects/contacts/search", body)
        contacts.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return contacts


def is_meta_lead(contact):
    """True si alguna de las propiedades de fuente contiene una de las
    palabras clave configuradas (ej. 'facebook', 'prospectos b2b')."""
    props = contact.get("properties", {})
    haystack = " ".join(
        str(props.get(p) or "").lower()
        for p in ("hs_analytics_source", "hs_analytics_source_data_1", "hs_analytics_source_data_2")
    )
    return any(kw in haystack for kw in SOURCE_MATCH_KEYWORDS)


def fetch_closed_deals_count(since_date_str):
    """Best-effort: cuenta negocios en la etapa 'cerrado ganado' desde since_date.
    Si el pipeline no coincide o la cuenta no usa Negocios, no rompe el script."""
    try:
        since_epoch = date_to_epoch_ms(since_date_str)
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "dealstage",
                            "operator": "EQ",
                            "value": CLOSEDWON_STAGE,
                        },
                        {
                            "propertyName": "closedate",
                            "operator": "GTE",
                            "value": str(since_epoch),
                        },
                    ]
                }
            ],
            "properties": ["dealstage", "closedate"],
            "limit": 100,
        }
        data = hubspot_post("/crm/v3/objects/deals/search", body)
        return {"count": len(data.get("results", [])), "available": True}
    except Exception as e:
        return {"count": None, "available": False, "error": str(e)}


def build_leads_by_day(contacts):
    counts = {}
    for c in contacts:
        createdate = c.get("properties", {}).get("createdate")
        if not createdate:
            continue
        # createdate viene como timestamp ISO en UTC
        dt = datetime.datetime.fromisoformat(createdate.replace("Z", "+00:00"))
        day = dt.date().isoformat()
        counts[day] = counts.get(day, 0) + 1
    return [{"date": d, "count": counts[d]} for d in sorted(counts)]


def main():
    if not HUBSPOT_TOKEN:
        print("ERROR: falta la variable de entorno HUBSPOT_TOKEN", file=sys.stderr)
        sys.exit(1)

    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date()
    month_start = today.replace(day=1).isoformat()

    try:
        all_contacts = fetch_all_contacts(CAMPAIGN_START_DATE)
    except urllib.error.HTTPError as e:
        print(f"ERROR al llamar a HubSpot: {e.code} {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

    contacts = [c for c in all_contacts if is_meta_lead(c)]
    excluded = len(all_contacts) - len(contacts)
    print(f"Contactos totales en el rango: {len(all_contacts)} · de Meta: {len(contacts)} · excluidos: {excluded}")

    leads_by_day = build_leads_by_day(contacts)
    total_leads = len(contacts)

    current_month_leads = sum(
        d["count"] for d in leads_by_day if d["date"] >= month_start
    )

    # Semana actual: lunes a domingo, en curso
    week_start = (today - datetime.timedelta(days=today.weekday())).isoformat()
    current_week_leads = sum(
        d["count"] for d in leads_by_day if d["date"] >= week_start
    )

    closed_deals = fetch_closed_deals_count(month_start)

    data = {
        "updated_at": now.isoformat(),
        "campaign_start": CAMPAIGN_START_DATE,
        "monthly_goal": MONTHLY_GOAL,
        "weekly_goal": WEEKLY_GOAL,
        "closed_deals_goal": CLOSED_DEALS_GOAL,
        "total_leads": total_leads,
        "leads_by_day": leads_by_day,
        "current_month_leads": current_month_leads,
        "current_month_label": today.strftime("%B %Y"),
        "current_week_leads": current_week_leads,
        "closed_deals": closed_deals,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"OK: {total_leads} leads totales, {current_month_leads} este mes, {current_week_leads} esta semana.")


if __name__ == "__main__":
    main()
