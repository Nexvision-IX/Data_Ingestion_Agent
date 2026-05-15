import requests

from ingestion.config import (
    KEFRON_BASE_URL,
    KEFRON_API_KEY
)

class KefronClient:

    def __init__(self):

        self.base_url = KEFRON_BASE_URL

        self.headers = {
            "Authorization": f"Bearer {KEFRON_API_KEY}"
        }

    def get(self, endpoint, params=None):

        url = f"{self.base_url}{endpoint}"

        response = requests.get(
            url,
            headers=self.headers,
            params=params,
            timeout=60
        )

        response.raise_for_status()

        return response.json()