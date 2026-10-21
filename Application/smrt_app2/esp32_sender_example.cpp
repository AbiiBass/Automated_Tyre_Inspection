// Replace the existing ESP32 JSON payload construction with:
// Five measurements followed by their average, separated by semicolons.
float samples[5] = {4.125, 4.188, 4.063, 4.125, 4.250};
float sum = 0;
String measurement = "";
for (int i = 0; i < 5; ++i) {
    if (i) measurement += ";";
    measurement += String(samples[i], 3);
    sum += samples[i];
}
measurement += ";" + String(sum / 5.0f, 3);
String payload = "{\"value\":\"" + measurement + "\"}";
// Send payload with the same HTTPS POST and X-API-Key header as before.
