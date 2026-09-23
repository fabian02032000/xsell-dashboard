#!/usr/bin/env python3
"""
Lee, desde HubSpot, los correos de prospección en frío que Ingrid envía
uno por uno desde su cuenta (el envío "insight" que menciona el resumen del
dashboard) y genera emails_data.json.

Cada correo que Ingrid manda queda guardado en HubSpot como un "engagement"
de tipo Email (el mismo tipo de registro que se ve en la línea de tiempo de
un contacto). Ese registro ya trae, sin que nadie tenga que anotarlo a mano:
asunto, destinatario, fecha y hora exactas, si se abrió (y cuántas veces),
si hubo clic, si hubo respuesta, y si rebotó o falló el envío.

No requiere conocimientos de programación para usarlo: corre solo, vía
GitHub Actions, junto con fetch_hubspot.py.

AVISO DE PRIVACIDAD: igual que data.json, este archivo incluye el correo (y
nombre, si HubSpot lo tiene) de cada destinatario, y queda visible en un
repositorio público de GitHub. Es la misma decisión que ya se tomó para los
registros de la campaña de Meta — si en algún momento se prefiere dejar de
exponer eso, hay que quitar el bloque "detail" antes de que corra de nuevo.

LIMITACIÓN CONOCIDA: el "rubro" de cada destinatario casi nunca va a estar
disponible. Cuando Ingrid le escribe por primera vez a un correo que no
existía antes en HubSpot, HubSpot crea el contacto automáticamente con solo
el correo (sin rubro, sin empresa, sin nombre). Por eso, cuando no hay
rubro/empresa cargados a mano en el contacto, este script arma una empresa
"adivinada" a partir del dominio del correo (ej. "ventas@nabila.pe" ->
"Nabila"), solo como referencia visual — nunca reemplaza el dato real si
alguien lo completa después en HubSpot.
"""
import os
import sys
import json
import datetime
import urllib.request
import urllib.error

# ---------------------- CONFIGURACIÓN ----------------------
# Correo desde el que Ingrid manda la prospección en frío. Si en algún
# momento alguien más suma correos de este tipo, o Ingrid cambia de correo,
# solo hay que ajustar esta variable de entorno (sin tocar el código).
INGRID_FROM_EMAIL = os.environ.get("INGRID_FROM_EMAIL", "ingrid.mio@3eriza.com.pe")

# Desde qué fecha traer correos (para no pedirle a HubSpot más de lo
# necesario). Ajustable sin tocar el código si hiciera falta ver más atrás.
EMAILS_SINCE_DATE = os.environ.get("EMAILS_SINCE_DATE", "2026-01-01")

# Dominios de correo "genéricos" (Gmail, Hotmail, etc.) — si el destinatario
# usa uno de estos, no tiene sentido adivinar la empresa a partir del
# dominio, así que en ese caso se deja en blanco.
GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "live.com",
    "icloud.com", "msn.com", "yahoo.es", "hotmail.es",
}

# Traducción de los estados de envío de HubSpot al texto que ve Fabián.
EMAIL_STATUS_LABELS = {
    "SENT": "Enviado",
    "BOUNCED": "Rebotado",
    "FAILED": "Fallido",
    "SCHEDULED": "Programado",
    "PROCESSING": "Procesando",
}

HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN")
API_BASE = "https://api.hubapi.com"


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


def chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


EMAIL_PROPERTIES = [
    "hs_timestamp",
    "hs_email_subject",
    "hs_email_to_email",
    "hs_email_status",
    "hs_email_open_count",
    "hs_email_click_count",
    "hs_email_reply_count",
]


def fetch_all_ingrid_emails(since_date_str):
    """Trae todos los correos (engagements de tipo Email) enviados desde el
    correo de Ingrid, desde since_date_str (paginado)."""
    emails = []
    after = None
    since_epoch = date_to_epoch_ms(since_date_str)
    while True:
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "hs_email_from_email", "operator": "EQ", "value": INGRID_FROM_EMAIL},
                        {"propertyName": "hs_email_direction", "operator": "EQ", "value": "EMAIL"},
                        {"propertyName": "hs_timestamp", "operator": "GTE", "value": str(since_epoch)},
                    ]
                }
            ],
            "properties": EMAIL_PROPERTIES,
            "limit": 100,
            "sorts": [{"propertyName": "hs_timestamp", "direction": "DESCENDING"}],
        }
        if after:
            body["after"] = after
        data = hubspot_post("/crm/v3/objects/emails/search", body)
        emails.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return emails


def fetch_contact_by_email_map(email_ids):
    """Para cada correo (engagement), busca el contacto asociado y trae su
    nombre, empresa y rubro/industria. Best-effort: si algo falla, todos
    quedan sin contacto asociado en vez de romper el script completo."""
    contact_by_email_id = {eid: None for eid in email_ids}
    if not email_ids:
        return contact_by_email_id

    try:
        email_to_contact_ids = {}
        for batch in chunked(email_ids, 100):
            body = {"inputs": [{"id": eid} for eid in batch]}
            resp = hubspot_post("/crm/v4/associations/emails/contacts/batch/read", body)
            for row in resp.get("results", []):
                from_id = row.get("from", {}).get("id")
                contact_ids = [t.get("toObjectId") for t in row.get("to", [])]
                if from_id and contact_ids:
                    email_to_contact_ids[from_id] = contact_ids[0]  # un correo, un destinatario principal

        all_contact_ids = sorted({str(cid) for cid in email_to_contact_ids.values()})

        contact_info = {}
        contact_properties = ["firstname", "lastname", "email", "company", "rubro", "industry", "industria"]
        for batch in chunked(all_contact_ids, 100):
            body = {"inputs": [{"id": cid} for cid in batch], "properties": contact_properties}
            resp = hubspot_post("/crm/v3/objects/contacts/batch/read", body)
            for c in resp.get("results", []):
                contact_info[c["id"]] = c.get("properties", {})

        for eid, cid in email_to_contact_ids.items():
            contact_by_email_id[eid] = contact_info.get(str(cid))
    except Exception as e:
        print(f"AVISO: no se pudo revisar el contacto asociado a cada correo: {e}", file=sys.stderr)

    return contact_by_email_id


def guess_company_from_domain(email_address):
    """Adivina un nombre de empresa a partir del dominio del correo, solo
    como referencia visual (ver aviso de limitación arriba). Devuelve None
    si el dominio es de un proveedor genérico (Gmail, Hotmail, etc.)."""
    if not email_address or "@" not in email_address:
        return None
    domain = email_address.split("@", 1)[1].lower().strip()
    if domain in GENERIC_EMAIL_DOMAINS:
        return None
    base = domain.split(".")[0]
    return base.replace("-", " ").replace("_", " ").title()


def build_detail_row(email_obj, contact_props):
    props = email_obj.get("properties", {})
    contact_props = contact_props or {}

    full_name = " ".join(
        x for x in [contact_props.get("firstname"), contact_props.get("lastname")] if x
    ).strip()

    company = contact_props.get("company")
    company_is_guess = False
    if not company:
        company = guess_company_from_domain(props.get("hs_email_to_email"))
        company_is_guess = company is not None

    rubro = contact_props.get("rubro") or contact_props.get("industry") or contact_props.get("industria")

    timestamp = props.get("hs_timestamp")
    sent_date, sent_time = None, None
    if timestamp:
        dt = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        sent_date = dt.date().isoformat()
        sent_time = dt.strftime("%H:%M")

    status_raw = props.get("hs_email_status") or ""
    return {
        "recipient_email": props.get("hs_email_to_email") or "(sin correo)",
        "recipient_name": full_name or None,
        "company": company,
        "company_is_guess": company_is_guess,
        "rubro": rubro,
        "subject": props.get("hs_email_subject") or "(sin asunto)",
        "sent_date": sent_date,
        "sent_time": sent_time,
        "status": EMAIL_STATUS_LABELS.get(status_raw, status_raw or "Sin dato"),
        "status_raw": status_raw,
        "opens": int(float(props.get("hs_email_open_count") or 0)),
        "clicks": int(float(props.get("hs_email_click_count") or 0)),
        "replies": int(float(props.get("hs_email_reply_count") or 0)),
    }


def main():
    if not HUBSPOT_TOKEN:
        print("ERROR: falta la variable de entorno HUBSPOT_TOKEN", file=sys.stderr)
        sys.exit(1)

    try:
        emails = fetch_all_ingrid_emails(EMAILS_SINCE_DATE)
    except urllib.error.HTTPError as e:
        print(f"ERROR al llamar a HubSpot: {e.code} {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

    email_ids = [e["id"] for e in emails]
    contact_by_email_id = fetch_contact_by_email_map(email_ids)

    detail = [
        build_detail_row(e, contact_by_email_id.get(e["id"]))
        for e in emails
    ]
    # Ya vienen ordenados por fecha desde HubSpot (DESCENDING), pero por si
    # alguna fecha viene vacía, se ordena de nuevo acá para no confiar solo
    # en el orden de la API.
    detail.sort(key=lambda r: (r["sent_date"] or "", r["sent_time"] or ""), reverse=True)

    total = len(detail)
    total_sent = sum(1 for r in detail if r["status_raw"] == "SENT")
    total_bounced = sum(1 for r in detail if r["status_raw"] == "BOUNCED")
    total_failed = sum(1 for r in detail if r["status_raw"] == "FAILED")
    total_opened = sum(1 for r in detail if r["opens"] > 0)
    total_clicked = sum(1 for r in detail if r["clicks"] > 0)
    total_replied = sum(1 for r in detail if r["replies"] > 0)
    total_with_rubro = sum(1 for r in detail if r["rubro"])

    def pct(n, d):
        return round(100 * n / d, 1) if d else 0.0

    data = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "from_email": INGRID_FROM_EMAIL,
        "since_date": EMAILS_SINCE_DATE,
        "total_emails": total,
        "total_sent": total_sent,
        "total_bounced": total_bounced,
        "total_failed": total_failed,
        "total_opened": total_opened,
        "open_rate": pct(total_opened, total_sent),
        "total_clicked": total_clicked,
        "click_rate": pct(total_clicked, total_sent),
        "total_replied": total_replied,
        "reply_rate": pct(total_replied, total_sent),
        "total_with_rubro": total_with_rubro,
        "rubro_coverage_rate": pct(total_with_rubro, total),
        "detail": detail,
    }

    with open("emails_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(
        f"OK: {total} correos de Ingrid, {total_sent} enviados, {total_opened} abiertos "
        f"({pct(total_opened, total_sent)}%), {total_clicked} con clic, {total_replied} con respuesta, "
        f"{total_bounced} rebotados, {total_failed} fallidos. "
        f"Rubro disponible en {total_with_rubro}/{total} ({pct(total_with_rubro, total)}%)."
    )


if __name__ == "__main__":
    main()
