import json
from urllib.request import Request, urlopen

SERVER_URL = "https://devotee-unsuited-moisture.ngrok-free.dev/api/esp/measurement"
API_KEY = "local-test-key"

while True:
    entry = input("Five readings separated by semicolons (q to quit): ").strip()
    if entry.lower() == "q":
        break
    try:
        readings = [float(v.strip()) for v in entry.split(";")]
        if len(readings) != 5:
            raise ValueError("Exactly five readings required")
        message = ";".join(f"{v:.3f}" for v in readings) + f";{sum(readings)/5:.3f}"
        payload = json.dumps({"value": message}).encode()
        req = Request(SERVER_URL, data=payload, headers={"Content-Type": "application/json", "X-API-Key": API_KEY, "ngrok-skip-browser-warning": "true"}, method="POST")
        with urlopen(req, timeout=15) as response:
            print(response.status, response.read().decode())
    except Exception as exc:
        print("Send failed:", exc)
