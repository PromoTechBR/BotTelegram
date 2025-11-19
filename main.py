import os
import time
import json
import textwrap
from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# Carrega .env localmente (ignorando erro se não existir; no Render usaremos env vars)
load_dotenv()

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@PromoTechBrasil")
ML_SITE_ID = os.getenv("ML_SITE_ID", "MLB")
ML_AFFILIATE_TAG = os.getenv("ML_AFFILIATE_TAG", "promotechbr")

DEFAULT_KEYWORDS = [
    "smartphone",
    "notebook",
    "pc gamer",
    "fone bluetooth",
    "caixa de som",
    "computador",
    "hardware",
    "gadgets",
    "casa inteligente",
]

KEYWORDS = [
    k.strip() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()
] or DEFAULT_KEYWORDS

OFFERS_PER_RUN = int(os.getenv("OFFERS_PER_RUN", "10"))
MIN_DISCOUNT_PERCENT = float(os.getenv("MIN_DISCOUNT_PERCENT", "15"))
SENT_IDS_FILE = Path(os.getenv("SENT_IDS_FILE", "sent_offers.json"))


def load_sent_ids():
    if SENT_IDS_FILE.exists():
        try:
            with SENT_IDS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("ids", []))
        except Exception:
            return set()
    return set()


def save_sent_ids(sent_ids):
    try:
        with SENT_IDS_FILE.open("w", encoding="utf-8") as f:
            json.dump({"ids": list(sent_ids)}, f)
    except Exception:
        pass


def fetch_offers_for_keyword(keyword, limit=50):
    url = f"https://api.mercadolibre.com/sites/{ML_SITE_ID}/search"
    params = {
        "q": keyword,
        "limit": limit,
        "offset": 0,
        "condition": "new",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


def build_affiliate_link(permalink: str) -> str:
    """
    ATENÇÃO: adapte para o formato EXATO do seu programa de afiliados.
    Aqui é só um exemplo usando aff_tag=promotechbr.
    """
    if "?" in permalink:
        return f"{permalink}&aff_tag={ML_AFFILIATE_TAG}"
    return f"{permalink}?aff_tag={ML_AFFILIATE_TAG}"


def compute_discount(item):
    price = item.get("price")
    original_price = item.get("original_price")
    if original_price and original_price > price:
        return round((original_price - price) / original_price * 100, 2)
    return 0.0


def collect_best_offers():
    all_items = {}
    for kw in KEYWORDS:
        try:
            results = fetch_offers_for_keyword(kw)
        except Exception as e:
            print(f"[ERRO] Falha ao buscar '{kw}': {e}")
            continue

        for item in results:
            item_id = item.get("id")
            if not item_id:
                continue
            if item_id in all_items:
                continue

            discount = compute_discount(item)
            sold_quantity = item.get("sold_quantity") or 0

            all_items[item_id] = {
                "id": item_id,
                "title": item.get("title", "")[:120],
                "price": item.get("price"),
                "original_price": item.get("original_price"),
                "discount": discount,
                "sold_quantity": sold_quantity,
                "permalink": item.get("permalink"),
                "thumbnail": item.get("thumbnail"),
            }

    filtered = [
        it for it in all_items.values()
        if it["discount"] >= MIN_DISCOUNT_PERCENT
    ]

    filtered.sort(key=lambda x: (x["discount"], x["sold_quantity"]), reverse=True)
    return filtered


def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não definido")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "disable_web_page_preview": False,
        "parse_mode": "HTML",
    }
    resp = requests.post(url, json=payload, timeout=15)
    if not resp.ok:
        print(f"[ERRO] Telegram: {resp.status_code} {resp.text}")
    return resp


def build_message(item):
    price = item["price"]
    original_price = item["original_price"]
    discount = item["discount"]
    sold = item["sold_quantity"]
    link = build_affiliate_link(item["permalink"])

    preco_str = f"R$ {price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if original_price:
        orig_str = f"R$ {original_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        preco_line = f"<b>Preço:</b> {preco_str} (de {orig_str}, {discount:.0f}% OFF)"
    else:
        preco_line = f"<b>Preço:</b> {preco_str}"

    sold_line = f"<b>Vendas:</b> {sold}" if sold else ""

    text = textwrap.dedent(f"""
        🔥 <b>{item["title"]}</b>

        {preco_line}
        {sold_line}

        👉 <a href="{link}">Ver oferta no Mercado Livre</a>
    """).strip()

    return text


def run_once_logic():
    print("[INFO] Buscando melhores ofertas...")
    sent_ids = load_sent_ids()
    offers = collect_best_offers()

    new_offers = [o for o in offers if o["id"] not in sent_ids]

    if not new_offers:
        print("[INFO] Nenhuma nova oferta nova encontrada.")
        return {"sent": 0, "message": "Nenhuma nova oferta encontrada."}

    to_send = new_offers[:OFFERS_PER_RUN]
    print(f"[INFO] Enviando {len(to_send)} ofertas para o canal {TELEGRAM_CHANNEL_ID}...")

    sent_count = 0
    titles = []

    for idx, item in enumerate(to_send, start=1):
        msg = build_message(item)
        send_telegram_message(msg)
        print(f"[OK] Oferta {idx} enviada: {item['title']}")
        titles.append(item["title"])
        sent_ids.add(item["id"])
        sent_count += 1
        time.sleep(2)  # pequeno intervalo para evitar flood

    save_sent_ids(sent_ids)
    print("[INFO] Execução finalizada.")

    return {
        "sent": sent_count,
        "titles": titles,
        "channel": TELEGRAM_CHANNEL_ID,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run-offers")
def run_offers():
    try:
        result = run_once_logic()
        return JSONResponse(content={"ok": True, "result": result})
    except Exception as e:
        print(f"[ERRO] Execução /run-offers: {e}")
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )