"""Números de Microsoft Clarity (mapas de calor de la web) para la pestaña "Web".

Clarity permite solo 10 consultas al día, así que esto consulta como máximo
una vez cada 4 horas y guarda el día de hoy en clarity_data.json (se va
armando un historial por día).

Variable de entorno: CLARITY_TOKEN (se crea en Clarity → Configuración →
Exportación de datos). Si falta o falla, se deja el archivo anterior.
"""
import datetime
import json
import os
import urllib.request

OUT = "clarity_data.json"
CADA_HORAS = 4
LIMA = datetime.timezone(datetime.timedelta(hours=-5))


def main():
    token = (os.environ.get("CLARITY_TOKEN") or "").strip()
    if not token:
        print("CLARITY_TOKEN no está configurado; no se actualiza Clarity.")
        return
    try:
        with open(OUT, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        data = {"dias": {}}
    ahora = datetime.datetime.now(datetime.timezone.utc)
    ultima = data.get("fetched_at")
    if ultima and ahora - datetime.datetime.fromisoformat(ultima) < datetime.timedelta(hours=CADA_HORAS):
        print("Clarity se consultó hace menos de 4 horas; se espera.")
        return
    url = "https://www.clarity.ms/export-data/api/v1/project-live-insights?numOfDays=1&dimension1=Device"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    # Clarity a veces demora en responder: esperamos hasta 3 minutos y reintentamos una vez.
    res = None
    for intento in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read().decode("utf-8"))
            break
        except Exception as e:  # noqa: BLE001
            print(f"No se pudo consultar Clarity (intento {intento}): {e}")
    if res is None:
        return
    hoy = ahora.astimezone(LIMA).strftime("%Y-%m-%d")
    dia = {}
    for bloque in res:
        nombre = bloque.get("metricName")
        dia[nombre] = bloque.get("information", [])
    data.setdefault("dias", {})[hoy] = dia
    data["fetched_at"] = ahora.isoformat()
    data["updated_at"] = ahora.isoformat()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Clarity actualizado para {hoy}: {', '.join(dia.keys())}")


if __name__ == "__main__":
    main()
