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

También revisa, para cada lead, si ya se le creó un Negocio en HubSpot (y en
qué etapa está), y arma vistas de progreso por mes y por semana.

AVISO DE PRIVACIDAD: este script incluye nombre, empresa y correo de cada
lead en data.json, y ese archivo queda visible en un repositorio público de
GitHub. Esto fue una decisión explícita del dueño del dashboard — si en
algún momento se prefiere dejar de exponer estos datos, hay que quitar el
bloque "leads_detail" antes de que corra de nuevo.
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
    "firstname",
    "lastname",
    "company",
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


def hubspot_get(path):
    req = urllib.request.Request(
        API_BASE + path,
        headers={"Authorization": f"Bearer {HUBSPOT_TOKEN}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def date_to_epoch_ms(date_str):
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc
    )
    return int(dt.timestamp() * 1000)


def chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


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


def fetch_deal_stage_labels():
    """Devuelve {stage_id: label} juntando las etapas de todos los pipelines
    de Negocios. Si falla, devuelve {} y en el dashboard se muestra el id tal cual."""
    try:
        data = hubspot_get("/crm/v3/pipelines/deals")
        labels = {}
        for pipeline in data.get("results", []):
            for stage in pipeline.get("stages", []):
                labels[stage.get("id")] = stage.get("label", stage.get("id"))
        return labels
    except Exception as e:
        print(f"AVISO: no se pudieron traer las etapas de negocio: {e}", file=sys.stderr)
        return {}


def fetch_contact_deal_status(contact_ids, stage_labels):
    """Para cada contact_id, revisa si tiene al menos un Negocio asociado y
    en qué etapa está. Best-effort: si algo falla, todos quedan como
    'sin dato' en vez de romper el script completo."""
    status_by_contact = {cid: None for cid in contact_ids}
    if not contact_ids:
        return status_by_contact

    try:
        # 1) Asociaciones contacto -> negocio (por lotes de 100)
        contact_to_deal_ids = {}
        for batch in chunked(contact_ids, 100):
            body = {"inputs": [{"id": cid} for cid in batch]}
            resp = hubspot_post("/crm/v4/associations/contacts/deals/batch/read", body)
            for row in resp.get("results", []):
                from_id = row.get("from", {}).get("id")
                deal_ids = [t.get("toObjectId") for t in row.get("to", [])]
                if from_id and deal_ids:
                    contact_to_deal_ids[from_id] = deal_ids

        all_deal_ids = sorted({str(d) for ids in contact_to_deal_ids.values() for d in ids})

        # 2) Detalle de cada negocio (etapa, nombre, fecha de creación)
        deal_info = {}
        for batch in chunked(all_deal_ids, 100):
            body = {
                "inputs": [{"id": did} for did in batch],
                "properties": ["dealname", "dealstage", "createdate"],
            }
            resp = hubspot_post("/crm/v3/objects/deals/batch/read", body)
            for d in resp.get("results", []):
                deal_info[d["id"]] = d.get("properties", {})

        # 3) Combina: para cada contacto, toma el negocio más reciente asociado
        for cid, deal_ids in contact_to_deal_ids.items():
            candidate_deals = [deal_info[str(d)] for d in deal_ids if str(d) in deal_info]
            if not candidate_deals:
                continue
            candidate_deals.sort(key=lambda p: p.get("createdate") or "", reverse=True)
            best = candidate_deals[0]
            stage_id = best.get("dealstage")
            status_by_contact[cid] = {
                "deal_name": best.get("dealname"),
                "stage_label": stage_labels.get(stage_id, stage_id),
            }
    except Exception as e:
        print(f"AVISO: no se pudo revisar el estado de negocios por lead: {e}", file=sys.stderr)

    return status_by_contact


def build_leads_by_day(contacts):
    counts = {}
    for c in contacts:
        createdate = c.get("properties", {}).get("createdate")
        if not createdate:
            continue
        dt = datetime.datetime.fromisoformat(createdate.replace("Z", "+00:00"))
        day = dt.date().isoformat()
        counts[day] = counts.get(day, 0) + 1
    return [{"date": d, "count": counts[d]} for d in sorted(counts)]


def build_leads_by_month(leads_by_day, campaign_start_str):
    """Agrupa los leads por mes calendario, y calcula la meta de cada mes
    (prorrateada para el primer mes, si la campaña empezó a mitad de mes)."""
    campaign_start = datetime.date.fromisoformat(campaign_start_str)
    month_counts = {}
    for row in leads_by_day:
        month_key = row["date"][:7]  # "YYYY-MM"
        month_counts[month_key] = month_counts.get(month_key, 0) + row["count"]

    months = []
    for month_key in sorted(month_counts):
        year, month = (int(x) for x in month_key.split("-"))
        first_day = datetime.date(year, month, 1)
        if month == 12:
            next_month_first = datetime.date(year + 1, 1, 1)
        else:
            next_month_first = datetime.date(year, month + 1, 1)
        days_in_month = (next_month_first - first_day).days

        is_partial = first_day < campaign_start < next_month_first
        active_days = days_in_month
        if is_partial:
            active_days = (next_month_first - campaign_start).days

        goal = round(MONTHLY_GOAL * active_days / days_in_month, 1)

        months.append({
            "month": month_key,
            "label": first_day.strftime("%B %Y").capitalize(),
            "leads": month_counts[month_key],
            "goal": goal,
            "is_partial": is_partial,
        })
    return months


def build_week_ranges(campaign_start_str, today):
    """Semanas de lunes a domingo, desde la semana de inicio de campaña
    hasta la semana actual (incluida, aunque esté en curso)."""
    campaign_start = datetime.date.fromisoformat(campaign_start_str)
    first_monday = campaign_start - datetime.timedelta(days=campaign_start.weekday())
    weeks = []
    cursor = first_monday
    while cursor <= today:
        week_end = cursor + datetime.timedelta(days=6)
        weeks.append({"week_start": cursor.isoformat(), "week_end": week_end.isoformat()})
        cursor += datetime.timedelta(days=7)
    return weeks


def build_leads_by_week(leads_by_day, weeks):
    by_week = []
    for w in weeks:
        leads = sum(
            r["count"] for r in leads_by_day
            if w["week_start"] <= r["date"] <= w["week_end"]
        )
        by_week.append({**w, "leads": leads, "goal": WEEKLY_GOAL})
    return by_week


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

    # ---- Estado de negocio por lead (nombre/correo/empresa visibles a propósito — ver aviso arriba) ----
    stage_labels = fetch_deal_stage_labels()
    contact_ids = [c["id"] for c in contacts]
    deal_status_by_contact = fetch_contact_deal_status(contact_ids, stage_labels)

    leads_detail = []
    for c in contacts:
        props = c.get("properties", {})
        full_name = " ".join(x for x in [props.get("firstname"), props.get("lastname")] if x).strip()
        status = deal_status_by_contact.get(c["id"])
        createdate = props.get("createdate")
        leads_detail.append({
            "name": full_name or "(sin nombre)",
            "email": props.get("email") or "(sin correo)",
            "company": props.get("company") or "(sin empresa)",
            "created_date": createdate[:10] if createdate else None,
            "has_deal": status is not None,
            "deal_stage": status.get("stage_label") if status else None,
            "deal_name": status.get("deal_name") if status else None,
        })
    # Más recientes primero
    leads_detail.sort(key=lambda r: r["created_date"] or "", reverse=True)
    leads_with_deal = sum(1 for r in leads_detail if r["has_deal"])

    # ---- Progreso mensual y semanal ----
    leads_by_month = build_leads_by_month(leads_by_day, CAMPAIGN_START_DATE)
    weeks = build_week_ranges(CAMPAIGN_START_DATE, today)
    leads_by_week = build_leads_by_week(leads_by_day, weeks)

    data = {
        "updated_at": now.isoformat(),
        "campaign_start": CAMPAIGN_START_DATE,
        "monthly_goal": MONTHLY_GOAL,
        "weekly_goal": WEEKLY_GOAL,
        "closed_deals_goal": CLOSED_DEALS_GOAL,
        "total_leads": total_leads,
        "leads_by_day": leads_by_day,
        "leads_by_month": leads_by_month,
        "leads_by_week": leads_by_week,
        "current_month_leads": current_month_leads,
        "current_month_label": today.strftime("%B %Y").capitalize(),
        "current_week_leads": current_week_leads,
        "closed_deals": closed_deals,
        "leads_with_deal": leads_with_deal,
        "leads_detail": leads_detail,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(
        f"OK: {total_leads} leads totales, {current_month_leads} este mes, "
        f"{current_week_leads} esta semana, {leads_with_deal} con negocio creado."
    )


if __name__ == "__main__":
    main()
