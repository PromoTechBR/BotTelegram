import os
import time
import json
import re
from pathlib import Path
from typing import List

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@PromoTechBrasil")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "changeme")
ALLOWED_TELEGRAM_USER_ID = os.getenv("ALLOWED_TELEGRAM_USER_ID")  # opcional

OFFERS_PER_RUN = int(os.getenv("OFFERS_PER_RUN", "10"))
LINKS_QUEUE_FILE = Path(os.getenv("LINKS_QUEUE_FILE", "links_queue.json"))


class TelegramUpdate(BaseModel):
    update_id: int | None = None
    message: dict | None = None
    edited_message: dict | None = None
    # ignoramos o resto


def load_links_queue() -> List[str]:
    if LINKS_QUEUE_FILE.exists():
        try:
            with LINKS_QUEUE_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("links", [])
        except Exception:
            return []
    return []


def save_links_queue(links: List[str]):
    try:
        with LINKS_QUEUE_FILE.open("w", encoding="utf-8") as f:
            json.dump({"links": links}, f)
    except Exception:
        pass

def jukeraKRL(text: str) -> List[str]:
    if not text:
        return []
    urls = re.findall(r"https?://\S+", text)
    result = []
    for url in urls:
        clean = url.strip(" ,;)")
        match clean:
            case "mercadolivre.com":
                result.append(clean)
            case "mercadolibre.com":
                result.append(clean)
            case "amazon.com.br":
                result.append(clean)
            case "amzn.to":
                result.append(clean)
            case "shopee.com.br":
                result.append(clean)
            case "shopee.com":
                result.append(clean)
    return result


def enqueue_links(new_links: List[str]) -> int:
    if not new_links:
        return 0
    queue = load_links_queue()
    existing = set(queue)
    added = 0
    for link in new_links:
        if link not in existing:
            queue.append(link)
            existing.add(link)
            added += 1
    save_links_queue(queue)
    return added


def send_telegram_message(text: str, chat_id: str | int):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não definido")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False,
        "parse_mode": "HTML",
    }
    resp = requests.post(url, json=payload, timeout=15)
    if not resp.ok:
        print(f"[ERRO] Telegram: {resp.status_code} {resp.text}")
    return resp


def run_once_logic():
    print("[INFO] Consumindo fila de links...")
    queue = load_links_queue()
    if not queue:
        print("[INFO] Fila vazia, nenhuma oferta para enviar.")
        return {"sent": 0, "message": "Fila vazia."}

    to_send = queue[:OFFERS_PER_RUN]
    remaining = queue[OFFERS_PER_RUN:]

    sent_count = 0
    for idx, link in enumerate(to_send, start=1):
        text = f"🔥 Oferta #{idx}:\n{link}"
        send_telegram_message(text, TELEGRAM_CHANNEL_ID)
        print(f"[OK] Link enviado: {link}")
        sent_count += 1
        time.sleep(2)

    save_links_queue(remaining)
    print(f"[INFO] Execução finalizada. Enviados {sent_count} links.")
    return {"sent": sent_count, "remaining": len(remaining)}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/telegram/webhook/{secret}")
def telegram_webhook(secret: str, update: TelegramUpdate):
    if secret != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    msg = update.message or update.edited_message
    if not msg:
        return {"ok": True}

    from_user = msg.get("from", {})
    user_id = from_user.get("id")
    chat_id = msg["chat"]["id"]
    text = msg.get("text") or msg.get("caption") or ""

    if ALLOWED_TELEGRAM_USER_ID and str(user_id) != str(ALLOWED_TELEGRAM_USER_ID):
        print(f"[INFO] Ignorando mensagem de user_id {user_id}")
        return {"ok": True}

    links = jukeraKRL(text)
    if not links:
        # tenta achar links em entidades
        entities = msg.get("entities") or msg.get("caption_entities") or []
        for e in entities:
            if e.get("type") == "text_link" and e.get("url"):
                links.append(e["url"])

    added = enqueue_links(links)

    if added > 0:
        send_telegram_message(
            f"✅ Recebi {added} link(s). Eles serão enviados gradualmente para o canal.",
            chat_id,
        )
    else:
        send_telegram_message(
            "Não encontrei nenhum link do Mercado Livre na mensagem (ou já estavam na fila).",
            chat_id,
        )

    return {"ok": True, "added": added}


@app.post("/run-offers")
def run_offers():
    try:
        result = run_once_logic()
        return JSONResponse(content={"ok": True, "result": result})
    except Exception as e:
        print(f"[ERRO] Execução /run-offers: {e}")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
