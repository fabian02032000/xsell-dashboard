#!/usr/bin/env python3
"""
Manda a Meta (API de conversiones) el estado de tipificacion que Ingrid pone
en cada negocio de HubSpot (Contactado, Reunion Agendada, Descartada), para
que Meta pueda optimizar la entrega de anuncios hacia perfiles parecidos a
los que si califican -- y evitar perfiles parecidos a los descartados.

Lee data.json (generado por fetch_hubspot.py) y compara contra un archivo de
estado (meta_events_state.json) para solo mandar eventos nuevos o que
cambiaron de estado desde la ultima vez -- evita mandar el mismo evento cada
hora sin necesidad.

Variable de entorno requerida:
  META_OFFLINE_TOKEN  -> token de acceso generado en Meta Events Manager
                         para el conjunto de datos "Calidad de Leads HubSpot"

Es best-effort: si algo falla, imprime un aviso y no rompe el resto del
pipeline (igual que fetch_notes_for_deals en fetch_hubspot.py).
"""
import os
import json
import hashlib
import unicodedata
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

DATASET_ID = "1081995541100208"
GRAPH_URL = "https://graph.facebook.com/v21.0/{}/events".format(DATASET_ID)

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
DATA_FILE = os.path.join(BASE_DIR, "data.json")
STATE_FILE = os.path.join(BASE_DIR, "meta_events_state.json")


def normalize(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def map_event_name(stage):
    s = normalize(stage)
    if not s:
        return None
    if "reunion" in s:
        return "ReunionAgendada"
    if "contactad" in s:
        return "Contactado"
    if "descartad" in s:
        return "Descartada"
    if "sin negocio" in s:
        return "SinNegocio"
    return None


def sha256_hex(value):
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def now_unix():
    # Meta exige que la marca de tiempo del evento sea de los ultimos 7 dias.
    # Como este evento representa "asi esta el lead AHORA" (no cuando se creo),
    # siempre usamos el momento del envio, sin importar la fecha de creacion.
    return int(datetime.now(timezone.utc).timestamp())


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def send_events(token, events):
    payload = {
        "data": json.dumps(events),
        "access_token": token,
    }
    body = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(GRAPH_URL, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_events(leads, state):
    """Devuelve (eventos_a_mandar, estado_actualizado). Funcion pura, facil de testear."""
    to_send = []
    new_state = dict(state)
    for lead in leads:
        email = (lead.get("email") or "").strip().lower()
        if "@" not in email:
            continue
        event_name = map_event_name(lead.get("deal_stage"))
        if not event_name:
            continue

        if new_state.get(email) == event_name:
            continue  # no cambio nada desde la ultima vez, no reenviar

        to_send.append({
            "event_name": event_name,
            "event_time": now_unix(),
            "action_source": "system_generated",
            "user_data": {
                "em": [sha256_hex(email)],
            },
            "custom_data": {
                "value": 0,
                "currency": "PEN",
            },
        })
        new_state[email] = event_name

    return to_send, new_state


def main():
    token = os.environ.get("META_OFFLINE_TOKEN")
    if not token:
        print("AVISO: no se encontro META_OFFLINE_TOKEN, se omite el envio de eventos a Meta.")
        return

    if not os.path.exists(DATA_FILE):
        print("AVISO: no se encontro data.json, se omite el envio de eventos a Meta.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    leads = data.get("leads_detail") or []
    state = load_state()

    to_send, new_state = build_events(leads, state)

    if not to_send:
        print("Sin eventos nuevos que mandar a Meta (todo esta al dia).")
        return

    try:
        result = send_events(token, to_send)
        print("Se mandaron {} eventos a Meta. Respuesta: {}".format(len(to_send), result))
        save_state(new_state)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print("AVISO: fallo el envio de eventos a Meta ({}): {}".format(e.code, body))
    except Exception as e:
        print("AVISO: fallo el envio de eventos a Meta: {}".format(e))


if __name__ == "__main__":
    main()
