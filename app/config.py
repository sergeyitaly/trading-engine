# app/config.py
import json
import os
import re
import requests
from bs4 import BeautifulSoup

CONFIG_URL = "https://www.jsonschemavalidator.net/s/Lungj8uI"
LOCAL_CONFIG_PATH = "config.json"


def load_config():
    # Try remote
    try:
        response = requests.get(CONFIG_URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Look for the <script> containing "var state ="
        for script in soup.find_all("script"):
            text = script.string or ""
            if "var state =" in text:
                match = re.search(r'"SchemaText":"(.*?)","', text, re.DOTALL)
                if match:
                    schema_raw = match.group(1)
                    schema_clean = bytes(schema_raw, "utf-8").decode("unicode_escape")
                    config = json.loads(schema_clean)
                    print("✅ Loaded config from remote")
                    return config

        print("❌ Could not parse SchemaText from remote page")

    except Exception as e:
        print(f"⚠️ Error loading config from remote: {e}")

    # Try local file
    if os.path.exists(LOCAL_CONFIG_PATH):
        try:
            with open(LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
                print("📂 Loaded config from local file")
                return config
        except Exception as e:
            print(f"⚠️ Error loading local config.json: {e}")

    # If both fail, raise
    raise RuntimeError("❌ Could not load configuration from remote or local file.")


# Test run
if __name__ == "__main__":
    config = load_config()
    print(json.dumps(config, indent=2))
