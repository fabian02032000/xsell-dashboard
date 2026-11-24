"""Trae los números de Google Ads para la pestaña "Google Ads" del dashboard.

Cómo llegan los datos (todo gratis, sin claves de la API de Google Ads):
  1. Un script dentro de la cuenta de Google Ads (scripts/google_ads_script.js)
     corre cada hora y deja los números, en formato JSON, en la hoja de Google
     "Xsell – Datos Google Ads (dashboard)", pestaña "json", columna A.
  2. Este archivo descarga esa hoja como CSV (es de solo lectura con el enlace),
     une los trozos y guarda google_ads_data.json en el repositorio.

Variable de entorno: GOOGLE_ADS_SHEET_ID (el ID de esa hoja). Si falta o falla
la descarga, se deja el archivo anterior tal cual y el workflow sigue.
"""
import csv
import io
import json
import os
import sys
import urllib.request

OUT = "google_ads_data.json"


def main():
    sheet_id = (os.environ.get("GOOGLE_ADS_SHEET_ID") or "").strip()
    if not sheet_id:
        print("GOOGLE_ADS_SHEET_ID no está configurado; no se actualiza Google Ads.")
        return
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=0"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            raw = r.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo descargar la hoja de Google Ads: {e}")
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
    print(
        f"Google Ads actualizado ({data.get('updated_at')}): "
        f"{len(data.get('campanas_dia', []))} filas de campaña, "
        f"{len(data.get('busquedas_dia', []))} filas de búsquedas."
    )


if __name__ == "__main__":
    main()
