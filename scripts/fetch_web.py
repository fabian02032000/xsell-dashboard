"""Trae los números de la web (Google Analytics) para la pestaña "Web" del dashboard.

Cómo llegan los datos (gratis, sin claves):
  1. Un proyecto de Apps Script (scripts/analytics_script.gs) corre cada hora,
     lee Google Analytics y deja los números en JSON en la hoja
     "Xsell – Datos Web (dashboard)", pestaña "json", columna A.
  2. Este archivo descarga esa hoja como CSV, une los trozos y guarda web_data.json.

Variable de entorno: WEB_SHEET_ID. Si falta o falla, se deja el archivo anterior.
"""
import csv
import io
import json
import os
import sys
import urllib.request

OUT = "web_data.json"


def main():
    sheet_id = (os.environ.get("WEB_SHEET_ID") or "").strip()
    if not sheet_id:
        print("WEB_SHEET_ID no está configurado; no se actualiza la web.")
        return
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=0"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            raw = r.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo descargar la hoja de la web: {e}")
        return
    csv.field_size_limit(sys.maxsize)
    trozos = [row[0] for row in csv.reader(io.StringIO(raw)) if row and row[0]]
    try:
        data = json.loads("".join(trozos))
    except json.JSONDecodeError as e:
        print(f"La hoja no tiene un JSON válido todavía: {e}")
        return
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Web actualizada ({data.get('updated_at')}): {len(data.get('dia', []))} días.")


if __name__ == "__main__":
    main()
