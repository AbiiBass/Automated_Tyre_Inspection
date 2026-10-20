
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <math.h>

// =====================================================
// 1. PIN CONFIGURATION
// =====================================================

#define SDA_PIN 8
#define SCL_PIN 9
#define OLED_RESET 10

#define MEASURE_BUTTON_PIN 4
#define UPLOAD_BUTTON_PIN 5

// =====================================================
// 2. OLED CONFIGURATION
// =====================================================

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_ADDRESS 0x3D

Adafruit_SSD1306 display(
    SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET
);

bool oledReady = false;

// =====================================================
// 3. GP2Y0E02B SENSOR
// =====================================================

const uint8_t GP2Y_ADDR = 0x40;
const uint8_t SHIFT_REG = 0x35;
const uint8_t DISTANCE_H_REG = 0x5E;

// Experimental calibration
// 30 mm measured distance = 0 mm tread depth
const float REFERENCE_MM = 30.000f;

// =====================================================
// 4. MEASUREMENT SETTINGS
// =====================================================

const int RAW_SAMPLE_COUNT = 15;
const int FILTERED_COUNT = 5;
const int GROUP_SIZE = 3;

const int SAMPLE_DELAY_MS = 30;
const float MAX_DISTANCE_MM = 500.0f;

// Thresholds supplied for the project
const float SMRT_THRESHOLD_MM = 5.000f;
const float LTA_THRESHOLD_MM = 1.600f;

// =====================================================
// 5. WIFI SETTINGS
// =====================================================

#include "secrets.h"

const char* WIFI_SSID = WIFI_NAME;
const char* WIFI_PASSWORD = WIFI_PASS;

// No laptop IP address required!
const uint16_t DISCOVERY_PORT = 5001;
const uint16_t TCP_PORT = 5000;

const char* DISCOVERY_REQUEST =
    "FIND_TREADDEPTH_SERVER";

const char* DISCOVERY_RESPONSE =
    "TREADDEPTH_SERVER_READY";

WiFiUDP udp;

// =====================================================
// 6. STORED MEASUREMENTS
// =====================================================

float storedReadings[FILTERED_COUNT] = {0};
float displayedDepth = 0.0f;

bool measurementAvailable = false;
bool measurementUploaded = false;

// =====================================================
// 7. SENSOR REGISTER READING
// =====================================================

bool readRegister(uint8_t reg, uint8_t &result)
{
    Wire.beginTransmission(GP2Y_ADDR);
    Wire.write(reg);

    if (Wire.endTransmission(false) != 0)
        return false;

    if (Wire.requestFrom(
            GP2Y_ADDR, (uint8_t)1) != 1)
        return false;

    result = Wire.read();
    return true;
}

// =====================================================
// 8. READ SENSOR DISTANCE
// =====================================================

bool readDistance(float &distanceMm)
{
    uint8_t shiftReg;

    if (!readRegister(SHIFT_REG, shiftReg))
        return false;

    uint8_t shift = shiftReg & 0x07;

    Wire.beginTransmission(GP2Y_ADDR);
    Wire.write(DISTANCE_H_REG);

    if (Wire.endTransmission(false) != 0)
        return false;

    if (Wire.requestFrom(
            GP2Y_ADDR, (uint8_t)2) != 2)
        return false;

    uint8_t highByte = Wire.read();
    uint8_t lowByte = Wire.read();

    uint16_t raw =
        ((uint16_t)highByte << 4) |
        (lowByte & 0x0F);

    float distanceCm =
        (float)raw / 16.0f / (1 << shift);

    distanceMm = distanceCm * 10.0f;

    return isfinite(distanceMm);
}

// =====================================================
// 9. MEDIAN FUNCTIONS
// =====================================================

float medianOfThree(float a, float b, float c)
{
    if (a > b) {
        float t = a; a = b; b = t;
    }

    if (b > c) {
        float t = b; b = c; c = t;
    }

    if (a > b) {
        float t = a; a = b; b = t;
    }

    return b;
}

float medianOfFive(const float values[5])
{
    float sorted[5];

    for (int i = 0; i < 5; i++)
        sorted[i] = values[i];

    for (int i = 0; i < 4; i++) {
        for (int j = i + 1; j < 5; j++) {
            if (sorted[j] < sorted[i]) {
                float t = sorted[i];
                sorted[i] = sorted[j];
                sorted[j] = t;
            }
        }
    }

    return sorted[2];
}

// =====================================================
// 10. OLED BASE SCREEN (V1 STYLE)
// =====================================================

void drawHeader()
{
    if (!oledReady) return;

    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.setTextSize(1);

    display.setCursor(27, 5);
    display.println("TREAD DEPTH");

    display.drawLine(
        0, 17, 127, 17, SSD1306_WHITE
    );
}

void showMessage(const char* line1, const char* line2 = "")
{
    if (!oledReady) return;

    drawHeader();

    display.setTextSize(1);
    display.setCursor(5, 30);
    display.println(line1);

    display.setCursor(5, 45);
    display.println(line2);

    display.display();
}

void showReady()
{
    showMessage("PRESS TO MEASURE");
}

void showMeasuring()
{
    showMessage("MEASURING...");
}

// =====================================================
// 11. OLED RESULT / WARNINGS
// =====================================================

void showDepth(float depthMm)
{
    if (!oledReady) return;

    drawHeader();

    // Preserve V1's large depth display
    display.setTextSize(2);
    display.setCursor(5, 27);
    display.print(depthMm, 3);
    display.print(" mm");

    if (depthMm <= LTA_THRESHOLD_MM) {
        // Strong inverted safety warning
        display.fillRect(
            0, 48, 128, 16, SSD1306_WHITE
        );

        display.setTextColor(SSD1306_BLACK);
        display.setTextSize(1);
        display.setCursor(19, 52);
        display.print("SAFETY WARNING");
    }
    else if (depthMm <= SMRT_THRESHOLD_MM) {
        // SMRT operational warning
        display.drawRect(
            0, 21, 128, 43, SSD1306_WHITE
        );

        display.setTextSize(1);
        display.setCursor(40, 52);
        display.print("WARNING!");
    }

    display.display();
}

void restoreDisplay()
{
    if (measurementAvailable)
        showDepth(displayedDepth);
    else
        showReady();
}

// =====================================================
// 12. CAPTURE MEASUREMENT
// =====================================================

void captureMeasurement()
{
    showMeasuring();

    // A new measurement invalidates the previous
    // stored readings until it succeeds.
    measurementAvailable = false;
    measurementUploaded = false;

    float rawSamples[RAW_SAMPLE_COUNT];

    Serial.println();
    Serial.println("===== MEASUREMENT START =====");

    for (int i = 0; i < RAW_SAMPLE_COUNT; i++) {
        float distanceMm;

        if (!readDistance(distanceMm)) {
            Serial.println("Sensor read error.");
            showMessage("SENSOR ERROR", "TRY AGAIN");
            return;
        }

        Serial.printf(
            "Sample %d: %.3f mm\n",
            i + 1, distanceMm
        );

        if (distanceMm > MAX_DISTANCE_MM ||
            distanceMm < REFERENCE_MM) {
            Serial.println("Invalid/out-of-range reading.");
            showMessage(
                "INVALID READING",
                "REPOSITION SENSOR"
            );
            return;
        }

        rawSamples[i] = distanceMm;
        delay(SAMPLE_DELAY_MS);
    }

    Serial.println();
    Serial.println("Five filtered tread depths:");

    for (int group = 0; group < FILTERED_COUNT; group++) {
        int base = group * GROUP_SIZE;

        float medianDistance = medianOfThree(
            rawSamples[base],
            rawSamples[base + 1],
            rawSamples[base + 2]
        );

        storedReadings[group] =
            medianDistance - REFERENCE_MM;

        Serial.printf(
            "Reading %d: %.3f mm\n",
            group + 1,
            storedReadings[group]
        );
    }

    displayedDepth = medianOfFive(storedReadings);

    measurementAvailable = true;

    Serial.printf(
        "OLED median depth: %.3f mm\n",
        displayedDepth
    );

    if (displayedDepth <= LTA_THRESHOLD_MM)
        Serial.println("STATUS: SAFETY WARNING");
    else if (displayedDepth <= SMRT_THRESHOLD_MM)
        Serial.println("STATUS: SMRT WARNING");
    else
        Serial.println("STATUS: OK");

    showDepth(displayedDepth);
}

// =====================================================
// 13. CONNECT TO WIFI
// =====================================================

bool connectWiFi()
{
    if (WiFi.status() == WL_CONNECTED)
        return true;

    Serial.println("Connecting to hotspot...");

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long start = millis();

    while (WiFi.status() != WL_CONNECTED &&
           millis() - start < 15000) {
        delay(300);
    }

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi connection failed.");
        return false;
    }

    Serial.print("Connected. ESP32 IP: ");
    Serial.println(WiFi.localIP());

    return true;
}

// =====================================================
// 14. AUTOMATIC LAPTOP DISCOVERY
// =====================================================

bool discoverLaptop(IPAddress &serverIP)
{
    Serial.println("Discovering Python receiver...");

    if (!udp.begin(0)) {
        Serial.println("UDP initialization failed.");
        return false;
    }

    // Use the current network's broadcast address.
    IPAddress localIP = WiFi.localIP();
    IPAddress mask = WiFi.subnetMask();

    IPAddress broadcastIP;

    for (int i = 0; i < 4; i++) {
        broadcastIP[i] =
            localIP[i] | (~mask[i] & 0xFF);
    }

    bool found = false;

    // Try discovery up to five times.
    for (int attempt = 0; attempt < 5 && !found; attempt++) {
        udp.beginPacket(broadcastIP, DISCOVERY_PORT);
        udp.print(DISCOVERY_REQUEST);
        udp.endPacket();

        Serial.printf(
            "Discovery attempt %d\n", attempt + 1
        );

        unsigned long start = millis();

        while (millis() - start < 1000) {
            int packetSize = udp.parsePacket();

            if (packetSize > 0) {
                char buffer[80];

                int len = udp.read(
                    buffer, sizeof(buffer) - 1
                );

                if (len > 0) {
                    buffer[len] = '\0';

                    if (strcmp(
                            buffer,
                            DISCOVERY_RESPONSE
                        ) == 0) {
                        // Use the sender's real IP address,
                        // not an address supplied in the message.
                        serverIP = udp.remoteIP();
                        found = true;
                        break;
                    }
                }
            }

            delay(10);
        }
    }

    udp.stop();

    if (found) {
        Serial.print("Python receiver found at: ");
        Serial.println(serverIP);
    } else {
        Serial.println("No Python receiver found.");
    }

    return found;
}

// =====================================================
// 15. UPLOAD FIVE READINGS
// =====================================================

void uploadMeasurement()
{
    if (!measurementAvailable) {
        showMessage(
            "NO MEASUREMENT",
            "MEASURE FIRST"
        );
        delay(1500);
        restoreDisplay();
        return;
    }

    if (measurementUploaded) {
        Serial.println(
            "This measurement was already uploaded."
        );
        showMessage(
            "ALREADY SENT",
            "MEASURE AGAIN"
        );
        delay(1500);
        restoreDisplay();
        return;
    }

    showMessage("CONNECTING...", "PLEASE WAIT");

    if (!connectWiFi()) {
        showMessage("WIFI FAILED", "CHECK HOTSPOT");
        delay(1800);
        restoreDisplay();
        return;
    }

    showMessage("FINDING LAPTOP...", "PLEASE WAIT");

    IPAddress serverIP;

    if (!discoverLaptop(serverIP)) {
        showMessage("LAPTOP NOT FOUND", "CHECK RECEIVER");
        delay(1800);
        restoreDisplay();
        return;
    }

    showMessage("UPLOADING...", "PLEASE WAIT");

    WiFiClient client;
    client.setTimeout(3000);

    if (!client.connect(serverIP, TCP_PORT, 3000)) {
        Serial.println("TCP connection failed.");
        showMessage("UPLOAD FAILED", "TCP ERROR");
        delay(1800);
        restoreDisplay();
        return;
    }

    // Send only five readings, no final depth.
    String message;

    for (int i = 0; i < FILTERED_COUNT; i++) {
        if (i > 0) message += ",";

        message += String(storedReadings[i], 3);
    }

    Serial.print("Sending: ");
    Serial.println(message);

    client.println(message);
    client.flush();

    // Wait for Python's acknowledgement.
    unsigned long start = millis();
    String reply;

    while (millis() - start < 3000) {
        if (client.available()) {
            reply = client.readStringUntil('\n');
            reply.trim();
            break;
        }

        if (!client.connected())
            break;

        delay(10);
    }

    client.stop();

    if (reply == "ACK") {
        Serial.println("Python acknowledged upload.");
        measurementUploaded = true;
        showMessage("UPLOADED!", "5 READINGS SENT");
    } else {
        Serial.println(
            "Upload not acknowledged; retry possible."
        );
        showMessage("UPLOAD FAILED", "NO ACK RECEIVED");
    }

    delay(1800);
    restoreDisplay();
}

// =====================================================
// 16. BUTTON HANDLING
// =====================================================

struct ButtonState {
    uint8_t pin;
    bool lastRaw;
    bool stable;
    unsigned long changedAt;
};

ButtonState measureButton = {
    MEASURE_BUTTON_PIN, HIGH, HIGH, 0
};

ButtonState uploadButton = {
    UPLOAD_BUTTON_PIN, HIGH, HIGH, 0
};

bool pressed(ButtonState &button)
{
    bool raw = digitalRead(button.pin);

    if (raw != button.lastRaw) {
        button.changedAt = millis();
        button.lastRaw = raw;
    }

    if (millis() - button.changedAt >= 35 &&
        raw != button.stable) {
        button.stable = raw;

        if (raw == LOW)
            return true;
    }

    return false;
}

// =====================================================
// 17. SETUP
// =====================================================

void setup()
{
    Serial.begin(115200);
    delay(1000);

    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setClock(100000);

    pinMode(MEASURE_BUTTON_PIN, INPUT_PULLUP);
    pinMode(UPLOAD_BUTTON_PIN, INPUT_PULLUP);

    oledReady = display.begin(
        SSD1306_SWITCHCAPVCC,
        OLED_ADDRESS
    );

    if (oledReady)
        showReady();
    else
        Serial.println("OLED initialization failed.");

    WiFi.mode(WIFI_STA);

    Serial.println();
    Serial.println("=============================");
    Serial.println("       TREAD DEPTH V3.1");
    Serial.println("=============================");
    Serial.println("GPIO4: MEASURE");
    Serial.println("GPIO5: UPLOAD");
    Serial.println("15 samples -> 5 grouped medians");
    Serial.println("Reference: 30.000 mm");
    Serial.println("UDP discovery: port 5001");
    Serial.println("TCP upload: port 5000");
    Serial.println("No laptop IP required.");
    Serial.println("=============================");
}

// =====================================================
// 18. MAIN LOOP
// =====================================================

void loop()
{
    bool measurePressed = pressed(measureButton);
    bool uploadPressed = pressed(uploadButton);

    if (measurePressed) {
        captureMeasurement();
    }
    else if (uploadPressed) {
        uploadMeasurement();
    }

    delay(5);
}
