from __future__ import annotations

import json
import urllib.request


class SlackNotifier:
    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    def send(self, subject: str, body: str) -> None:
        payload = json.dumps({"text": f"*{subject}*\n{body}"}).encode()
        req = urllib.request.Request(
            self._webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
