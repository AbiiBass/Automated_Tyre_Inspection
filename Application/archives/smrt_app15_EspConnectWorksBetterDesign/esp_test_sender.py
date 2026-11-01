"""Pretend to be the ESP32 gauge. Usage:

    python3 esp_test_sender.py 1.1 1.2 1.3 1.2 1.1         # 5 readings -> average is computed
    python3 esp_test_sender.py 1.1 1.2 1.3 1.2 1.1 1.18    # 6 values sent exactly as given

Tap a tyre box in the technician page first, then run this.
Set SERVER_URL to your ngrok address, or use http://127.0.0.1:5000/... locally.
"""
import sys
import requests

SERVER_URL = "https://devotee-unsuited-moisture.ngrok-free.dev/api/esp/measurement"
API_KEY = "local-test-key"

vals = [float(v) for v in sys.argv[1:]]
if len(vals) == 5:
    vals.append(round(sum(vals) / 5, 2))
if len(vals) != 6:
    sys.exit("Give 5 readings (average is added) or all 6 values.")

message = ";".join(f"{v:g}" for v in vals)
r = requests.post(
    SERVER_URL,
    data=message,
    headers={"X-API-Key": API_KEY, "Content-Type": "text/plain"},
    timeout=10,
)
print(r.status_code, r.text)
