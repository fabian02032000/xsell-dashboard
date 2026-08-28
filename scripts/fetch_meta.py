#!/usr/bin/env python3
"""
Lee el gasto (inversión), las creatividades activas, el detalle semanal
(gasto, frecuencia) y los hitos de cambios de campaña desde Meta Ads,
y genera meta_data.json para el dashboard.

También arma el "Análisis de Campaña": rendimiento (CPL, CTR, impresiones,
inversión, frecuencia, alcance, CPM) a nivel de Campaña, Conjunto de
anuncios y Anuncio — y, si está disponible, cruza la tipificación de cada
registro (Descartada / Contactado / Reunión Agendada / Sin negocio, según
HubSpot) con el conjunto de anuncios del que salió, usando la API de
"Leads Retrieval" de Meta.

Se ejecuta automáticamente vía GitHub Actions, junto con fetch_hubspot.py
(que debe correr ANTES que este script, porque este lee data.json ya
generado para cruzar la tipificación por conjunto de anuncios).
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
# ID de la página de Facebook conectada al formulario de leads. Necesario
# SOLO para cruzar la tipificación por conjunto de anuncios (opcional — si no
# se configura, el resto del dashboard sigue funcionando igual).
FACEBOOK_PAGE_ID = os.environ.get("FACEBOOK_PAGE_ID", "")

META_TOKEN = os.environ.get("META_TOKEN")
API_VERSION = "v21.0"
API_BASE = f"https://graph.facebook.com/{API_VERSION}"

LEAD_ACTION_TYPES = ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead")
EMAIL_FIELD_KEYS = ("email", "correo", "correo_electronico", "correo electrónico", "e-mail")


def meta_get(path, params):
    params = dict(params)
    params["access_token"] = META_TOKEN
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def meta_get_paginated(path, params, max_pages=20):
    """Igual que meta_get, pero sigue 'paging.next' hasta max_pages páginas
    y devuelve todos los 'data' concatenados."""
    all_rows = []
    next_url = None
    page = 0
    while page < max_pages:
        if next_url:
            req = urllib.request.Request(next_url, method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        else:
            data = meta_get(path, params)
        all_rows.extend(data.get("data", []))
        next_url = (data.get("paging") or {}).get("next")
        page += 1
        if not next_url:
            break
    return all_rows


def extract_leads(actions):
    if not actions:
        return 0
    for a in actions:
        if a.get("action_type") in LEAD_ACTION_TYPES:
            return int(float(a.get("value", 0)))
    return 0


def extract_cost_per_lead(cost_per_action_type, spend, leads):
    if cost_per_action_type:
        for c in cost_per_action_type:
            if c.get("action_type") in LEAD_ACTION_TYPES:
                return round(float(c.get("value", 0)), 2)
    if leads:
        return round(spend / leads, 2)
    return None


def fetch_campaign_totals(since_date, until_date):
    """Gasto total por campaña, en el rango dado (para el resumen de gasto por campaña)."""
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


def extract_link_description(creative):
    """
    La descripción corta que aparece debajo del título en un anuncio de enlace
    no viene como un campo directo del creative — Meta la guarda dentro de
    object_story_spec.link_data.description. Si el anuncio no tiene esa
    estructura (por ejemplo, es un anuncio de otro formato), devuelve "".
    """
    story = creative.get("object_story_spec") or {}
    link_data = story.get("link_data") or {}
    return link_data.get("description") or link_data.get("caption") or ""


def fetch_ad_creatives_map():
    """
    Trae el texto real de cada anuncio (título, texto principal, descripción)
    y su imagen, para poder mostrarlo junto a su rendimiento. Si falla, devuelve
    un diccionario vacío y el resto del script sigue funcionando igual.
    """
    try:
        data = meta_get(
            f"/{AD_ACCOUNT_ID}/ads",
            {
                "fields": "id,name,campaign{name},creative{title,body,image_url,thumbnail_url,object_story_spec}",
                "limit": 500,
            },
        )
    except Exception as e:
        print(f"AVISO: no se pudo traer el texto de los anuncios: {e}", file=sys.stderr)
        return {}, False

    creatives_map = {}
    for ad in data.get("data", []):
        creative = ad.get("creative") or {}
        creatives_map[ad.get("id")] = {
            "ad_name": ad.get("name"),
            "campaign_name": (ad.get("campaign") or {}).get("name", ""),
            "title": creative.get("title") or "",
            "body": creative.get("body") or "",
            "description": extract_link_description(creative),
            "image_url": creative.get("image_url") or creative.get("thumbnail_url") or "",
        }
    return creatives_map, True


def fetch_ads_performance(since_date, until_date):
    """
    Rendimiento (gasto, impresiones, alcance, frecuencia, CPM, clics, leads)
    de cada anuncio individual, combinado con su texto real (título, texto
    principal, descripción) e imagen, y con el conjunto de anuncios/campaña
    al que pertenece — para el "Análisis de Campaña" y el reporte de "cómo
    va cada anuncio". Si algo falla, devuelve una lista vacía en vez de
    romper el resto del script.
    """
    creatives_map, creatives_ok = fetch_ad_creatives_map()
    if not creatives_ok:
        return [], False

    try:
        data = meta_get(
            f"/{AD_ACCOUNT_ID}/insights",
            {
                "level": "ad",
                "fields": (
                    "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,"
                    "spend,impressions,clicks,ctr,cpm,reach,frequency,actions,cost_per_action_type"
                ),
                "time_range": json.dumps({"since": since_date, "until": until_date}),
                "limit": 500,
            },
        )
    except Exception as e:
        print(f"AVISO: no se pudo traer el rendimiento por anuncio: {e}", file=sys.stderr)
        return [], False

    result = []
    for row in data.get("data", []):
        ad_id = row.get("ad_id")
        extra = creatives_map.get(ad_id, {})
        leads = extract_leads(row.get("actions"))
        spend = round(float(row.get("spend", 0)), 2)
        cpl = extract_cost_per_lead(row.get("cost_per_action_type"), spend, leads)
        result.append({
            "ad_id": ad_id,
            "ad_name": row.get("ad_name") or extra.get("ad_name") or "(sin nombre)",
            "adset_id": row.get("adset_id"),
            "adset_name": row.get("adset_name") or "",
            "campaign_id": row.get("campaign_id"),
            "campaign_name": row.get("campaign_name") or extra.get("campaign_name") or "",
            "title": extra.get("title", ""),
            "body": extra.get("body", ""),
            "description": extra.get("description", ""),
            "image_url": extra.get("image_url", ""),
            "spend": spend,
            "impressions": int(float(row.get("impressions", 0))),
            "reach": int(float(row.get("reach", 0))),
            "frequency": round(float(row.get("frequency", 0)), 2),
            "cpm": round(float(row.get("cpm", 0)), 2),
            "clicks": int(float(row.get("clicks", 0))),
            "ctr": round(float(row.get("ctr", 0)), 2),
            "leads": leads,
            "cost_per_lead": cpl,
        })

    result.sort(key=lambda r: r["spend"], reverse=True)
    return result, True


def fetch_level_performance(level, since_date, until_date, name_field, id_field):
    """
    Rendimiento agregado a nivel de Campaña o Conjunto de anuncios (mismas
    métricas que a nivel de anuncio: gasto, impresiones, alcance, frecuencia,
    CPM, clics, CTR, leads, CPL). 'level' es 'campaign' o 'adset'.
    Best-effort: si falla, devuelve lista vacía sin romper el resto.
    """
    try:
        fields = (
            f"{id_field},{name_field},campaign_name,"
            "spend,impressions,clicks,ctr,cpm,reach,frequency,actions,cost_per_action_type"
        )
        data = meta_get(
            f"/{AD_ACCOUNT_ID}/insights",
            {
                "level": level,
                "fields": fields,
                "time_range": json.dumps({"since": since_date, "until": until_date}),
                "limit": 500,
            },
        )
    except Exception as e:
        print(f"AVISO: no se pudo traer el rendimiento por {level}: {e}", file=sys.stderr)
        return [], False

    result = []
    for row in data.get("data", []):
        leads = extract_leads(row.get("actions"))
        spend = round(float(row.get("spend", 0)), 2)
        cpl = extract_cost_per_lead(row.get("cost_per_action_type"), spend, leads)
        result.append({
            "id": row.get(id_field),
            "name": row.get(name_field) or "(sin nombre)",
            "campaign_name": row.get("campaign_name") or "",
            "spend": spend,
            "impressions": int(float(row.get("impressions", 0))),
            "reach": int(float(row.get("reach", 0))),
            "frequency": round(float(row.get("frequency", 0)), 2),
            "cpm": round(float(row.get("cpm", 0)), 2),
            "clicks": int(float(row.get("clicks", 0))),
            "ctr": round(float(row.get("ctr", 0)), 2),
            "leads": leads,
            "cost_per_lead": cpl,
        })
    result.sort(key=lambda r: r["spend"], reverse=True)
    return result, True


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
                "fields": "name,effective_status,campaign{name},creative{image_url,thumbnail_url,name,title,body,object_story_spec}",
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
            # Texto real del anuncio (título, texto principal, descripción), para
            # mostrarlo directamente junto a la imagen sin tener que hacer clic.
            "title": creative.get("title") or "",
            "body": creative.get("body") or "",
            "description": extract_link_description(creative),
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


# ---------------------- Atribución: tipificación por conjunto de anuncios ----------------------

def fetch_adset_name_map():
    """{adset_id: adset_name} de toda la cuenta. Best-effort."""
    try:
        rows = meta_get_paginated(f"/{AD_ACCOUNT_ID}/adsets", {"fields": "id,name", "limit": 200})
        return {r["id"]: r.get("name", "") for r in rows}
    except Exception as e:
        print(f"AVISO: no se pudo traer el nombre de los conjuntos de anuncios: {e}", file=sys.stderr)
        return {}


def extract_field(field_data, keys):
    """Busca en field_data (formato de Meta Lead Ads: [{name, values:[...]}]) el
    primer campo cuyo nombre (en minúsculas) esté en 'keys'."""
    for f in field_data or []:
        name = (f.get("name") or "").strip().lower()
        if name in keys:
            values = f.get("values") or []
            if values:
                return str(values[0]).strip().lower()
    return None


def fetch_leads_attribution():
    """
    Cruza cada lead con el anuncio/conjunto de anuncios/campaña del que salió,
    usando la API de "Leads Retrieval" de Meta (requiere el permiso
    leads_retrieval sobre la Página conectada al formulario, y la variable
    FACEBOOK_PAGE_ID configurada). Devuelve {email_en_minuscula: {...}}.

    Best-effort en cada paso: si falta el permiso, la variable de entorno, o
    cualquier llamada falla, devuelve un mapa vacío y available=False — el
    resto del dashboard sigue funcionando igual, solo no se muestra el cruce
    de tipificación por conjunto de anuncios.
    """
    if not FACEBOOK_PAGE_ID:
        print("AVISO: falta FACEBOOK_PAGE_ID — se omite el cruce de tipificación por conjunto de anuncios.", file=sys.stderr)
        return {}, False

    try:
        forms = meta_get_paginated(f"/{FACEBOOK_PAGE_ID}/leadgen_forms", {"fields": "id,name", "limit": 100})
    except Exception as e:
        print(f"AVISO: no se pudieron traer los formularios de leads (¿falta el permiso leads_retrieval?): {e}", file=sys.stderr)
        return {}, False

    if not forms:
        print("AVISO: la página no tiene formularios de leads visibles con este token.", file=sys.stderr)
        return {}, False

    attribution = {}
    any_ok = False
    for form in forms:
        form_id = form.get("id")
        try:
            leads = meta_get_paginated(
                f"/{form_id}/leads",
                {"fields": "id,created_time,ad_id,adset_id,campaign_id,field_data", "limit": 200},
                max_pages=10,
            )
            any_ok = True
        except Exception as e:
            print(f"AVISO: no se pudieron traer los leads del formulario {form.get('name', form_id)}: {e}", file=sys.stderr)
            continue

        for lead in leads:
            email = extract_field(lead.get("field_data"), EMAIL_FIELD_KEYS)
            if not email:
                continue
            attribution[email] = {
                "ad_id": lead.get("ad_id"),
                "adset_id": lead.get("adset_id"),
                "campaign_id": lead.get("campaign_id"),
            }

    return attribution, any_ok


def build_leads_status_by_adset(attribution, adset_names):
    """
    Lee data.json (ya generado por fetch_hubspot.py en esta misma corrida) y
    cruza cada lead con su conjunto de anuncios (por email), agrupando la
    tipificación (Descartada / Contactado / Reunión Agendada / Sin negocio)
    por conjunto de anuncios. Best-effort: si data.json no existe todavía o
    algo falla, devuelve lista vacía sin romper el resto.
    """
    if not attribution:
        return [], False
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            leads_data = json.load(f)
    except Exception as e:
        print(f"AVISO: no se pudo leer data.json para cruzar tipificación por conjunto de anuncios: {e}", file=sys.stderr)
        return [], False

    counts = {}  # adset_name -> {status_label: count}
    matched = 0
    for lead in leads_data.get("leads_detail", []):
        email = (lead.get("email") or "").strip().lower()
        attr = attribution.get(email)
        if not attr:
            continue
        adset_id = attr.get("adset_id")
        adset_name = adset_names.get(adset_id, adset_id or "(conjunto desconocido)")
        status_label = lead.get("deal_stage") if lead.get("has_deal") and lead.get("deal_stage") else "Sin negocio"
        counts.setdefault(adset_name, {})
        counts[adset_name][status_label] = counts[adset_name].get(status_label, 0) + 1
        matched += 1

    result = [
        {"adset_name": name, "status_counts": statuses, "total": sum(statuses.values())}
        for name, statuses in counts.items()
    ]
    result.sort(key=lambda r: r["total"], reverse=True)
    print(f"Cruce de tipificación por conjunto de anuncios: {matched} registros con atribución encontrada.")
    return result, True


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

    ads_performance, ads_performance_available = fetch_ads_performance(CAMPAIGN_START_DATE, today_str)
    campaigns_performance, campaigns_performance_available = fetch_level_performance(
        "campaign", CAMPAIGN_START_DATE, today_str, "campaign_name", "campaign_id"
    )
    adsets_performance, adsets_performance_available = fetch_level_performance(
        "adset", CAMPAIGN_START_DATE, today_str, "adset_name", "adset_id"
    )

    # ---- Atribución: tipificación por conjunto de anuncios (opcional) ----
    attribution, attribution_available = fetch_leads_attribution()
    adset_names = fetch_adset_name_map() if attribution_available else {}
    leads_status_by_adset, leads_status_by_adset_available = build_leads_status_by_adset(attribution, adset_names)

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
        "campaigns_month": [
            {"name": c.get("campaign_name"), "spend": round(float(c.get("spend", 0)), 2)}
            for c in campaigns_month
        ],
        "daily_spend": daily,
        "spend_by_week": spend_by_week,
        "creatives_available": creatives_available,
        "leads_creatives": leads_creatives,
        "other_creatives": other_creatives,
        "milestones_available": milestones_available,
        "milestones": milestones,
        "ads_performance_available": ads_performance_available,
        "ads_performance": ads_performance,
        "campaigns_performance_available": campaigns_performance_available,
        "campaigns_performance": campaigns_performance,
        "adsets_performance_available": adsets_performance_available,
        "adsets_performance": adsets_performance,
        "leads_status_by_adset_available": leads_status_by_adset_available,
        "leads_status_by_adset": leads_status_by_adset,
    }

    with open("meta_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(
        f"OK: gasto total S/{total_spend:.2f}, este mes S/{month_spend:.2f}, "
        f"{len(leads_creatives)} creatividades de leads, {len(milestones)} hitos, "
        f"{len(campaigns_performance)} campañas, {len(adsets_performance)} conjuntos de anuncios analizados"
    )


if __name__ == "__main__":
    main()
