"""Registros que llegan desde el formulario de la web (wexsell.co) a HubSpot.

Lee los envíos del formulario "Xsell Web - Fotografía Comercial (Chat)" con la
API de formularios de HubSpot (mismo HUBSPOT_TOKEN del dashboard) y guarda
web_leads.json con: fecha, nombre, empresa, sector, qué hizo (vio diagnóstico,
descargó PDF, pidió la Fotografía Comercial, chat) y de dónde vino (Google Ads,
Meta, Google sin pagar, directo).

Por ser un dashboard público, NO se guardan correos ni teléfonos.
Si algo falla, se deja el archivo anterior y el workflow sigue.
"""
import datetime
import json
import os
import re
import urllib.parse
import urllib.request

OUT = "web_leads.json"
FORM_ID = "a5d4bc5a-b046-495c-8503-122ea67e69a8"
DESDE = datetime.datetime(2026, 11, 16, tzinfo=datetime.timezone.utc)
LIMA = datetime.timezone(datetime.timedelta(hours=-5))


def get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def tipo(msg):
    m = (msg or "").upper()
    if "FOTO COMERCIAL WEB" in m:
        return "Pidió la Fotografía Comercial", 3
    if "DESCARGÓ SU PDF" in m or "DESCARGO SU PDF" in m:
        return "Descargó su PDF", 2
    if "DESBLOQUEÓ" in m or "DESBLOQUEO" in m:
        return "Vio su diagnóstico", 1
    if "CHAT" in m:
        return "Escribió en el chat", 0
    return "Otro", 0


def origen(page_url):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(page_url or "").query)
    src = (q.get("utm_source") or [""])[0].lower()
    med = (q.get("utm_medium") or [""])[0].lower()
    if q.get("gclid") or q.get("gbraid") or q.get("wbraid") or (src == "google" and med in ("cpc", "paid", "ppc")):
        return "Google Ads"
    if q.get("fbclid") or re.search(r"facebook|instagram|^fb$|^ig$|meta", src):
        return "Meta (Facebook e Instagram)"
    if src:
        return src.capitalize()
    return "Sin dato (directo o búsqueda)"


def campo(msg, nombre):
    m = re.search(nombre + r":\s*([^|]+)", msg or "")
    return m.group(1).strip() if m else ""


def post(url, token, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


FUENTES = {
    "PAID_SEARCH": "Google Ads", "ORGANIC_SEARCH": "Google (sin pagar)", "DIRECT_TRAFFIC": "Directo (escribieron la web)",
    "PAID_SOCIAL": "Meta (Facebook e Instagram)", "SOCIAL_MEDIA": "Redes sociales (sin pagar)", "REFERRALS": "Otros sitios",
    "EMAIL_MARKETING": "Correo", "OTHER_CAMPAIGNS": "Otras campañas", "OFFLINE": "Cargado a mano", "AI_REFERRALS": "Buscadores con IA",
}


def envios_formulario(token):
    """Envíos del formulario (necesita el permiso 'forms' en el token)."""
    envios, after = [], None
    for _ in range(40):
        url = f"https://api.hubapi.com/form-integrations/v1/submissions/forms/{FORM_ID}?limit=50"
        if after:
            url += "&after=" + urllib.parse.quote(after)
        data = get(url, token)
        for s in data.get("results", []):
            ts = datetime.datetime.fromtimestamp(s.get("submittedAt", 0) / 1000, tz=datetime.timezone.utc)
            if ts < DESDE:
                continue
            vals = {v.get("name"): v.get("value") for v in s.get("values", [])}
            envios.append((ts, vals, origen(s.get("pageUrl") or "")))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    return envios


def envios_contactos(token):
    """Plan B con el permiso de contactos: contactos cuyo mensaje viene de la web."""
    envios, after = [], None
    desde_ms = str(int(DESDE.timestamp() * 1000))
    props = ["firstname", "company", "message", "createdate", "hs_analytics_source", "hs_analytics_source_data_1", "email"]
    for _ in range(20):
        body = {"filterGroups": [{"filters": [
                    {"propertyName": "message", "operator": "CONTAINS_TOKEN", "value": "XSELL"},
                    {"propertyName": "createdate", "operator": "GTE", "value": desde_ms}]}],
                "properties": props, "limit": 100,
                "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}]}
        if after:
            body["after"] = after
        data = post("https://api.hubapi.com/crm/v3/objects/contacts/search", token, body)
        for c in data.get("results", []):
            p = c.get("properties", {})
            msg = p.get("message") or ""
            if "WEB XSELL" not in msg.upper() and "CHAT" not in msg.upper():
                continue
            ts = datetime.datetime.fromisoformat((p.get("createdate") or "").replace("Z", "+00:00"))
            fuente = (p.get("hs_analytics_source") or "").upper()
            ori = FUENTES.get(fuente, fuente.capitalize() or "Sin dato")
            d1 = (p.get("hs_analytics_source_data_1") or "").lower()
            if fuente in ("PAID_SOCIAL", "SOCIAL_MEDIA") and re.search(r"facebook|instagram", d1):
                ori = "Meta (Facebook e Instagram)" + ("" if fuente == "PAID_SOCIAL" else " sin pagar")
            envios.append((ts, p, ori))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    return envios


def main():
    token = (os.environ.get("HUBSPOT_TOKEN") or "").strip()
    if not token:
        print("HUBSPOT_TOKEN no está configurado; no se actualizan los registros de la web.")
        return
    try:
        envios = envios_formulario(token)
        via = "formulario"
    except Exception as e:  # noqa: BLE001
        print(f"Sin acceso a los envíos del formulario ({e}); uso los contactos de HubSpot.")
        try:
            envios = envios_contactos(token)
            via = "contactos"
        except Exception as e2:  # noqa: BLE001
            print(f"No se pudieron leer los contactos de la web: {e2}")
            return

    # Una fila por persona (por correo), con lo más lejos que llegó.
    personas = {}
    for ts, v, ori in sorted(envios, key=lambda x: x[0]):
        clave = (v.get("email") or v.get("firstname") or str(ts)).strip().lower()
        msg = v.get("message") or ""
        t, nivel = tipo(msg)
        p = personas.get(clave)
        if not p:
            p = personas[clave] = {
                "fecha": ts.astimezone(LIMA).strftime("%Y-%m-%d"),
                "hora": ts.astimezone(LIMA).strftime("%H:%M"),
                "nombre": (v.get("firstname") or "").strip(),
                "empresa": (v.get("company") or "").strip(),
                "sector": "", "accion": t, "_nivel": nivel, "origen": ori,
                "ventas_hoy": "", "potencial": "", "etapa": "", "envios": 0,
            }
        p["envios"] += 1
        if nivel >= p["_nivel"]:
            p["accion"], p["_nivel"] = t, nivel
        for k, n in (("sector", "Sector"), ("ventas_hoy", "Ventas hoy"), ("potencial", "Potencial"), ("etapa", "Etapa")):
            val = campo(msg, n)
            if val and val != "-":
                p[k] = val
        if p["origen"].startswith("Sin dato"):
            p["origen"] = ori
    filas = sorted(personas.values(), key=lambda x: (x["fecha"], x["hora"]), reverse=True)
    for f in filas:
        f.pop("_nivel", None)
    out = {"updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "via": via,
           "registros": filas, "envios": len(envios)}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"Registros de la web ({via}): {len(filas)} personas, {len(envios)} envíos.")


if __name__ == "__main__":
    main()
