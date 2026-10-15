#!/usr/bin/env python3
"""
Manda a Meta (API de conversiones para CRM) cada etapa que Ingrid pone en los
negocios de HubSpot, con el formato que Meta exige para optimizar con el
objetivo "Maximizar el número de clientes potenciales calificados"
(Conversion Leads):

  - action_source = "system_generated"
  - custom_data.event_source = "crm" y custom_data.lead_event_source = "HubSpot"
  - user_data.lead_id (el identificador del formulario instantáneo de Meta) cuando
    lo tenemos, más correo y teléfono cifrados (SHA-256)
  - una etapa inicial ("Registro") para cada persona que llenó el formulario y
    luego cada cambio de etapa (Contactado, ReunionAgendada, Propuesta, Cliente,
    Descartada)

Lee data.json (fetch_hubspot.py) y meta_lead_ids.json (fetch_meta.py, no se sube
al repositorio). Compara contra meta_events_state.json para mandar solo lo nuevo.

Variable de entorno requerida:
  META_OFFLINE_TOKEN  -> token de acceso generado en Events Manager para el
                         conjunto de datos "Calidad de Leads HubSpot"

Es best-effort: si algo falla, imprime un aviso y no rompe el resto del pipeline.
"""
import os
import re
import json
import hashlib
import unicodedata
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

DATASET_ID = "1081995541100208"
GRAPH_URL = "https://graph.facebook.com/v21.0/{}/events".format(DATASET_ID)
CRM_NAME = "HubSpot"
STATE_FORMAT = "crm_v2"          # al cambiar el formato, se reenvía la etapa actual de todos una vez
MAX_AGE_SECONDS = 6 * 24 * 3600  # Meta acepta eventos de hasta 7 días atrás
BATCH = 500

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
DATA_FILE = os.path.join(BASE_DIR, "data.json")
LEAD_IDS_FILE = os.path.join(BASE_DIR, "meta_lead_ids.json")
STATE_FILE = os.path.join(BASE_DIR, "meta_events_state.json")


def normalize(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def map_event_name(stage):
    """Etapa de HubSpot -> nombre del evento en Meta. None si no tiene negocio."""
    s = normalize(stage)
    if not s or "sin negocio" in s:
        return None
    if "descartad" in s:
        return "Descartada"
    if "implementa" in s or "aceptad" in s or "ganad" in s:
        return "Cliente"
    if "propuesta" in s:
        return "Propuesta"
    if "reunion" in s:
        return "ReunionAgendada"
    if "contactad" in s:
        return "Contactado"
    return None


def sha256_hex(value):
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def phone_digits(phone):
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 9 and d.startswith("9"):  # celular peruano sin código de país
        d = "51" + d
    return d if len(d) >= 8 else ""


def parse_time(value):
    """ISO de Meta ('2026-10-14T21:03:11+0000') o fecha 'YYYY-MM-DD' -> unix."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value[:24] if "T" in value else value[:10], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def load_state():
    state = load_json(STATE_FILE, {})
    if state.get("_format") != STATE_FORMAT:
        return {"_format": STATE_FORMAT, "leads": {}}
    state.setdefault("leads", {})
    return state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def build_user_data(email, phone, lead_id):
    ud = {"em": [sha256_hex(email)]}
    ph = phone_digits(phone)
    if ph:
        ud["ph"] = [sha256_hex(ph)]
    if lead_id and str(lead_id).isdigit():
        ud["lead_id"] = int(lead_id)
    return ud


def make_event(name, when, user_data):
    return {
        "event_name": name,
        "event_time": when,
        "action_source": "system_generated",
        "user_data": user_data,
        "custom_data": {"event_source": "crm", "lead_event_source": CRM_NAME},
    }


def build_events(leads, lead_ids, state, now):
    """Devuelve (eventos, estado_nuevo). Función pura, fácil de probar."""
    to_send = []
    new_state = {"_format": STATE_FORMAT, "leads": dict(state.get("leads", {}))}
    seen = new_state["leads"]
    oldest_ok = now - MAX_AGE_SECONDS

    for lead in leads:
        email = (lead.get("email") or "").strip().lower()
        if "@" not in email:
            continue
        info = lead_ids.get(email) or {}
        lead_time = parse_time(info.get("created_time"))
        if not lead_time and parse_time(lead.get("created_date")):
            # HubSpot solo da el día: se suma un día para no quedar antes del registro real.
            lead_time = parse_time(lead.get("created_date")) + 86400
        user_data = build_user_data(email, lead.get("phone"), info.get("lead_id"))
        rec = dict(seen.get(email) or {})

        # Etapa inicial: la persona llenó el formulario.
        if not rec.get("registro"):
            when = max(lead_time or oldest_ok, oldest_ok)
            to_send.append(make_event("Registro", min(when, now), user_data))
            rec["registro"] = True

        # Etapa actual en HubSpot, si cambió desde el último envío.
        stage = map_event_name(lead.get("deal_stage")) if lead.get("has_deal") else None
        if stage and rec.get("stage") != stage:
            to_send.append(make_event(stage, now, user_data))
            rec["stage"] = stage

        seen[email] = rec

    # Personas que llenaron el formulario de Meta pero no están (o no tienen correo) en
    # HubSpot: igual se manda su etapa inicial, porque Meta exige que al menos el 60%
    # de los clientes potenciales reciba un evento ("cobertura").
    in_hubspot = {(l.get("email") or "").strip().lower() for l in leads}
    for email, info in lead_ids.items():
        if email in in_hubspot or "@" not in email or (seen.get(email) or {}).get("registro"):
            continue
        lead_time = parse_time(info.get("created_time"))
        when = min(max(lead_time or oldest_ok, oldest_ok), now)
        to_send.append(make_event("Registro", when, build_user_data(email, None, info.get("lead_id"))))
        seen[email] = {"registro": True}

    return to_send, new_state


def send_events(token, events):
    payload = {"data": json.dumps(events), "access_token": token}
    body = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(GRAPH_URL, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    token = os.environ.get("META_OFFLINE_TOKEN")
    if not token:
        print("AVISO: no se encontró META_OFFLINE_TOKEN, se omite el envío de eventos a Meta.")
        return
    data = load_json(DATA_FILE, None)
    if not data:
        print("AVISO: no se encontró data.json, se omite el envío de eventos a Meta.")
        return

    leads = data.get("leads_detail") or []
    lead_ids = load_json(LEAD_IDS_FILE, {})
    now = int(datetime.now(timezone.utc).timestamp())
    to_send, new_state = build_events(leads, lead_ids, load_state(), now)

    if not to_send:
        print("Sin eventos nuevos que mandar a Meta (todo está al día).")
        return

    with_id = sum(1 for e in to_send if "lead_id" in e["user_data"])
    try:
        for i in range(0, len(to_send), BATCH):
            result = send_events(token, to_send[i:i + BATCH])
            print("Lote {}: {}".format(i // BATCH + 1, result))
        print("Se mandaron {} eventos a Meta ({} con identificador del formulario).".format(len(to_send), with_id))
        save_state(new_state)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print("AVISO: falló el envío de eventos a Meta ({}): {}".format(e.code, body))
    except Exception as e:
        print("AVISO: falló el envío de eventos a Meta: {}".format(e))


if __name__ == "__main__":
    main()
