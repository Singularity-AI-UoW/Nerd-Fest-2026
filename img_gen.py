import os
from google import genai
import base64

client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])  # upgrade this key's tier

interaction = client.interactions.create(
    model="gemini-nano-banana-2.1",
    input="A kawaii-style sticker of a tuatara with a kiwifruit, bold outlines, white background",
    response_format={"type": "image", "aspect_ratio": "1:1", "image_size": "1K"},
    timeout=60.0,  # 1 min, so a stall surfaces as an exception instead of hanging forever
)

with open("output.png", "wb") as f:
    f.write(base64.b64decode(interaction.output_image.data))