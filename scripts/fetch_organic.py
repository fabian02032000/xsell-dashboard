#!/usr/bin/env python3
"""
Lee las publicaciones orgánicas (sin pago) de la Página de Facebook y de la
cuenta de Instagram conectada, junto con sus seguidores, alcance e
interacciones, y genera organic_data.json para la pestaña de "Contenido
orgánico" del dashboard. Se ejecuta automáticamente vía GitHub Actions,
junto con fetch_hubspot.py y fetch_meta.py.

Usa el mismo token del sistema (META_TOKEN) que fetch_meta.py, pero con
permisos adicionales sobre la Página y la cuenta de Instagram.
"""
import os
import sys
import json
import datetime
import urllib.request
import urllib.parse
import urllib.error
from collections import defaultdict

# ---------------------- CONFIGURACIÓN ----------------------
META_TOKEN = os.environ.get("META_TOKEN")
API_VERSION = "v21.0"
API_BASE = f"https://graph.facebook.com/{API_VERSION}"
# Parte del nombre de la Página de Facebook, para identificarla si el usuario
# del sistema tuviera acceso a más de una.
PAGE_NAME_MATCH = os.environ.get("PAGE_NAME_MATCH", "Xsell")
OUTPUT_FILE = "organic_data.json"
MAX_POSTS = 60
MAX_HISTORY_DAYS = 180


def meta_get(path, params, token=None):
    params = dict(params)
    params["access_token"] = token or META_TOKEN
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def discover_assets():
    """
    Encuentra la Página de Facebook asignada al usuario del sistema y, si
    tiene una cuenta de Instagram profesional conectada, también su ID.
    Devuelve None si no se encontró ninguna Página (por ejemplo, si aún no
    se le asignó acceso).
    """
    try:
        data = meta_get("/me/accounts", {
            "fields": "id,name,access_token,instagram_business_account{id,username}",
            "limit": 50,
        })
    except Exception as e:
        print(f"ERROR: no se pudo listar las páginas conectadas al token: {e}", file=sys.stderr)
        return None

    pages = data.get("data", [])
    if not pages:
        print("ERROR: el usuario del sistema no tiene ninguna página de Facebook asignada.", file=sys.stderr)
        return None

    page = None
    for p in pages:
        if PAGE_NAME_MATCH.lower() in (p.get("name") or "").lower():
            page = p
            break
    if page is None:
        page = pages[0]

    ig = page.get("instagram_business_account") or {}
    return {
        "page_id": page.get("id"),
        "page_name": page.get("name"),
        "page_token": page.get("access_token"),
        "ig_user_id": ig.get("id"),
        "ig_username": ig.get("username"),
    }


def fetch_facebook_posts(page_id, page_token):
    """Publicaciones de la Página, con likes/comentarios/compartidos."""
    try:
        data = meta_get(f"/{page_id}/posts", {
            "fields": "id,message,created_time,full_picture,permalink_url,"
                      "likes.summary(true),comments.summary(true),shares",
            "limit": MAX_POSTS,
        }, token=page_token)
    except Exception as e:
        print(f"AVISO: no se pudieron traer las publicaciones de Facebook: {e}", file=sys.stderr)
        return [], False

    posts = []
    for row in data.get("data", []):
        likes = ((row.get("likes") or {}).get("summary") or {}).get("total_count", 0) or 0
        comments = ((row.get("comments") or {}).get("summary") or {}).get("total_count", 0) or 0
        shares = (row.get("shares") or {}).get("count", 0) or 0
        posts.append({
            "platform": "facebook",
            "id": row.get("id"),
            "date": (row.get("created_time") or "")[:10],
            "text": (row.get("message") or "")[:280],
            "image_url": row.get("full_picture") or "",
            "permalink": row.get("permalink_url") or "",
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "reach": None,
            "interactions": likes + comments + shares,
        })
    return posts, True


def fetch_instagram_media_insights(media_id, page_token):
    """
    Alcance e interacciones de una publicación puntual de Instagram. Si el
    permiso, la versión de la API o el tipo de publicación no lo permiten,
    devuelve (None, None) sin romper el resto del script.
    """
    try:
        data = meta_get(f"/{media_id}/insights", {
            "metric": "reach,total_interactions",
        }, token=page_token)
        values = {}
        for row in data.get("data", []):
            vlist = row.get("values") or [{}]
            values[row.get("name")] = vlist[0].get("value")
        return values.get("reach"), values.get("total_interactions")
    except Exception:
        return None, None


def fetch_instagram_media(ig_user_id, page_token):
    """Publicaciones de Instagram (post por post), con su alcance e interacciones."""
    if not ig_user_id:
        return [], False
    try:
        data = meta_get(f"/{ig_user_id}/media", {
            "fields": "id,caption,timestamp,permalink,media_type,media_url,"
                      "thumbnail_url,like_count,comments_count",
            "limit": 40,
        }, token=page_token)
    except Exception as e:
        print(f"AVISO: no se pudieron traer las publicaciones de Instagram: {e}", file=sys.stderr)
        return [], False

    posts = []
    for row in data.get("data", []):
        media_id = row.get("id")
        reach, engagement = fetch_instagram_media_insights(media_id, page_token)
        likes = row.get("like_count", 0) or 0
        comments = row.get("comments_count", 0) or 0
        image = row.get("thumbnail_url") or row.get("media_url") or ""
        posts.append({
            "platform": "instagram",
            "id": media_id,
            "date": (row.get("timestamp") or "")[:10],
            "text": (row.get("caption") or "")[:280],
            "image_url": image,
            "permalink": row.get("permalink") or "",
            "likes": likes,
            "comments": comments,
            "shares": 0,
            "reach": reach,
            "interactions": engagement if engagement is not None else (likes + comments),
        })
    return posts, True


def fetch_facebook_snapshot(page_id, page_token):
    """Seguidores actuales de la Página, en este momento."""
    try:
        data = meta_get(f"/{page_id}", {"fields": "followers_count,fan_count"}, token=page_token)
        return data.get("followers_count") or data.get("fan_count") or 0, True
    except Exception as e:
        print(f"AVISO: no se pudo traer el número de seguidores de Facebook: {e}", file=sys.stderr)
        return None, False


def fetch_instagram_snapshot(ig_user_id, page_token):
    """Seguidores actuales de Instagram, en este momento."""
    if not ig_user_id:
        return None, False
    try:
        data = meta_get(f"/{ig_user_id}", {"fields": "followers_count,media_count"}, token=page_token)
        return data.get("followers_count"), True
    except Exception as e:
        print(f"AVISO: no se pudo traer el número de seguidores de Instagram: {e}", file=sys.stderr)
        return None, False


def load_previous_history():
    """
    Lee el organic_data.json de la ejecución anterior (si existe) para no
    perder el historial de seguidores acumulado día a día — Meta no entrega
    ese histórico completo por API, así que lo vamos guardando nosotros.
    """
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            prev = json.load(f)
        return prev.get("follower_history", [])
    except Exception:
        return []


def update_follower_history(history, today_str, fb_followers, ig_followers):
    history = [h for h in history if h.get("date") != today_str]
    history.append({"date": today_str, "fb_followers": fb_followers, "ig_followers": ig_followers})
    history.sort(key=lambda h: h["date"])
    return history[-MAX_HISTORY_DAYS:]


def build_posting_calendar(posts):
    """Cuántas publicaciones (por red) se hicieron cada día, para el calendario."""
    calendar = {}
    for p in posts:
        d = p.get("date")
        if not d:
            continue
        if d not in calendar:
            calendar[d] = {"facebook": 0, "instagram": 0}
        calendar[d][p["platform"]] += 1
    return calendar


def build_monthly_summary(posts):
    """Publicaciones, interacciones y alcance sumado, mes a mes."""
    months = defaultdict(lambda: {"posts": 0, "interactions": 0, "reach": 0, "reach_count": 0})
    for p in posts:
        d = p.get("date")
        if not d:
            continue
        m = months[d[:7]]
        m["posts"] += 1
        m["interactions"] += p.get("interactions") or 0
        if p.get("reach"):
            m["reach"] += p["reach"]
            m["reach_count"] += 1

    result = []
    for key in sorted(months.keys()):
        m = months[key]
        result.append({
            "month": key,
            "posts": m["posts"],
            "interactions": m["interactions"],
            "reach": m["reach"] if m["reach_count"] else None,
        })
    return result


def main():
    if not META_TOKEN:
        print("ERROR: falta la variable de entorno META_TOKEN", file=sys.stderr)
        sys.exit(1)

    today_str = datetime.date.today().isoformat()
    history_so_far = load_previous_history()

    assets = discover_assets()
    if assets is None:
        data = {
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "facebook_available": False,
            "instagram_available": False,
            "error": "No se encontró ninguna página de Facebook asignada al usuario del sistema.",
            "page_name": None,
            "ig_username": None,
            "fb_followers": None,
            "ig_followers": None,
            "posts": [],
            "monthly_summary": [],
            "posting_calendar": {},
            "follower_history": history_so_far,
        }
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("AVISO: no se encontraron activos de Facebook/Instagram — organic_data.json quedó vacío.")
        return

    fb_posts, fb_ok = fetch_facebook_posts(assets["page_id"], assets["page_token"])
    ig_posts, ig_ok = fetch_instagram_media(assets["ig_user_id"], assets["page_token"])
    fb_followers, fb_snap_ok = fetch_facebook_snapshot(assets["page_id"], assets["page_token"])
    ig_followers, ig_snap_ok = fetch_instagram_snapshot(assets["ig_user_id"], assets["page_token"])

    all_posts = fb_posts + ig_posts
    all_posts.sort(key=lambda p: p["date"], reverse=True)

    history = update_follower_history(history_so_far, today_str, fb_followers, ig_followers)

    data = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "facebook_available": fb_ok,
        "instagram_available": ig_ok and bool(assets["ig_user_id"]),
        "page_name": assets["page_name"],
        "ig_username": assets["ig_username"],
        "fb_followers": fb_followers,
        "ig_followers": ig_followers,
        "posts": all_posts,
        "monthly_summary": build_monthly_summary(all_posts),
        "posting_calendar": build_posting_calendar(all_posts),
        "follower_history": history,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(
        f"OK: {len(fb_posts)} publicaciones de Facebook, {len(ig_posts)} de Instagram, "
        f"{fb_followers or 0} seguidores FB, {ig_followers or 0} seguidores IG"
    )


if __name__ == "__main__":
    main()
