import time
import json
from finger_state import FingerState


class GloveEngine:
    def __init__(self, config=None):
        self.config = config or self._default_config()

        self.session_start_ms = self._now_ms()
        self.frame_index = 0

        self.fingers = {
            "thumb": FingerState("thumb", **self.config["thumb"]),
            "index": FingerState("index", **self.config["index"]),
            "middle": FingerState("middle", **self.config["middle"]),
            "ring": FingerState("ring", **self.config["ring"]),
            "pinky": FingerState("pinky", **self.config["pinky"]),
        }

        self.last_packet_ms = self._now_ms()

    def _now_ms(self):
        try:
            return int(time.ticks_ms())  # MicroPython
        except:
            return int(time.time() * 1000)  # PC Python

    def _ticks_diff(self, now_ms, past_ms):
        try:
            return time.ticks_diff(now_ms, past_ms)  # MicroPython
        except:
            return now_ms - past_ms  # PC Python

    def _default_config(self):
        base = {
            "open_min": 100,
            "bend_max": 900,
            "bend_threshold": 0.60,
            "release_threshold": 0.30,
            "smoothing_alpha": 0.35,
            "dead_zone": 0.01,
        }

        return {
            "thumb": dict(base),
            "index": dict(base),
            "middle": dict(base),
            "ring": dict(base),
            "pinky": dict(base),
        }

    def update(self, raw_inputs):
        self.frame_index += 1

        for finger_name, finger_state in self.fingers.items():
            raw_value = raw_inputs.get(finger_name, finger_state.raw)
            finger_state.update(raw_value)

    def get_active_fingers(self):
        return [name for name, finger in self.fingers.items() if finger.is_bent]

    def get_just_bent_fingers(self):
        return [name for name, finger in self.fingers.items() if finger.just_bent]

    def get_just_released_fingers(self):
        return [name for name, finger in self.fingers.items() if finger.just_released]

    def get_primary_active_finger(self):
        best_name = None
        best_value = -1.0

        for name, finger in self.fingers.items():
            if finger.filtered > best_value:
                best_value = finger.filtered
                best_name = name

        return best_name, round(best_value, 4)

    def get_average_flex(self):
        total = 0.0
        for finger in self.fingers.values():
            total += finger.filtered
        return round(total / 5.0, 4)

    def get_flex_spread(self):
        values = [finger.filtered for finger in self.fingers.values()]
        return round(max(values) - min(values), 4)

    def get_coactivation_score(self, target_finger=None):
        if target_finger is None or target_finger not in self.fingers:
            active = [finger.filtered for finger in self.fingers.values()]
            return round(sum(active) / len(active), 4)

        others = []
        for name, finger in self.fingers.items():
            if name != target_finger:
                others.append(finger.filtered)

        if not others:
            return 0.0

        return round(sum(others) / len(others), 4)

    def build_packet(self):
        now_ms = self._now_ms()
        elapsed_ms = self._ticks_diff(now_ms, self.session_start_ms)

        primary_finger, primary_value = self.get_primary_active_finger()

        packet = {
            "t": elapsed_ms,
            "frame": self.frame_index,
            "summary": {
                "activeFingers": self.get_active_fingers(),
                "justBent": self.get_just_bent_fingers(),
                "justReleased": self.get_just_released_fingers(),
                "primaryFinger": primary_finger,
                "primaryValue": primary_value,
                "avgFlex": self.get_average_flex(),
                "flexSpread": self.get_flex_spread(),
            },
            "fingers": {}
        }

        for name, finger in self.fingers.items():
            packet["fingers"][name] = finger.to_dict()

        return packet

    def to_json_line(self):
        return json.dumps(self.build_packet(), ensure_ascii=False)