
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import math

# ========================================
# CONFIGURATION
# ========================================

SERVER_URL = "https://devotee-unsuited-moisture.ngrok-free.dev/api/esp/measurement"

API_KEY = "local-test-key"

# Fixed device identification
DEVICE_ID = "ESP_002"

# ========================================
# SEND MEASUREMENTS
# ========================================

print("================================")
print("    ESP32 MEASUREMENT SIMULATOR")
print("================================")
print("Device: Device 2 (ESP_002)")

while True:

    entry = input(
        "\nEnter 5 measurements separated by semicolons "
        "(q to quit): "
    ).strip()

    if entry.lower() == "q":
        break

    try:
        # Convert input into five readings
        readings = [
            float(x.strip())
            for x in entry.split(";")
        ]

        # Validate number of readings
        if len(readings) != 5:
            print("Error: Enter exactly 5 measurements.")
            continue

        # Validate measurement range
        if not all(
            math.isfinite(x) and 0 <= x <= 30
            for x in readings
        ):
            print("Error: Readings must be finite values between 0 and 30 mm.")
            continue

        # Calculate average
        average = sum(readings) / 5

        # Create semicolon-separated string
        measurement_string = ";".join(
            f"{x:.3f}" for x in readings
        ) + f";{average:.3f}"

        print("\n----------------------------")
        print("Device:", DEVICE_ID)
        print("Readings:", readings)
        print(f"Average: {average:.3f} mm")
        print("Sending:", measurement_string)

        # JSON payload
        payload = json.dumps({
            "device_id": DEVICE_ID,
            "value": measurement_string
        }).encode("utf-8")

        req = Request(
            SERVER_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
                "ngrok-skip-browser-warning": "true"
            },
            method="POST"
        )

        with urlopen(req, timeout=15) as response:
            print("HTTP Status:", response.status)
            print(
                "Server Response:",
                response.read().decode("utf-8")
            )
            print("Measurement sent successfully!")

    except HTTPError as exc:
        print("HTTP Error:", exc.code)
        print(
            exc.read().decode(
                "utf-8", errors="replace"
            )
        )

    except (ValueError, URLError) as exc:
        print("Send failed:", exc)