#!/usr/bin/env python3
"""
Benchmark semanal de competidores: agencias de ventas B2B (no de marketing)
que pautan en Meta (Facebook/Instagram), para comparar contra la actividad
propia de la cuenta.

Qué SÍ se puede saber de un competidor por este medio (dato público, gratis,
vía la Biblioteca de Anuncios de Meta): qué anuncios tiene activos ahora
mismo, desde cuándo corre cada uno, y el texto de la creatividad.

Qué NO se puede saber, y este script nunca inventa: cuánto gasta un
competidor, cuántos leads o ventas genera, ni ningún dato de resultados.
Esa información es privada de cada cuenta publicitaria y nadie puede verla
desde afuera — ni este script, ni Meta la comparte.

La Biblioteca de Anuncios es una página web dinámica pensada para uso
humano, no una API estable: Meta puede cambiarla, mostrarla en otro idioma
según la región del servidor que consulta, o bloquear temporalmente tráfico
automatizado. Por eso este script es defensivo: si un competidor no se
puede leer esta semana (bloqueo, cambio de diseño, timeout), NO se inventa
ni se borra su dato anterior — se conserva el último resultado bueno y se
marca cuándo fue la última vez que se pudo confirmar, para que el
dashboard sea siempre honesto sobre qué tan fresca es la información.

Requiere Playwright con Chromium (se instala en el workflow de GitHub
Actions vía `playwright install --with-deps chromium`).
"""
import os
import re
import json
import datetime
import urllib.parse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_FILE = os.path.join(BASE_DIR, "benchmark_data.json")

# ------------------------- COMPETIDORES -------------------------
# Agencias de ventas B2B (venta/prospección tercerizada, NO agencias de
# marketing) identificadas manualmente en septiembre 2026 por tener
# anuncios activos verificables en la Biblioteca de Anuncios de Meta.
# Se pueden agregar o quitar competidores editando esta lista.
COMPETITORS = [
    {
        "id": "pacs",
        "name": "PACS - La Aceleradora B2B",
        "country": "AR",
        "country_label": "Argentina",
        "profile": "Ayuda a empresas B2B a conseguir reuniones y cerrar más ventas, con un método propio (\"anti-ghosting\", seguimiento, manejo de objeciones).",
        "search_query": "PACS La Aceleradora B2B",
    },
    {
        "id": "enbi",
        "name": "ENBI Consulting",
        "country": "CO",
        "country_label": "Colombia (alcance regional)",
        "profile": "Agencia de prospección B2B: agenda reuniones calificadas para empresas de tecnología/software.",
        "search_query": "ENBI Consulting",
    },
    {
        "id": "aurum",
        "name": "Aurum Sales",
        "country": "BR",
        "country_label": "Brasil",
        "profile": "Equipo comercial tercerizado (\"time comercial terceirizado\") para acelerar resultados de venta B2B.",
        "search_query": "Aurum Sales",
    },
]

MAX_ADS_PER_COMPETITOR = 12


def build_url(query: str, country: str) -> str:
    q = urllib.parse.quote(query)
    return (
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
        f"&country={country}&is_targeted_country=false&media_type=all"
        f"&q={q}&search_type=keyword_unordered"
    )


# Meses en varios idiomas/formatos que la Biblioteca de Anuncios puede
# mostrar según el idioma con el que Meta decida responder (depende de la
# región del servidor que consulta, no es controlable de forma confiable
# desde afuera).
MONTHS = {
    "ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5,
    "jun": 6, "jul": 7, "ago": 8, "aug": 8, "sep": 9, "set": 9, "oct": 10,
    "nov": 11, "dic": 12, "dec": 12,
}

# Formato en español ("En circulación desde el 19 mar 2026"): día, mes, año.
DATE_RE_ES = re.compile(
    r"En circulaci[oó]n desde el?\s+(\d{1,2})\s*(?:de\s+)?"
    r"([A-Za-zÁÉÍÓÚáéíóú]{3,})\.?\s*(?:de\s+|,\s*)?(\d{4})",
    re.IGNORECASE,
)
# Formato en inglés ("Active since Aug 24, 2026"): mes, día, año — el orden
# se invierte respecto al español. El servidor de GitHub Actions es un
# datacenter en EE. UU., así que Meta podría responder en inglés aunque el
# resto del script pida español; por eso se intentan ambos formatos.
DATE_RE_EN = re.compile(
    r"Active since\s+([A-Za-z]{3,})\.?\s+(\d{1,2}),?\s+(\d{4})",
    re.IGNORECASE,
)
COUNT_RE = re.compile(r"~?\s*([\d.,]+)\s*(?:results?|resultados?)", re.IGNORECASE)
# Cada tarjeta de anuncio individual empieza con la línea "Activo"/"Active"
# (estado del anuncio), en su propia línea.
CARD_SPLIT_RE = re.compile(r"\n\s*Activo\s*\n|\n\s*Active\s*\n")
# Dentro de una tarjeta: quién la publica, justo después del botón
# "Ver detalles (del anuncio)" / "See ad details".
PAGE_NAME_RE = re.compile(
    r"(?:Ver detalles(?: del (?:anuncio|resumen))?|See (?:ad|summary) details)\s*\n"
    r"(.+?)\s*\n\s*(?:Publicidad|Sponsored)\b",
    re.IGNORECASE,
)


def parse_since_date(text: str):
    m = DATE_RE_ES.search(text)
    if m:
        day, month_raw, year = m.groups()
    else:
        m = DATE_RE_EN.search(text)
        if not m:
            return None
        month_raw, day, year = m.groups()
    month = MONTHS.get(month_raw.strip().lower()[:3])
    if not month:
        return None
    try:
        return datetime.date(int(year), month, int(day)).isoformat()
    except ValueError:
        return None


def name_matches(candidate: str, target: str) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    c, t = norm(candidate), norm(target)
    return bool(c) and (c in t or t in c or c == t)


def scrape_competitor(page, comp):
    url = build_url(comp["search_query"], comp["country"])
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    # La página carga los resultados por JS después del load inicial.
    page.wait_for_timeout(4000)
    try:
        page.wait_for_selector("text=/resultados|results/i", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1500)

    body_text = page.inner_text("body")

    # Conteo que reporta Meta para la búsqueda (incluye cualquier anuncio
    # que mencione esas palabras, no solo los de este competidor exacto —
    # por eso se guarda aparte, como referencia, y el conteo "de verdad"
    # es cuántas tarjetas cargadas tienen exactamente su nombre de página).
    count_match = COUNT_RE.search(body_text)
    search_match_count = None
    if count_match:
        raw = count_match.group(1).replace(".", "").replace(",", "")
        if raw.isdigit():
            search_match_count = int(raw)

    chunks = CARD_SPLIT_RE.split(body_text)[1:]  # [0] es el header/nav, se descarta
    since_dates = []
    samples = []
    matched_cards = 0
    for chunk in chunks[: MAX_ADS_PER_COMPETITOR * 2]:
        name_match = PAGE_NAME_RE.search(chunk)
        page_name = name_match.group(1).strip() if name_match else None
        if not page_name or not name_matches(page_name, comp["name"]):
            continue  # tarjeta de otro anunciante que coincidió con la palabra clave
        matched_cards += 1
        since = parse_since_date(chunk)
        if since:
            since_dates.append(since)
        if len(samples) < 6:
            body_start = name_match.end() if name_match else 0
            tail = chunk[body_start:].strip()
            snippet = tail.split("\n")[0].strip() if tail else ""
            if since or snippet:
                samples.append({"since": since, "text": snippet[:220]})

    active_ads_count = matched_cards if matched_cards else search_match_count

    return {
        "active_ads_count": active_ads_count,
        "search_match_count": search_match_count,
        "oldest_active_since": min(since_dates) if since_dates else None,
        "newest_active_since": max(since_dates) if since_dates else None,
        "sample_ads": samples,
        "ad_library_url": url,
    }


def load_previous():
    if os.path.exists(OUT_FILE):
        try:
            with open(OUT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def main():
    from playwright.sync_api import sync_playwright

    previous = load_previous() or {"competitors": {}, }
    prev_competitors = previous.get("competitors", {})

    today = datetime.date.today().isoformat()
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    result_competitors = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            locale="es-PE",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        for comp in COMPETITORS:
            cid = comp["id"]
            prev = prev_competitors.get(cid, {})
            history = prev.get("history", [])
            entry = {
                "id": cid,
                "name": comp["name"],
                "country": comp["country"],
                "country_label": comp["country_label"],
                "profile": comp["profile"],
            }
            try:
                scraped = scrape_competitor(page, comp)
                ok = scraped.get("active_ads_count") is not None
            except Exception as exc:  # noqa: BLE001 - nunca debe tumbar el resto
                scraped = None
                ok = False
                print(f"[benchmark] {cid}: no se pudo leer esta semana ({exc})")

            if ok:
                entry.update(scraped)
                entry["last_checked_ok"] = today
                # Evita duplicar el mismo número dos semanas seguidas en el
                # historial (igual se guarda 1 punto por semana real).
                if not history or history[-1].get("date") != today:
                    history = history + [
                        {"date": today, "active_ads_count": scraped["active_ads_count"]}
                    ]
                entry["status"] = "ok"
            else:
                # Se conserva el último dato bueno conocido, marcado como
                # desactualizado — nunca se inventa un número nuevo.
                entry.update(
                    {
                        k: prev.get(k)
                        for k in (
                            "active_ads_count",
                            "search_match_count",
                            "oldest_active_since",
                            "newest_active_since",
                            "sample_ads",
                            "ad_library_url",
                            "last_checked_ok",
                        )
                    }
                )
                entry["status"] = "stale"

            entry["history"] = history[-26:]  # ~6 meses de historia semanal
            result_competitors[cid] = entry

        browser.close()

    output = {
        "generated_at": now_iso,
        "note": (
            "Conteo de anuncios activos y fecha de inicio de cada anuncio, "
            "leídos de la Biblioteca de Anuncios de Meta (público y gratis). "
            "No incluye gasto ni resultados de estos competidores: eso es "
            "privado de cada cuenta y no existe forma de verlo desde afuera."
        ),
        "competitors": result_competitors,
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[benchmark] listo → {OUT_FILE}")


if __name__ == "__main__":
    main()
