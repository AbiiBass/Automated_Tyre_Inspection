TECHNICIAN DEVICE SELECTION

Select Device 1, 2, or 3 on the technician homepage. This selection is saved in the login session. Open an inspection and tap a tyre field to listen.

ESP32 boards must POST JSON to /api/esp/measurement with the device_id matching the selection:
Device 1 = ESP_001
Device 2 = ESP_002
Device 3 = ESP_003

Example: {"device_id":"ESP_001","value":"0.937;0.469;0.312;0.000;0.000;0.344"}

Send X-API-Key as before. The existing ESP32 firmware must be updated to include device_id, or the backend will return HTTP 400. The provided esp_test_sender.py has been updated accordingly.

The device selector does not establish a persistent physical connection: it routes incoming HTTPS readings to the selected technician while a tyre field is listening. In-memory routing requires a single Flask process; use shared storage for multiple workers.
