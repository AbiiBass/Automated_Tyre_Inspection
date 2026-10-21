import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# Flask server through ngrok
SERVER_URL = "https://devotee-unsuited-moisture.ngrok-free.dev/api/esp/measurement"

API_KEY = "local-test-key"

while True:
    entry = input(
        "\nEnter 5 measurements separated by semicolons (q to quit): "
    ).strip()

    if entry.lower() == "q":
        break

    try:
        # Convert semicolon-separated input into floats
        readings = [float(x.strip()) for x in entry.split(";")]

        # Validate exactly 5 readings
        if len(readings) != 5:
            print("Error: Please enter exactly 5 measurements.")
            continue

        # Validate measurement range
        if not all(0 <= x <= 30 for x in readings):
            print("Error: Measurements must be between 0 and 30 mm.")
            continue

        # Calculate average
        average = sum(readings) / len(readings)

        # Create 6-value semicolon-separated string
        measurement_string = ";".join(
            f"{x:.3f}" for x in readings
        ) + f";{average:.3f}"

        print("\nReadings:", readings)
        print(f"Average: {average:.3f} mm")
        print("Sending:", measurement_string)

        # JSON payload
        payload = json.dumps({
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
            print(
                "Success:",
                response.status,
                response.read().decode("utf-8")
            )

    except HTTPError as exc:
        print("HTTP Error:", exc.code)
        print(exc.read().decode("utf-8", errors="replace"))

    except (ValueError, URLError) as exc:
        print("Send failed:", exc)
