#!/usr/bin/env python3
"""
VIGIA // GTA VI — resumo diário via Notion + Telegram.

Roda 1x/dia via GitHub Actions (cron). Lê o data.json já gerado pelo
fetch_signals.py (não importa nem modifica esse script), seleciona os
sinais fortes das últimas 24h e manda pro Notion (arquivo buscável) e
Telegram (empurrão pra pessoa agir no dia).

Todas as credenciais são opcionais — sem NOTION_TOKEN/NOTION_DATABASE_ID
pula o Notion, sem TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID pula o Telegram,
sem nenhuma das duas o script sai sem fazer nada.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data.json")
DATA_FILE = os.path.abspath(DATA_FILE)
DIGEST_SENT_FILE = os.path.join(os.path.dirname(__file__), "..", "digest_sent.json")
DIGEST_SENT_FILE = os.path.abspath(DIGEST_SENT_FILE)

LOOKBACK_HOURS = 24
MIN_SIGNAL = 4
MAX_ITEMS = 3
SENT_RETENTION_DAYS = 3


def load_items():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[digest] falhou ler {DATA_FILE}: {e}", file=sys.stderr)
        return []
    return data.get("items", []) if isinstance(data, dict) else []


def load_sent():
    if not os.path.exists(DIGEST_SENT_FILE):
        return {}
    try:
        with open(DIGEST_SENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[digest] falhou ler {DIGEST_SENT_FILE}: {e}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def save_sent(sent):
    with open(DIGEST_SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sent, f, ensure_ascii=False, indent=2)


def prune_sent(sent):
    """Remove entradas com mais de SENT_RETENTION_DAYS dias — o suficiente
    pra nunca colidir com a janela de 24h, sem crescer sem limite."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=SENT_RETENTION_DAYS)
    kept = {}
    for url, sent_at in sent.items():
        try:
            if datetime.fromisoformat(sent_at) >= cutoff:
                kept[url] = sent_at
        except Exception:
            continue
    return kept


def select_digest_items(items, sent):
    """Filtra por janela de 24h + sinal mínimo, tira quem já foi enviado,
    ordena por sinal (desempate por mais recente) e corta em MAX_ITEMS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    candidates = []
    for it in items:
        published_at = it.get("publishedAt")
        if not published_at:
            continue
        try:
            published = datetime.fromisoformat(published_at)
            if published < cutoff:
                continue
        except Exception:
            continue
        if (it.get("signal") or 0) < MIN_SIGNAL:
            continue
        if it.get("url") in sent:
            continue
        candidates.append((published, it))
    candidates.sort(key=lambda pair: (pair[1]["signal"], pair[0]), reverse=True)
    return [it for _, it in candidates[:MAX_ITEMS]]


NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def send_to_notion(item, token, database_id):
    """Cria uma linha na database do Notion pro item. Retorna True em
    sucesso, False em falha — nunca lança, quem chama decide o que fazer."""
    try:
        r = requests.post(
            f"{NOTION_API_BASE}/pages",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            json={
                "parent": {"database_id": database_id},
                "properties": {
                    "Headline": {"title": [{"text": {"content": item.get("headline", "")[:2000]}}]},
                    "Categoria": {"select": {"name": item.get("tagLabel") or item.get("cat") or "Outro"}},
                    "Sinal": {"number": item.get("signal", 0)},
                    "Data": {"date": {"start": item.get("publishedAt") or datetime.now(timezone.utc).isoformat(timespec="seconds")}},
                    "Gancho": {"rich_text": [{"text": {"content": item.get("hook", "")[:2000]}}]},
                    "Link": {"url": item.get("url") or None},
                },
            },
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[digest] falhou escrever no Notion pra '{item.get('headline', '')[:50]}': {e}", file=sys.stderr)
        return False
