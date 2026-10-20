
import socket
import threading
from datetime import datetime

# ================================================
# CONFIGURATION
# ================================================

HOST = "0.0.0.0"

DISCOVERY_PORT = 5001
TCP_PORT = 5000

DISCOVERY_REQUEST = "FIND_TREADDEPTH_SERVER"
DISCOVERY_RESPONSE = "TREADDEPTH_SERVER_READY"


# ================================================
# UDP DISCOVERY SERVER
# ================================================

def discovery_server():
    udp = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    udp.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1
    )

    udp.bind((HOST, DISCOVERY_PORT))

    print(
        f"Discovery listening on UDP {DISCOVERY_PORT}"
    )

    while True:
        data, address = udp.recvfrom(1024)

        message = data.decode(
            "utf-8", errors="replace"
        ).strip()

        if message == DISCOVERY_REQUEST:
            print(
                f"\nDiscovery request from {address[0]}"
            )

            udp.sendto(
                DISCOVERY_RESPONSE.encode("utf-8"),
                address
            )

            print("Discovery reply sent.")


# ================================================
# TCP MEASUREMENT RECEIVER
# ================================================

def measurement_server():
    server = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    )

    server.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1
    )

    server.bind((HOST, TCP_PORT))
    server.listen(5)

    print(
        f"Measurement receiver on TCP {TCP_PORT}"
    )

    print("\nWaiting for ESP32 uploads...\n")

    while True:
        conn, address = server.accept()

        with conn:
            conn.settimeout(5)

            try:
                # Receive one newline-terminated record
                chunks = []
                total = 0

                while total < 1024:
                    chunk = conn.recv(1024)

                    if not chunk:
                        break

                    chunks.append(chunk)
                    total += len(chunk)

                    if b"\n" in chunk:
                        break

                message = b"".join(chunks)
                message = message.split(b"\n", 1)[0]
                message = message.decode(
                    "utf-8"
                ).strip()

                values = [
                    float(item.strip())
                    for item in message.split(",")
                ]

                if len(values) != 5:
                    raise ValueError(
                        "Expected exactly five readings"
                    )

                from math import isfinite

                if not all(isfinite(x) for x in values):
                    raise ValueError(
                        "Non-finite measurement"
                    )

                print("=" * 45)
                print("TREAD DEPTH UPLOAD")
                print("=" * 45)

                print(
                    "Time:",
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )

                print("ESP32:", address[0])
                print()

                for index, depth in enumerate(
                    values, start=1
                ):
                    print(
                        f"Reading {index}: "
                        f"{depth:.3f} mm"
                    )

                print()

                # Acknowledge successful receipt
                conn.sendall(b"ACK\n")

                print("ACK sent to ESP32.")
                print("=" * 45)
                print()

            except (
                ValueError,
                UnicodeDecodeError,
                socket.timeout,
                OSError
            ) as error:
                print("Receive error:", error)


# ================================================
# MAIN
# ================================================

if __name__ == "__main__":
    print("=" * 45)
    print("TREAD DEPTH PYTHON RECEIVER V3.1")
    print("=" * 45)

    discovery_thread = threading.Thread(
        target=discovery_server,
        daemon=True
    )

    discovery_thread.start()

    measurement_server()
