#!/usr/bin/env python3
"""
Lee el gasto (inversión), las creatividades activas, el detalle semanal
(gasto, frecuencia) y los hitos de cambios de campaña desde Meta Ads,
y genera meta_data.json para el dashboard.

También arma el "Análisis de Campaña": rendimiento (CPL, CTR, impresiones,
inversión, frecuencia, alcance, CPM) a nivel de Campaña, Conjunto de
anuncios y Anuncio — con detalle día a día para poder filtrar por
cualquier rango de fechas y por campaña directamente en el dashboard — y,
si está disponible, la atribución de cada registro (de qué anuncio y
conjunto de anuncios salió), usando la API de "Leads Retrieval" de Meta.

Se ejecuta automáticamente vía GitHub Actions, junto con fetch_hubspot.py.
Este script YA NO depende de leer data.json: la tipificación por conjunto
de anuncios se cruza directamente en el navegador (index.html), combinando
data.json (HubSpot) con la atribución que este script deja en meta_data.json.
"""
import os
import re
import sys
import json
import unicodedata
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
# SOLO para saber de qué anuncio/conjunto de anuncios salió cada registro
# (opcional — si no se configura, el resto del dashboard sigue funcionando igual).
FACEBOOK_PAGE_ID = os.environ.get("FACEBOOK_PAGE_ID", "")

META_TOKEN = os.environ.get("META_TOKEN")
API_VERSION = "v21.0"
API_BASE = f"https://graph.facebook.com/{API_VERSION}"

LEAD_ACTION_TYPES = ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead")
# Pistas (sin tildes, solo letras) para reconocer la pregunta de correo en
# CUALQUIER formulario, sin importar cómo la haya nombrado quien lo creó
# (ej. "work_email", "correo_electrónico_del_trabajo", "email_de_contacto").
EMAIL_FIELD_HINTS = ("email", "correo")


def meta_get(path, params, token=None):
    params = dict(params)
    params["access_token"] = token or META_TOKEN
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def meta_get_paginated(path, params, max_pages=30, token=None):
    """Igual que meta_get, pero sigue 'paging.next' hasta max_pages páginas
    y devuelve todos los 'data' concatenados. 'token' opcional para usar un
    token distinto al META_TOKEN global (p.ej. un token de Página)."""
    all_rows = []
    next_url = None
    page = 0
    while page < max_pages:
        if next_url:
            req = urllib.request.Request(next_url, method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        else:
            data = meta_get(path, params, token=token)
        all_rows.extend(data.get("data", []))
        next_url = (data.get("paging") or {}).get("next")
        page += 1
        if not next_url:
            break
    return all_rows


def fetch_page_access_token(page_id):
    """Intercambia el META_TOKEN (de usuario/usuario del sistema) por un
    token de acceso a la Página — requerido por la API de Leads Retrieval
    (leadgen_forms/leads exige un Page Access Token, no un token normal).
    Requiere que META_TOKEN tenga acceso de administrador a la Página vía
    el Business Manager (permisos pages_show_list y pages_manage_ads)."""
    resp = meta_get(f"/{page_id}", {"fields": "access_token"})
    return resp.get("access_token")


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
    de cada anuncio individual en TODO el período, combinado con su texto
    real (título, texto principal, descripción) e imagen — para la tabla
    "Rendimiento de cada anuncio". Si algo falla, devuelve una lista vacía en
    vez de romper el resto del script.
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
    Rendimiento agregado de TODO el período a nivel de Campaña o Conjunto de
    anuncios (gasto, impresiones, alcance, frecuencia, CPM, clics, CTR,
    leads, CPL). 'level' es 'campaign' o 'adset'. Esta es la versión "exacta"
    (una sola consulta a Meta, sin sumar días) — se usa como valor por
    defecto en "Todo el período", donde el alcance y la frecuencia son
    precisos. Best-effort: si falla, devuelve lista vacía sin romper el resto.
    """
    try:
        # Si name_field ya ES "campaign_name" (level="campaign"), no lo repetimos:
        # Meta rechaza con error 2500 un campo pedido dos veces en la misma consulta.
        extra_name = "" if name_field == "campaign_name" else "campaign_name,"
        fields = (
            f"{id_field},{name_field},{extra_name}"
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


def fetch_level_performance_daily(level, since_date, until_date, name_field, id_field, extra_fields=""):
    """
    Igual que fetch_level_performance, pero día a día (time_increment=1), sin
    calcular CTR/CPM/frecuencia/CPL todavía — esos se recalculan en el
    navegador después de sumar el rango de fechas que la persona elija (sumar
    gasto/impresiones/clics/alcance/leads es válido; sumar CTR/CPM/frecuencia
    ya calculados NO lo es). Devuelve filas "en bruto": id, nombre, campaña
    (y conjunto de anuncios si aplica), fecha, spend, impressions, clicks,
    reach, leads. Best-effort: si falla, devuelve lista vacía sin romper el
    resto del dashboard — la vista "Todo el período" sigue funcionando con
    fetch_level_performance.
    """
    try:
        # Mismo cuidado que en fetch_level_performance: no repetir campaign_name
        # cuando name_field ya es "campaign_name" (level="campaign"), porque
        # Meta rechaza un campo pedido dos veces en la misma consulta.
        extra_name = "" if name_field == "campaign_name" else "campaign_name,"
        fields = f"{id_field},{name_field},{extra_name}{extra_fields}spend,impressions,clicks,reach,actions"
        rows = meta_get_paginated(
            f"/{AD_ACCOUNT_ID}/insights",
            {
                "level": level,
                "fields": fields,
                "time_increment": 1,
                "time_range": json.dumps({"since": since_date, "until": until_date}),
                "limit": 500,
            },
            max_pages=60,
        )
    except Exception as e:
        print(f"AVISO: no se pudo traer el detalle diario por {level}: {e}", file=sys.stderr)
        return [], False

    result = []
    for row in rows:
        entry = {
            "id": row.get(id_field),
            "name": row.get(name_field) or "(sin nombre)",
            "campaign_name": row.get("campaign_name") or "",
            "date": row.get("date_start"),
            "spend": round(float(row.get("spend", 0)), 2),
            "impressions": int(float(row.get("impressions", 0))),
            "reach": int(float(row.get("reach", 0))),
            "clicks": int(float(row.get("clicks", 0))),
            "leads": extract_leads(row.get("actions")),
        }
        if level == "ad":
            entry["adset_id"] = row.get("adset_id")
            entry["adset_name"] = row.get("adset_name") or ""
        result.append(entry)
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


# ---------------------- Atribución: de qué anuncio/conjunto salió cada registro ----------------------

def fetch_id_name_map(edge, fields="id,name"):
    """{id: name} de un edge de la cuenta (adsets, ads, campaigns). Best-effort."""
    try:
        rows = meta_get_paginated(f"/{AD_ACCOUNT_ID}/{edge}", {"fields": fields, "limit": 500})
        return {r["id"]: r.get("name", "") for r in rows}
    except Exception as e:
        print(f"AVISO: no se pudo traer el nombre de {edge}: {e}", file=sys.stderr)
        return {}


def _normalize_field_name(name):
    """Quita tildes/ñ y cualquier caracter que no sea letra, para poder
    reconocer el nombre del campo sin importar tildes, guiones o guiones
    bajos (ej. 'correo_electrónico_del_trabajo' -> 'correoelectronicodeltrabajo')."""
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", name.lower())


def extract_field(field_data, hints):
    """Busca en field_data (formato de Meta Lead Ads: [{name, values:[...]}]) el
    primer campo cuyo nombre normalizado CONTENGA alguna de las 'hints'
    (ej. 'email' o 'correo'), en vez de exigir una coincidencia exacta —
    así reconoce variantes como 'work_email' o 'correo_electrónico_del_trabajo'
    que no son iguales a una lista fija de nombres."""
    for f in field_data or []:
        name = _normalize_field_name(f.get("name") or "")
        if any(hint in name for hint in hints):
            values = f.get("values") or []
            if values:
                return str(values[0]).strip().lower()
    return None


def fetch_leads_attribution_raw():
    """
    Trae, para cada lead recibido en los formularios de la Página, el
    ad_id/adset_id/campaign_id del que salió — usando la API de "Leads
    Retrieval" de Meta (requiere el permiso leads_retrieval sobre la Página
    conectada al formulario, y la variable FACEBOOK_PAGE_ID configurada).
    Devuelve {email_en_minuscula: {ad_id, adset_id, campaign_id}}.

    Best-effort en cada paso: si falta el permiso, la variable de entorno, o
    cualquier llamada falla, devuelve un mapa vacío y available=False — el
    resto del dashboard sigue funcionando igual, solo no se muestra de qué
    anuncio/conjunto de anuncios salió cada registro.
    """
    if not FACEBOOK_PAGE_ID:
        print("AVISO: falta FACEBOOK_PAGE_ID — se omite la atribución de registros por anuncio/conjunto.", file=sys.stderr)
        return {}, False

    try:
        page_token = fetch_page_access_token(FACEBOOK_PAGE_ID)
    except Exception as e:
        print(f"AVISO: no se pudo obtener el token de acceso de la Página (¿falta pages_show_list o el token no administra esta Página?): {e}", file=sys.stderr)
        return {}, False

    if not page_token:
        print("AVISO: la Página no devolvió un token de acceso — probablemente falta el permiso pages_manage_ads.", file=sys.stderr)
        return {}, False

    try:
        forms = meta_get_paginated(f"/{FACEBOOK_PAGE_ID}/leadgen_forms", {"fields": "id,name", "limit": 100}, token=page_token)
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
                max_pages=15,
                token=page_token,
            )
            any_ok = True
        except Exception as e:
            print(f"AVISO: no se pudieron traer los leads del formulario {form.get('name', form_id)}: {e}", file=sys.stderr)
            continue

        for lead in leads:
            email = extract_field(lead.get("field_data"), EMAIL_FIELD_HINTS)
            if not email:
                continue
            attribution[email] = {
                "ad_id": lead.get("ad_id"),
                "adset_id": lead.get("adset_id"),
                "campaign_id": lead.get("campaign_id"),
            }

    return attribution, any_ok


def build_leads_attribution(attribution_raw, adset_names, ad_names, campaign_names):
    """Resuelve ad_id/adset_id/campaign_id a sus nombres reales, para que
    index.html pueda mostrarlos directamente junto a cada registro."""
    resolved = {}
    for email, attr in attribution_raw.items():
        resolved[email] = {
            "ad_name": ad_names.get(attr.get("ad_id"), attr.get("ad_id") or ""),
            "adset_name": adset_names.get(attr.get("adset_id"), attr.get("adset_id") or ""),
            "campaign_name": campaign_names.get(attr.get("campaign_id"), attr.get("campaign_id") or ""),
        }
    return resolved


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

    # ---- Análisis de campaña: "Todo el período" (exacto) + detalle diario (para filtrar por fecha) ----
    campaigns_performance, campaigns_performance_available = fetch_level_performance(
        "campaign", CAMPAIGN_START_DATE, today_str, "campaign_name", "campaign_id"
    )
    adsets_performance, adsets_performance_available = fetch_level_performance(
        "adset", CAMPAIGN_START_DATE, today_str, "adset_name", "adset_id"
    )
    campaigns_daily, campaigns_daily_available = fetch_level_performance_daily(
        "campaign", CAMPAIGN_START_DATE, today_str, "campaign_name", "campaign_id"
    )
    adsets_daily, adsets_daily_available = fetch_level_performance_daily(
        "adset", CAMPAIGN_START_DATE, today_str, "adset_name", "adset_id"
    )
    ads_daily, ads_daily_available = fetch_level_performance_daily(
        "ad", CAMPAIGN_START_DATE, today_str, "ad_name", "ad_id", extra_fields="adset_id,adset_name,"
    )

    # ---- Atribución: de qué anuncio/conjunto de anuncios salió cada registro (opcional) ----
    attribution_raw, attribution_available = fetch_leads_attribution_raw()
    if attribution_available:
        adset_names = fetch_id_name_map("adsets")
        ad_names = fetch_id_name_map("ads")
        campaign_names = fetch_id_name_map("campaigns")
        leads_attribution = build_leads_attribution(attribution_raw, adset_names, ad_names, campaign_names)
    else:
        leads_attribution = {}

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
        # Análisis de campaña (por Campaña / Conjunto de anuncios / Anuncio)
        "campaigns_performance_available": campaigns_performance_available,
        "campaigns_performance": campaigns_performance,
        "adsets_performance_available": adsets_performance_available,
        "adsets_performance": adsets_performance,
        "campaign_analysis_daily_available": campaigns_daily_available and adsets_daily_available and ads_daily_available,
        "campaign_analysis_daily": {
            "campaigns": campaigns_daily,
            "adsets": adsets_daily,
            "ads": ads_daily,
        },
        # De qué anuncio/conjunto de anuncios salió cada registro (por email)
        "leads_attribution_available": attribution_available,
        "leads_attribution": leads_attribution,
    }

    with open("meta_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(
        f"OK: gasto total S/{total_spend:.2f}, este mes S/{month_spend:.2f}, "
        f"{len(leads_creatives)} creatividades de leads, {len(milestones)} hitos, "
        f"{len(campaigns_performance)} campañas, {len(adsets_performance)} conjuntos de anuncios, "
        f"atribución de registros: {'disponible (' + str(len(leads_attribution)) + ')' if attribution_available else 'no disponible'}"
    )


if __name__ == "__main__":
    main()
