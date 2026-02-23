from bs4 import BeautifulSoup
import requests
import json

url = "https://liquipedia.net/dota2/api.php"
params = {"action": "parse", "page": "Version_7.40c", "prop": "text", "format": "json"}
headers = {"User-Agent": "DotaCoachBot/1.0 (nctr834@gmail.com)"}

resp = requests.get(url, params=params, headers=headers)
html = resp.json()["parse"]["text"]["*"]

soup = BeautifulSoup(html, "html.parser")

# Remove all images
for img in soup.find_all("img"):
    img.decompose()

json.dump(soup.get_text(separator="\n", strip=True), open("data/patch_notes.json", "w"))
