import os
import logging
import requests  # website library for making web requests
GRAPH_API_BASE = "https://graph.facebook.com/v20.0"

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")


logger = logging.getLogger(__file__)


def _auth_headers():  # this is a helper function which can't be called outside this file
    return {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
# forms the authentication header which includes the authentication token to access whatsapp
# Bearer token is a standard http authentication scheme
# to prove to meta's server that the request is coming from a legitimate app


def get_media_url(media_id):  # receive the media_id i got from whatsapp on the webhook payload
    response = requests.get(f"{GRAPH_API_BASE}/{media_id}", headers=_auth_headers())  # to fetch this url combined with the media id to give me the info for this media object
    if response.status_code == 200:  # status_code is a plain int attribute, not a method - no parentheses
        return response.json()["url"]  # to convert the json text to python dict and access the downloadable url for the image
    else:
        logger.error(f"Error Code: {response.status_code}")
        return None


def download_media(media_url):
    response = requests.get(media_url, headers=_auth_headers(), stream=True)  # gets the media url to get the actual image bytes and uses the auth header for authentication
    if response.status_code == 200:  # stream=true to download piece by piece rather than downloading the whole file directly to the RAM
        for chunk in response.iter_content(chunk_size=128 * 1024):
            if chunk:
                yield chunk
    else:
        logger.error(f"couldn't download media url: {response.status_code}")
        return


def send_text_message(to, body):  # this is the part where we can add an agent
    try:
        url = f"{GRAPH_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"  # specific address to sending messages from my bot's num to
        payload = {
            "messaging_product": "whatsapp",  # to tell meta that this is a whatsapp message
            "to": to,
            "type": "text",  # this is a plain text message
            "text": {"body": body},  # the actual message content nested under text
        }  # build the payload so now i have
        response = requests.post(url, headers=_auth_headers(), json=payload)  # ask meta to create a new outgoing message
        # url is where to send this request
        # then i pass my authentication data and the json to convert the payload to json and send the actual content
        response.raise_for_status()
        return response.json()  # return the entire dict response and convert it to a python dict
    except Exception as e:
        logger.error(f"Couldn't send text message to {to} : {e}")