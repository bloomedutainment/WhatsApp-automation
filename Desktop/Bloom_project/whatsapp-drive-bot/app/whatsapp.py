import os

import requests

GRAPH_API_BASE = "https://graph.facebook.com/v20.0"

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")


def _auth_headers():
    return {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}


def get_media_url(media_id):
    response = requests.get(f"{GRAPH_API_BASE}/{media_id}", headers=_auth_headers())
    response.raise_for_status()
    return response.json()["url"]


def download_media(media_url):
    response = requests.get(media_url, headers=_auth_headers())
    response.raise_for_status()
    return response.content


def send_text_message(to, body):
    url = f"{GRAPH_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    response = requests.post(url, headers=_auth_headers(), json=payload)
    response.raise_for_status()
    return response.json()
