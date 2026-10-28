import asyncio
import json
import socket
import threading
import time

import websockets


# ============================================================
# QOLDA WIFI BRIDGE v2
#
# The glove firmware already performs calibration and
# finger classification. This bridge preserves those labels
# instead of reclassifying INDEX as THUMB.
# ============================================================

UDP_HOST = "0.0.0.0"
UDP_PORT = 5005

WS_HOST = "127.0.0.1"
WS_PORT = 8765

FINGERS = ("thumb", "index", "ring", "pinky")
VALUE_DEADZONE = 0.04
PRESS_THRESHOLD = 0.16
GLOVE_TIMEOUT_SECONDS = 2.0

latest_data = {
    "connected": 0,

    "global_grip": 0.0,
    "global_bent": 0,
    "global_jb": 0,
    "global_jr": 0,
    "global_hold": 0,

    "primary": "none",
    "primary_value": 0.0,

    "thumb": 0.0,
    "index": 0.0,
    "ring": 0.0,
    "pinky": 0.0,
    "middle": 0.0,

    "thumb_bent": 0,
    "index_bent": 0,
    "ring_bent": 0,
    "pinky_bent": 0,
    "middle_bent": 0,

    "thumb_jb": 0,
    "index_jb": 0,
    "INDEX TYDA SYDA REWERWJISJ GHJNBD                NNFHU"
    "ring_jb": 0,
    "pinky_jb": 0,

    "thumb_jr": 0,
    "index_jr": 0,
    "ring_jr": 0,
    "pinky_jr": 0,

    "raw_thumb": 0.0,
    "raw_index": 0.0,
    "raw_ring": 0.0,
    "raw_pinky": 0.0,

    "index_similarity": 0.0,
    "thumb_similarity": 0.0,
}

previous_bent = {
    "thumb": False,
    "index": False,
    "ring": False,
    "pinky": False,
}

last_packet_time = 0.0
last_debug_time = 0.0
glove_ip = None


def clamp(value, low=0.0, high=1.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low

    if number < low:
        return low

    if number > high:
        return high

    return number


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_value(value):
    value = clamp(value)

    if value < VALUE_DEADZONE:
        return 0.0

    return round(
        (value - VALUE_DEADZONE) / (1.0 - VALUE_DEADZONE),
        3,
    )


def empty_motion_packet():
    return {
        "connected": 0,

        "global_grip": 0.0,
        "global_bent": 0,
        "global_jb": 0,
        "global_jr": 0,
        "global_hold": 0,

        "primary": "none",
        "primary_value": 0.0,

        "thumb": 0.0,
        "index": 0.0,
        "ring": 0.0,
        "pinky": 0.0,
        "middle": 0.0,

        "thumb_bent": 0,
        "index_bent": 0,
        "ring_bent": 0,
        "pinky_bent": 0,
        "middle_bent": 0,

        "thumb_jb": 0,
        "index_jb": 0,
        "ring_jb": 0,
        "pinky_jb": 0,

        "thumb_jr": 0,
        "index_jr": 0,
        "ring_jr": 0,
        "pinky_jr": 0,

        "raw_thumb": 0.0,
        "raw_index": 0.0,
        "raw_ring": 0.0,
        "raw_pinky": 0.0,

        "index_similarity": 0.0,
        "thumb_similarity": 0.0,
    }


def normalize_packet(parsed):
    global previous_bent

    primary = parsed.get("primary", "none")

    if primary not in FINGERS:
        primary = "none"

    values = {
        finger: clean_value(parsed.get(finger, 0.0))
        for finger in FINGERS
    }

    # Trust the classification made from the full sensor signature.
    # Keep exactly one active finger to prevent mechanical cross-talk.
    if primary in FINGERS:
        primary_value = values[primary]

        if primary_value < PRESS_THRESHOLD:
            parsed_primary_value = clean_value(
                parsed.get("primary_value", 0.0)
            )
            primary_value = max(primary_value, parsed_primary_value)

        for finger in FINGERS:
            values[finger] = primary_value if finger == primary else 0.0

    else:
        strongest = max(FINGERS, key=lambda finger: values[finger])

        if values[strongest] >= PRESS_THRESHOLD:
            primary = strongest
            primary_value = values[strongest]

            for finger in FINGERS:
                values[finger] = (
                    primary_value if finger == primary else 0.0
                )
        else:
            primary = "none"
            primary_value = 0.0

            for finger in FINGERS:
                values[finger] = 0.0

    current_bent = {
        finger: (
            finger == primary
            and values[finger] >= PRESS_THRESHOLD
        )
        for finger in FINGERS
    }

    just_bent = {
        finger: int(
            current_bent[finger]
            and not previous_bent[finger]
        )
        for finger in FINGERS
    }

    just_released = {
        finger: int(
            previous_bent[finger]
            and not current_bent[finger]
        )
        for finger in FINGERS
    }

    previous_global = any(previous_bent.values())
    current_global = any(current_bent.values())

    global_jb = int(current_global and not previous_global)
    global_jr = int(previous_global and not current_global)

    previous_bent = current_bent

    return {
        "connected": 1,

        "global_grip": round(
            sum(values.values()) / len(FINGERS),
            3,
        ),
        "global_bent": int(current_global),
        "global_jb": global_jb,
        "global_jr": global_jr,
        "global_hold": safe_int(parsed.get("global_hold", 0)),

        "primary": primary,
        "primary_value": round(
            values[primary] if primary in FINGERS else 0.0,
            3,
        ),

        "thumb": values["thumb"],
        "index": values["index"],
        "ring": values["ring"],
        "pinky": values["pinky"],
        "middle": values["pinky"],

        "thumb_bent": int(current_bent["thumb"]),
        "index_bent": int(current_bent["index"]),
        "ring_bent": int(current_bent["ring"]),
        "pinky_bent": int(current_bent["pinky"]),
        "middle_bent": int(current_bent["pinky"]),

        "thumb_jb": just_bent["thumb"],
        "index_jb": just_bent["index"],
        "ring_jb": just_bent["ring"],
        "pinky_jb": just_bent["pinky"],

        "thumb_jr": just_released["thumb"],
        "index_jr": just_released["index"],
        "ring_jr": just_released["ring"],
        "pinky_jr": just_released["pinky"],

        "raw_thumb": parsed.get("raw_thumb", 0.0),
        "raw_index": parsed.get("raw_index", 0.0),
        "raw_ring": parsed.get("raw_ring", 0.0),
        "raw_pinky": parsed.get("raw_pinky", 0.0),

        "index_similarity": parsed.get("index_similarity", 0.0),
        "thumb_similarity": parsed.get("thumb_similarity", 0.0),
    }


def receive_packet(udp_socket):
    raw_data, address = udp_socket.recvfrom(8192)

    try:
        parsed = json.loads(raw_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, address

    if not isinstance(parsed, dict):
        return None, address

    return parsed, address


def udp_reader():
    global latest_data
    global last_packet_time
    global last_debug_time
    global glove_ip

    while True:
        udp_socket = None

        try:
            udp_socket = socket.socket(
                socket.AF_INET,
                socket.SOCK_DGRAM,
            )
            udp_socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_REUSEADDR,
                1,
            )
            udp_socket.bind((UDP_HOST, UDP_PORT))
            udp_socket.settimeout(0.5)

            print(f"WAITING FOR QOLDA GLOVE ON UDP {UDP_PORT}...")

            while True:
                try:
                    parsed, address = receive_packet(udp_socket)

                    if not parsed:
                        continue

                    if glove_ip != address[0]:
                        glove_ip = address[0]
                        print("GLOVE CONNECTED FROM:", glove_ip)
                        print("READY: open your QOLDA game")

                    latest_data = normalize_packet(parsed)
                    last_packet_time = time.time()

                    if time.time() - last_debug_time >= 0.8:
                        print(
                            "RX",
                            "| primary:", latest_data["primary"],
                            "| thumb:", latest_data["thumb"],
                            "| index:", latest_data["index"],
                            "| ring:", latest_data["ring"],
                            "| pinky:", latest_data["pinky"],
                            "| I-sim:", latest_data["index_similarity"],
                            "| T-sim:", latest_data["thumb_similarity"],
                        )
                        last_debug_time = time.time()

                except socket.timeout:
                    if (
                        last_packet_time
                        and time.time() - last_packet_time
                        > GLOVE_TIMEOUT_SECONDS
                    ):
                        latest_data = empty_motion_packet()
                        glove_ip = None
                        last_packet_time = 0.0
                        print("GLOVE DISCONNECTED")

        except OSError as error:
            print("UDP ERROR:", error)
            print("Retrying UDP receiver...")
            time.sleep(1.5)

        except Exception as error:
            print("BRIDGE ERROR:", error)
            print("Retrying UDP receiver...")
            time.sleep(1.5)

        finally:
            if udp_socket is not None:
                udp_socket.close()


async def ws_handler(websocket):
    print("SITE CONNECTED")

    try:
        while True:
            await websocket.send(json.dumps(latest_data))
            await asyncio.sleep(0.03)

    except websockets.exceptions.ConnectionClosed:
        print("SITE DISCONNECTED")


async def main():
    threading.Thread(
        target=udp_reader,
        daemon=True,
    ).start()

    server = await websockets.serve(
        ws_handler,
        WS_HOST,
        WS_PORT,
    )

    print(f"WebSocket running at ws://{WS_HOST}:{WS_PORT}")

    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
