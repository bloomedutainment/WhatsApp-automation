from dotenv import load_dotenv
load_dotenv()

from app import whatsapp

# Replace with the recipient's number, international format, no "+" or spaces
recipient = "201090276077"

response = whatsapp.send_text_message(recipient, "Hello from my bot!")
print(response)