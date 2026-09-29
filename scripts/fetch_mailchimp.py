#!/usr/bin/env python3
"""
Lee las campañas de correo (email marketing) enviadas desde Mailchimp y
genera mailchimp_data.json para la pestaña "Email marketing" del dashboard.
Se ejecuta automáticamente vía GitHub Actions, junto con fetch_hubspot.py,
fetch_meta.py y fetch_organic.py.

Necesita un único secreto: MAILCHIMP_API_KEY (ver README.md, sección
"Conectar Mailchimp"). El "server prefix" de la cuenta (por ejemplo "us18")
no hay que configurarlo aparte: viene incluido al final del propio API Key
(después del último guion), así que este script lo saca solo de ahí.

Para cada campaña de correo ya enviada trae sus KPIs (aperturas, clics,
rebotes, bajas) y el promedio de la industria que reporta el propio
Mailchimp, para poder comparar. Además guarda el HTML completo del correo
más reciente, para mostrar el "cuerpo del correo" tal cual se envió.
"""
import os
import sys
import json
import base64
import datetime
import urllib.request
import urllib.parse
import urllib.error

# ---------------------- CONFIGURACIÓN ----------------------
MAILCHIMP_API_KEY = os.environ.get("MAILCHIMP_API_KEY", "").strip()
# Cuántas campañas recientes se traen con detalle (KPIs). Mailchimp permite
# hasta 1000 por página; con 60 sobra por muchos años de envíos mensuales.
MAX_CAMPAIGNS = int(os.environ.get("MAILCHIMP_MAX_CAMPAIGNS", "60"))
OUTPUT_FILE = "mailchimp_data.json"


def empty_result(error=None):
    return {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "available": False,
        "error": error,
        "summary": {},
        "campaigns": [],
        "latest_campaign_html": None,
    }


def mc_get(base_url, path, params=None):
    params = params or {}
    url = f"{base_url}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    token = base64.b64encode(f"xsell:{MAILCHIMP_API_KEY}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def safe_get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur if cur is not None else default


def main():
    if not MAILCHIMP_API_KEY:
        data = empty_result(
            "Todavía no se conectó Mailchimp — falta agregar el secreto "
            "MAILCHIMP_API_KEY en el repositorio (ver README.md, sección "
            "\"Conectar Mailchimp\")."
        )
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("AVISO: MAILCHIMP_API_KEY no está configurado — mailchimp_data.json quedó vacío.")
        return

    if "-" not in MAILCHIMP_API_KEY:
        data = empty_result(
            "El API Key de Mailchimp no tiene el formato esperado (debe terminar en "
            "algo como \"-us18\"). Revisa que se haya copiado completo."
        )
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("ERROR: MAILCHIMP_API_KEY con formato inválido.", file=sys.stderr)
        return

    dc = MAILCHIMP_API_KEY.rsplit("-", 1)[-1]
    base_url = f"https://{dc}.api.mailchimp.com/3.0"

    try:
        campaigns_resp = mc_get(base_url, "/campaigns", {
            "status": "sent",
            "count": MAX_CAMPAIGNS,
            "sort_field": "send_time",
            "sort_dir": "DESC",
            "fields": ",".join([
                "campaigns.id",
                "campaigns.settings.subject_line",
                "campaigns.settings.preview_text",
                "campaigns.send_time",
                "campaigns.emails_sent",
                "campaigns.recipients.list_name",
                "campaigns.recipients.segment_text",
                "campaigns.recipients.recipient_count",
                "campaigns.archive_url",
                "campaigns.long_archive_url",
            ]),
        })
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        data = empty_result(
            f"Mailchimp devolvió un error al pedir la lista de campañas ({e.code}). "
            "Revisa que el API Key siga siendo válido."
        )
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"ERROR HTTP {e.code} al listar campañas: {body}", file=sys.stderr)
        return
    except Exception as e:
        data = empty_result(f"No se pudo conectar con Mailchimp: {e}")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"ERROR: {e}", file=sys.stderr)
        return

    raw_campaigns = campaigns_resp.get("campaigns", [])

    campaigns = []
    for c in raw_campaigns:
        cid = c.get("id")
        entry = {
            "id": cid,
            "subject": safe_get(c, "settings", "subject_line", default=""),
            "preview_text": safe_get(c, "settings", "preview_text", default=""),
            "send_time": c.get("send_time"),
            "recipients": safe_get(c, "recipients", "recipient_count", default=c.get("emails_sent", 0)),
            "list_name": safe_get(c, "recipients", "list_name", default=""),
            "segment_text": strip_html(safe_get(c, "recipients", "segment_text", default="")),
            "archive_url": c.get("long_archive_url") or c.get("archive_url"),
            "opens_total": None, "unique_opens": None, "open_rate": None,
            "clicks_total": None, "unique_clicks": None, "click_rate": None,
            "hard_bounces": 0, "soft_bounces": 0, "unsubscribes": 0,
            "industry_open_rate": None, "industry_click_rate": None,
        }

        try:
            report = mc_get(base_url, f"/reports/{cid}", {
                "fields": ",".join([
                    "opens.opens_total", "opens.unique_opens", "opens.open_rate",
                    "clicks.clicks_total", "clicks.unique_clicks", "clicks.click_rate",
                    "bounces.hard_bounces", "bounces.soft_bounces",
                    "unsubscribed", "industry_stats.open_rate", "industry_stats.click_rate",
                ]),
            })
            entry["opens_total"] = safe_get(report, "opens", "opens_total")
            entry["unique_opens"] = safe_get(report, "opens", "unique_opens")
            entry["open_rate"] = safe_get(report, "opens", "open_rate")
            entry["clicks_total"] = safe_get(report, "clicks", "clicks_total")
            entry["unique_clicks"] = safe_get(report, "clicks", "unique_clicks")
            entry["click_rate"] = safe_get(report, "clicks", "click_rate")
            entry["hard_bounces"] = safe_get(report, "bounces", "hard_bounces", default=0)
            entry["soft_bounces"] = safe_get(report, "bounces", "soft_bounces", default=0)
            entry["unsubscribes"] = report.get("unsubscribed", 0)
            entry["industry_open_rate"] = safe_get(report, "industry_stats", "open_rate")
            entry["industry_click_rate"] = safe_get(report, "industry_stats", "click_rate")
        except Exception as e:
            print(f"AVISO: no se pudo traer el reporte de la campaña {cid}: {e}", file=sys.stderr)

        campaigns.append(entry)

    # Resumen: promedio simple de tasas entre campañas con dato, y sumas para
    # lo que sí tiene sentido sumar (destinatarios, rebotes, bajas).
    open_rates = [c["open_rate"] for c in campaigns if c["open_rate"] is not None]
    click_rates = [c["click_rate"] for c in campaigns if c["click_rate"] is not None]
    industry_open = [c["industry_open_rate"] for c in campaigns if c["industry_open_rate"]]
    industry_click = [c["industry_click_rate"] for c in campaigns if c["industry_click_rate"]]

    summary = {
        "total_campaigns": len(campaigns),
        "total_recipients_all": sum(c["recipients"] or 0 for c in campaigns),
        "avg_open_rate": (sum(open_rates) / len(open_rates)) if open_rates else None,
        "avg_click_rate": (sum(click_rates) / len(click_rates)) if click_rates else None,
        "total_bounces": sum((c["hard_bounces"] or 0) + (c["soft_bounces"] or 0) for c in campaigns),
        "total_unsubscribes": sum(c["unsubscribes"] or 0 for c in campaigns),
        "industry_avg_open_rate": (sum(industry_open) / len(industry_open)) if industry_open else None,
        "industry_avg_click_rate": (sum(industry_click) / len(industry_click)) if industry_click else None,
    }

    # Cuerpo del correo más reciente, para mostrarlo tal cual se envió.
    latest_html = None
    if campaigns:
        latest_id = campaigns[0]["id"]
        try:
            content = mc_get(base_url, f"/campaigns/{latest_id}/content", {"fields": "html"})
            latest_html = content.get("html")
        except Exception as e:
            print(f"AVISO: no se pudo traer el HTML del correo más reciente: {e}", file=sys.stderr)

    data = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "available": True,
        "error": None,
        "summary": summary,
        "campaigns": campaigns,
        "latest_campaign_html": latest_html,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(campaigns)} campañas de correo traídas de Mailchimp.")


def strip_html(text):
    """Quita etiquetas HTML simples que Mailchimp a veces incluye en segment_text."""
    import re
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


if __name__ == "__main__":
    main()
