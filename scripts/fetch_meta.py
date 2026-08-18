#!/usr/bin/env python3
"""
Lee el gasto (inversión) de Meta Ads y genera meta_data.json para el dashboard.
Se ejecuta automáticamente vía GitHub Actions, junto con fetch_hubspot.py.
"""
import os
import sys
import json
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

META_TOKEN = os.environ.get("META_TOKEN")
API_VERSION = "v21.0"
API_BASE = f"https://graph.facebook.com/{API_VERSION}"


def meta_get(path, params):
    params = dict(params)
    params["access_token"] = META_TOKEN
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_campaign_totals(since_date, until_date):
    """Gasto total por campaña, en el rango dado."""
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
        "daily_spend": daily,
    }

    with open("meta_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"OK: gasto total S/{total_spend:.2f}, este mes S/{month_spend:.2f}")


if __name__ == "__main__":
    main()
