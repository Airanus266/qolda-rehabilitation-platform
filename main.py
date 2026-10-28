import time
import network
import socket
import math

try:
    import uos as os
except ImportError:
    import os

try:
    import ujson as json
except ImportError:
    import json


# ============================================================
# QOLDA 4-FINGER WIFI CORE v5 — PERSISTENT CALIBRATION
#
# Fixes:
# - Wi-Fi starts before rc_module
# - Uses only 6 analog X12 channels
# - Calibrates the FULL movement pattern of each finger
# - Classifies thumb/index/ring/pinky by pattern, not by one channel
# - Suppresses accidental cross-talk and duplicate channel mapping
# - Saves full finger calibration after the first USB setup
# - Loads calibration automatically on battery-only startup
# - Performs only a short automatic relaxed-hand zero after reboot
# ============================================================

WIFI_NAME = "QOLDA"
WIFI_PASSWORD = "12345678"

LAPTOP_IP = "192.168.137.1"
UDP_PORT = 5005

ANALOG_CHANNELS = (0, 1, 2, 3, 4, 5)
FINGER_NAMES = ("thumb", "index", "ring", "pinky")
FINGER_LABELS = {
    "thumb": "THUMB",
    "index": "INDEX",
    "ring": "RING",
    "pinky": "PINKY",
}

SEND_INTERVAL_MS = 45
DEBUG_INTERVAL_MS = 800

PRESS_THRESHOLD = 0.18
RELEASE_THRESHOLD = 0.09
MIN_SIMILARITY = 0.50
SWITCH_MARGIN = 0.10
MIN_CALIBRATION_NORM = 90.0

# Full four-finger calibration is saved here after the first USB run.
CALIBRATION_FILE = "qolda_calibration.json"
CALIBRATION_TEMP_FILE = "qolda_calibration.tmp"
CALIBRATION_VERSION = 1

# Normally keep this False.
# To recalibrate later, delete qolda_calibration.json from the board.
FORCE_RECALIBRATION = False


def clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def now_ms():
    try:
        return int(time.ticks_ms())
    except Exception:
        return int(time.time() * 1000)


def diff_ms(a, b):
    try:
        return time.ticks_diff(a, b)
    except Exception:
        return a - b


def vector_norm(values):
    total = 0.0
    for value in values:
        total += value * value
    return math.sqrt(total)


def dot(a, b):
    total = 0.0
    for i in range(len(a)):
        total += a[i] * b[i]
    return total


def connect_wifi(timeout_seconds=25):
    wifi = network.WLAN(network.STA_IF)
    time.sleep(2)

    while not wifi.active():
        try:
            print("Activating Wi-Fi...")
            wifi.active(True)
        except OSError as error:
            print("WIFI ACTIVATE ERROR:", error)
            time.sleep(2)

    if wifi.isconnected():
        print("Wi-Fi already connected:", wifi.ifconfig())
        return wifi

    attempt = 0

    while not wifi.isconnected():
        attempt += 1
        print("Connecting to Wi-Fi:", WIFI_NAME, "| attempt", attempt)

        try:
            try:
                wifi.disconnect()
            except Exception:
                pass

            time.sleep(1)
            wifi.connect(WIFI_NAME, WIFI_PASSWORD)

            start = time.time()

            while not wifi.isconnected():
                if time.time() - start >= timeout_seconds:
                    print("Wi-Fi attempt timed out")
                    break

                print("Wi-Fi status:", wifi.status())
                time.sleep(1)

        except OSError as error:
            print("WIFI INTERNAL ERROR:", error)

        except Exception as error:
            print("WIFI ERROR:", error)

        if not wifi.isconnected():
            print("Retrying Wi-Fi in 3 seconds...")
            time.sleep(3)

    print("WIFI CONNECTED:", wifi.ifconfig())
    return wifi


# ------------------------------------------------------------
# 1. Wi-Fi first
# ------------------------------------------------------------

wifi = connect_wifi()
udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_target = (LAPTOP_IP, UDP_PORT)

# ------------------------------------------------------------
# 2. Import and initialize rc_module only after Wi-Fi
# ------------------------------------------------------------

print("Importing RC module...")
import rc_module

print("RC MODULE IMPORTED")
rc_module.rc_master_init()
print("RC MODULE STARTED")

if not wifi.isconnected():
    print("Wi-Fi dropped after RC init. Reconnecting...")
    wifi = connect_wifi()


def read_all():
    data = rc_module.rc_master_data()

    if not data:
        return None

    if len(data) < 6:
        return None

    return data


def read_analog(data):
    return [float(data[channel]) for channel in ANALOG_CHANNELS]


def average_open(duration_ms=1600):
    print("KEEP ALL FINGERS RELAXED...")

    sums = [0.0 for _ in ANALOG_CHANNELS]
    count = 0
    start = now_ms()

    while diff_ms(now_ms(), start) < duration_ms:
        data = read_all()

        if data:
            analog = read_analog(data)

            for i in range(len(analog)):
                sums[i] += analog[i]

            count += 1

        time.sleep(0.025)

    if count == 0:
        raise RuntimeError("No X12 analog data during open calibration")

    return [value / count for value in sums]


def capture_signature(name, open_values, duration_ms=2400):
    label = FINGER_LABELS[name]

    print("")
    print("RELAX ALL FINGERS")
    time.sleep(1.0)

    print("PRESS ONLY", label, "NOW")
    time.sleep(0.35)

    top_samples = []
    start = now_ms()

    while diff_ms(now_ms(), start) < duration_ms:
        data = read_all()

        if data:
            analog = read_analog(data)
            delta = [
                analog[i] - open_values[i]
                for i in range(len(ANALOG_CHANNELS))
            ]

            energy = 0.0
            for value in delta:
                energy += value * value

            top_samples.append((energy, delta))
            top_samples.sort(key=lambda item: item[0], reverse=True)

            if len(top_samples) > 14:
                top_samples.pop()

        time.sleep(0.025)

    if not top_samples:
        raise RuntimeError("No samples captured for " + label)

    signature = [0.0 for _ in ANALOG_CHANNELS]

    for _, sample in top_samples:
        for i in range(len(signature)):
            signature[i] += sample[i]

    sample_count = len(top_samples)

    for i in range(len(signature)):
        signature[i] /= sample_count

    norm = vector_norm(signature)

    print(label, "signature:", [int(value) for value in signature])
    print(label, "norm:", int(norm))

    if norm < MIN_CALIBRATION_NORM:
        print("WARNING:", label, "movement was weak. Repeat calibration carefully.")

    return signature


def choose_unique_channels(signatures):
    # Diagnostic mapping only. Runtime classification uses full signatures.
    best_score = -1.0
    best_map = None

    for thumb_ch in ANALOG_CHANNELS:
        for index_ch in ANALOG_CHANNELS:
            if index_ch == thumb_ch:
                continue

            for ring_ch in ANALOG_CHANNELS:
                if ring_ch == thumb_ch or ring_ch == index_ch:
                    continue

                for pinky_ch in ANALOG_CHANNELS:
                    if (
                        pinky_ch == thumb_ch
                        or pinky_ch == index_ch
                        or pinky_ch == ring_ch
                    ):
                        continue

                    candidate = {
                        "thumb": thumb_ch,
                        "index": index_ch,
                        "ring": ring_ch,
                        "pinky": pinky_ch,
                    }

                    score = 0.0

                    for finger in FINGER_NAMES:
                        score += abs(signatures[finger][candidate[finger]])

                    if score > best_score:
                        best_score = score
                        best_map = candidate

    return best_map


def file_exists(path):
    try:
        os.stat(path)
        return True
    except Exception:
        return False


def safe_remove(path):
    try:
        os.remove(path)
    except Exception:
        pass


def validate_saved_calibration(payload):
    if not isinstance(payload, dict):
        raise ValueError("Calibration payload is not an object")

    if int(payload.get("version", -1)) != CALIBRATION_VERSION:
        raise ValueError("Calibration version mismatch")

    saved_channels = payload.get("channels")

    if list(saved_channels or []) != list(ANALOG_CHANNELS):
        raise ValueError("Analog channel layout mismatch")

    saved_signatures = payload.get("signatures")

    if not isinstance(saved_signatures, dict):
        raise ValueError("Missing signatures")

    signatures = {}

    for finger_name in FINGER_NAMES:
        values = saved_signatures.get(finger_name)

        if not isinstance(values, (list, tuple)):
            raise ValueError("Missing signature for " + finger_name)

        if len(values) != len(ANALOG_CHANNELS):
            raise ValueError("Wrong signature length for " + finger_name)

        vector = [float(value) for value in values]

        if vector_norm(vector) < MIN_CALIBRATION_NORM * 0.45:
            raise ValueError("Saved signature is too weak for " + finger_name)

        signatures[finger_name] = vector

    channel_map = payload.get("channel_map")

    if not isinstance(channel_map, dict):
        channel_map = choose_unique_channels(signatures)

    validated_map = {}
    used_channels = []

    for finger_name in FINGER_NAMES:
        channel = int(channel_map.get(finger_name, -1))

        if channel not in ANALOG_CHANNELS:
            raise ValueError("Invalid channel for " + finger_name)

        if channel in used_channels:
            raise ValueError("Duplicate diagnostic channel mapping")

        used_channels.append(channel)
        validated_map[finger_name] = channel

    return signatures, validated_map


def load_saved_calibration():
    if FORCE_RECALIBRATION:
        print("FORCE_RECALIBRATION is enabled")
        return None

    if not file_exists(CALIBRATION_FILE):
        print("No saved finger calibration found")
        return None

    try:
        with open(CALIBRATION_FILE, "r") as calibration_file:
            payload = json.loads(calibration_file.read())

        signatures, channel_map = validate_saved_calibration(payload)

        print("SAVED FINGER CALIBRATION LOADED")
        print("UNIQUE CHANNEL MAP:", channel_map)

        return signatures, channel_map

    except Exception as error:
        print("SAVED CALIBRATION ERROR:", error)
        print("A new full calibration will be created")
        return None


def save_calibration(signatures, channel_map, reference_open_values):
    payload = {
        "version": CALIBRATION_VERSION,
        "channels": list(ANALOG_CHANNELS),
        "fingers": list(FINGER_NAMES),
        "signatures": signatures,
        "channel_map": channel_map,
        "reference_open_values": reference_open_values,
    }

    encoded = json.dumps(payload)

    safe_remove(CALIBRATION_TEMP_FILE)

    with open(CALIBRATION_TEMP_FILE, "w") as calibration_file:
        calibration_file.write(encoded)

    # Replace the old file only after the temporary file was written.
    safe_remove(CALIBRATION_FILE)
    os.rename(CALIBRATION_TEMP_FILE, CALIBRATION_FILE)

    print("CALIBRATION SAVED TO:", CALIBRATION_FILE)


def template_metrics(current_delta, signature):
    signature_norm = vector_norm(signature)
    current_norm = vector_norm(current_delta)

    if signature_norm <= 0.001 or current_norm <= 0.001:
        return 0.0, 0.0, 0.0

    projection = dot(current_delta, signature)
    amplitude = projection / (signature_norm * signature_norm)
    similarity = projection / (current_norm * signature_norm)

    amplitude = clamp(amplitude, 0.0, 1.25)
    similarity = clamp(similarity, -1.0, 1.0)

    if similarity <= 0.0:
        score = 0.0
    else:
        score = amplitude * similarity * similarity

    return amplitude, similarity, score


print("")
print("QOLDA CALIBRATION MANAGER STARTED")

saved_calibration = load_saved_calibration()

# A short automatic rest zero is measured on every startup.
# This compensates for battery level, temperature and sensor baseline drift.
# No USB is required: simply keep the hand relaxed for about 2 seconds.
print("")
print("AUTO-ZERO: KEEP ALL FINGERS RELAXED")
open_values = average_open()
print("CURRENT OPEN VALUES:", [int(value) for value in open_values])

if saved_calibration is not None:
    signatures, channel_map = saved_calibration
    calibration_source = "saved"

    print("")
    print("FULL FINGER CALIBRATION: LOADED FROM MEMORY")
    print("NO THUMB/INDEX/RING/PINKY RECALIBRATION REQUIRED")

else:
    calibration_source = "new"
    signatures = {}

    print("")
    print("FIRST FULL CALIBRATION REQUIRED")
    print("FOLLOW THE USB CONSOLE INSTRUCTIONS")

    for finger_name in FINGER_NAMES:
        signatures[finger_name] = capture_signature(
            finger_name,
            open_values,
        )

    channel_map = choose_unique_channels(signatures)

    print("")
    print("FULL CALIBRATION COMPLETE")
    print("UNIQUE CHANNEL MAP:", channel_map)
    print("IMPORTANT: runtime uses full signatures, so INDEX cannot reuse THUMB mapping.")

    try:
        save_calibration(
            signatures,
            channel_map,
            open_values,
        )
    except Exception as error:
        print("CALIBRATION SAVE ERROR:", error)
        print("The glove will work now, but calibration was not saved.")

print("")
print("QOLDA READY")
print("RELAX ALL FINGERS")
time.sleep(1.5)


active_finger = "none"
active_since_ms = None
last_change_ms = now_ms()

smooth_values = {
    "thumb": 0.0,
    "index": 0.0,
    "ring": 0.0,
    "pinky": 0.0,
}

is_bent = {
    "thumb": False,
    "index": False,
    "ring": False,
    "pinky": False,
}

last_send_ms = 0
last_debug_ms = 0
packet_sequence = 0


while True:
    data = read_all()

    if data:
        current_ms = now_ms()
        analog = read_analog(data)

        current_delta = [
            analog[i] - open_values[i]
            for i in range(len(ANALOG_CHANNELS))
        ]

        metrics = {}
        candidate = "none"
        candidate_score = 0.0
        candidate_amplitude = 0.0
        candidate_similarity = 0.0

        for finger_name in FINGER_NAMES:
            amplitude, similarity, score = template_metrics(
                current_delta,
                signatures[finger_name],
            )

            metrics[finger_name] = (
                amplitude,
                similarity,
                score,
            )

            if score > candidate_score:
                candidate = finger_name
                candidate_score = score
                candidate_amplitude = amplitude
                candidate_similarity = similarity

        previous_active = active_finger

        if active_finger == "none":
            if (
                candidate != "none"
                and candidate_amplitude >= PRESS_THRESHOLD
                and candidate_similarity >= MIN_SIMILARITY
                and diff_ms(current_ms, last_change_ms) >= 100
            ):
                active_finger = candidate
                active_since_ms = current_ms
                last_change_ms = current_ms

        else:
            active_amp, active_sim, active_score = metrics[active_finger]

            should_release = (
                active_amp <= RELEASE_THRESHOLD
                or active_sim < 0.25
            )

            should_switch = (
                candidate != "none"
                and candidate != active_finger
                and candidate_amplitude >= PRESS_THRESHOLD
                and candidate_similarity >= MIN_SIMILARITY
                and candidate_score >= active_score + SWITCH_MARGIN
            )

            if should_release and diff_ms(current_ms, last_change_ms) >= 100:
                active_finger = "none"
                active_since_ms = None
                last_change_ms = current_ms

            elif should_switch and diff_ms(current_ms, last_change_ms) >= 120:
                active_finger = candidate
                active_since_ms = current_ms
                last_change_ms = current_ms

        target_values = {
            "thumb": 0.0,
            "index": 0.0,
            "ring": 0.0,
            "pinky": 0.0,
        }

        if active_finger != "none":
            active_amp, active_sim, active_score = metrics[active_finger]
            target_values[active_finger] = clamp(active_amp, 0.0, 1.0)

        for finger_name in FINGER_NAMES:
            if finger_name == active_finger:
                smooth_values[finger_name] = (
                    smooth_values[finger_name] * 0.48
                    + target_values[finger_name] * 0.52
                )
            else:
                smooth_values[finger_name] *= 0.38

                if smooth_values[finger_name] < 0.015:
                    smooth_values[finger_name] = 0.0

        just_bent = {
            "thumb": False,
            "index": False,
            "ring": False,
            "pinky": False,
        }

        just_released = {
            "thumb": False,
            "index": False,
            "ring": False,
            "pinky": False,
        }

        for finger_name in FINGER_NAMES:
            was_bent = is_bent[finger_name]
            now_bent = (
                finger_name == active_finger
                and smooth_values[finger_name] >= PRESS_THRESHOLD
            )

            is_bent[finger_name] = now_bent
            just_bent[finger_name] = (not was_bent) and now_bent
            just_released[finger_name] = was_bent and (not now_bent)

        if active_finger != "none" and active_since_ms is not None:
            global_hold_ms = diff_ms(current_ms, active_since_ms)
        else:
            global_hold_ms = 0

        primary_value = 0.0

        if active_finger != "none":
            primary_value = smooth_values[active_finger]

        global_grip = (
            smooth_values["thumb"]
            + smooth_values["index"]
            + smooth_values["ring"]
            + smooth_values["pinky"]
        ) / 4.0

        packet = {
            "seq": packet_sequence,
            "calibration_source": calibration_source,
            "autonomous": 1,

            "global_grip": round(global_grip, 3),
            "global_bent": int(active_finger != "none"),
            "global_hold": global_hold_ms,

            "primary": active_finger,
            "primary_value": round(primary_value, 3),

            "thumb": round(smooth_values["thumb"], 3),
            "index": round(smooth_values["index"], 3),
            "ring": round(smooth_values["ring"], 3),
            "pinky": round(smooth_values["pinky"], 3),
            "middle": round(smooth_values["pinky"], 3),

            "thumb_bent": int(is_bent["thumb"]),
            "index_bent": int(is_bent["index"]),
            "ring_bent": int(is_bent["ring"]),
            "pinky_bent": int(is_bent["pinky"]),
            "middle_bent": int(is_bent["pinky"]),

            "thumb_jb": int(just_bent["thumb"]),
            "index_jb": int(just_bent["index"]),
            "ring_jb": int(just_bent["ring"]),
            "pinky_jb": int(just_bent["pinky"]),

            "thumb_jr": int(just_released["thumb"]),
            "index_jr": int(just_released["index"]),
            "ring_jr": int(just_released["ring"]),
            "pinky_jr": int(just_released["pinky"]),

            "raw_thumb": int(analog[channel_map["thumb"]]),
            "raw_index": int(analog[channel_map["index"]]),
            "raw_ring": int(analog[channel_map["ring"]]),
            "raw_pinky": int(analog[channel_map["pinky"]]),

            "index_similarity": round(metrics["index"][1], 3),
            "thumb_similarity": round(metrics["thumb"][1], 3),
        }

        if diff_ms(current_ms, last_send_ms) >= SEND_INTERVAL_MS:
            try:
                if not wifi.isconnected():
                    print("Wi-Fi disconnected. Reconnecting...")
                    wifi = connect_wifi(timeout_seconds=15)

                payload = json.dumps(packet).encode("utf-8")
                udp_socket.sendto(payload, udp_target)

                packet_sequence += 1
                last_send_ms = current_ms

            except Exception as error:
                print("UDP SEND ERROR:", error)
                time.sleep(0.2)

        if diff_ms(current_ms, last_debug_ms) >= DEBUG_INTERVAL_MS:
            print(
                "TX",
                packet_sequence,
                "| primary:",
                active_finger,
                "| thumb:",
                round(smooth_values["thumb"], 2),
                "| index:",
                round(smooth_values["index"], 2),
                "| ring:",
                round(smooth_values["ring"], 2),
                "| pinky:",
                round(smooth_values["pinky"], 2),
                "| I-sim:",
                round(metrics["index"][1], 2),
                "| T-sim:",
                round(metrics["thumb"][1], 2),
            )

            last_debug_ms = current_ms

    time.sleep(0.025)
