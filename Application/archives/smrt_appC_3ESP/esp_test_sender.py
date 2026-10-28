"""Pretend to be an ESP32 gauge. Usage:

    python3 esp_test_sender.py ESP_002 1.1 1.2 1.3 1.2 1.1         # device + 5 readings (average is computed)
    python3 esp_test_sender.py ESP_002 1.1 1.2 1.3 1.2 1.1 1.18    # device + 6 values sent exactly as given
    python3 esp_test_sender.py 1.1 1.2 1.3 1.2 1.1                 # no device given -> uses DEFAULT_DEVICE

Devices: ESP_001 (Device 1), ESP_002 (Device 2), ESP_003 (Device 3).
Select the same device on the technician home page, then tap a tyre box, then run this.
Set SERVER_URL to your ngrok address, or use http://127.0.0.1:5000/... locally.
"""
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SERVER_URL = "https://devotee-unsuited-moisture.ngrok-free.dev/api/esp/measurement"
API_KEY = "local-test-key"
DEFAULT_DEVICE = "ESP_001"

args = sys.argv[1:]
device = DEFAULT_DEVICE
if args and args[0].upper().startswith("ESP_"):
    device = args.pop(0).upper()

vals = [float(v) for v in args]
if len(vals) == 5:
    vals.append(sum(vals) / 5)
if len(vals) != 6:
    sys.exit("Give 5 readings (average is added) or all 6 values, optionally after a device id.")

message = ";".join(f"{v:.3f}" for v in vals)
print(f"Device: {device}\nSending: {message}")
req = Request(
    SERVER_URL,
    data=json.dumps({"device_id": device, "value": message}).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "X-API-Key": API_KEY,
        "ngrok-skip-browser-warning": "true",
    },
    method="POST",
)
try:
    with urlopen(req, timeout=15) as r:
        print(r.status, r.read().decode("utf-8"))
except HTTPError as exc:
    print("HTTP Error:", exc.code, exc.read().decode("utf-8", errors="replace"))
except URLError as exc:
    print("Send failed:", exc)
