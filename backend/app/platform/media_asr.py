"""Xunfei AST signing and revision-aware transcript assembly.

Protocol: https://www.xfyun.cn/doc/spark/asr_llm/rtasr_llm.html
"""

import base64
import hashlib
import hmac
import json
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode


def asr_signed_url(base_url: str, app_id: str, api_key: str, api_secret: str,
                   *, now: datetime | None = None, session_id: str | None = None) -> str:
    params = {
        "accessKeyId": api_key,
        "appId": app_id,
        "audio_encode": "pcm_s16le",
        "lang": "autodialect",
        "samplerate": "16000",
        "utc": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "uuid": session_id or uuid.uuid4().hex,
    }
    canonical = urlencode(sorted(params.items()))
    params["signature"] = base64.b64encode(
        hmac.new(api_secret.encode(), canonical.encode(), hashlib.sha1).digest()
    ).decode()
    return f"{base_url}?{urlencode(sorted(params.items()))}"


def decode_packet(raw: str | bytes | dict) -> dict:
    packet = json.loads(raw) if isinstance(raw, str | bytes) else dict(raw)
    if isinstance(packet.get("data"), str):
        packet["data"] = json.loads(packet["data"])
    return packet


def check_asr_error(packet: dict) -> None:
    code = packet.get("code")
    data = packet.get("data") or {}
    if (packet.get("action") == "error" or code not in (None, 0, "0", "000000")
            or (isinstance(data, dict) and data.get("normal") is False)):
        # Do not expose signed URLs or raw provider packets (which contain user text).
        raise ValueError(f"语音识别服务返回错误（{code or 'engine_error'}）")


@dataclass
class Segment:
    text: str
    final: bool
    begin: int | None
    end: int | None


class TranscriptAssembler:
    """Upsert sentence revisions rather than appending every provider packet.

    AST may revise a sentence with a new seg_id. Time windows identify those
    revisions. The older wpgs replacement range is supported as well. Retired
    ids prevent delayed packets from resurrecting replaced sentences.
    """

    def __init__(self) -> None:
        self.segments: dict[int, Segment] = {}
        self.retired: set[int] = set()
        self.revision = 0
        self.final = False
        self.last_snapshot: tuple[str, str, bool] | None = None

    def apply(self, raw: dict | str | bytes) -> dict | None:
        packet = decode_packet(raw)
        check_asr_error(packet)
        data = packet.get("data") or {}
        cn = data.get("cn") or packet.get("cn") or {}
        st = cn.get("st") or data.get("st") or packet.get("st") or {}
        final = data.get("ls") is True or st.get("ls") is True
        self.final = self.final or final
        segment_id = data.get("seg_id", st.get("seg_id", st.get("sn")))
        text = "".join(
            str(words["cw"][0].get("w", ""))
            for rt in st.get("rt", []) for words in rt.get("ws", []) if words.get("cw")
        )
        if segment_id is not None and text:
            self._update(int(segment_id), st, text, final)
        if self.final:
            for segment in self.segments.values():
                segment.final = True

        ordered = sorted(self.segments.items(), key=lambda item: (
            item[1].begin if item[1].begin is not None else item[0], item[0],
        ))
        committed = ""
        display = "".join(segment.text for _, segment in ordered)
        for _, segment in ordered:
            if not segment.final:
                break
            committed += segment.text
        snapshot = (display, committed, self.final)
        if snapshot == self.last_snapshot or (not display and not self.final):
            return None
        self.last_snapshot = snapshot
        self.revision += 1
        return {
            "type": "transcript", "committed_text": committed,
            "live_text": display[len(committed):], "display_text": display,
            "revision": self.revision, "final": self.final,
        }

    def _update(self, segment_id: int, st: dict, text: str, final: bool) -> None:
        if segment_id in self.retired:
            return
        sentence_final = final or str(st.get("type")) == "0"
        old = self.segments.get(segment_id)
        if old and old.final and not sentence_final:
            return
        if old and all(unicodedata.category(char).startswith("P") for char in text):
            if not old.text.endswith(text):
                old.text += text
            old.final = old.final or sentence_final
            return
        begin, end = st.get("bg"), st.get("ed")
        begin = int(begin) if begin is not None else None
        end = int(end) if end is not None else None
        replacement = st.get("rg")
        if st.get("pgs") == "rpl" and isinstance(replacement, list) and len(replacement) == 2:
            low, high = sorted(int(x) for x in replacement)
            for other_id in list(self.segments):
                if low <= other_id <= high and other_id != segment_id:
                    del self.segments[other_id]
                    self.retired.add(other_id)
        elif not st.get("pgs") and begin is not None and end is not None:
            for other_id, other in list(self.segments.items()):
                if other_id == segment_id or other.begin is None or other.end is None:
                    continue
                same_range = begin == other.begin and end == other.end
                if same_range and all(unicodedata.category(char).startswith("P") for char in text):
                    if not other.text.endswith(text):
                        other.text += text
                    other.final = other.final or sentence_final
                    self.retired.add(segment_id)
                    return
                overlap = max(0, min(end, other.end) - max(begin, other.begin))
                shorter = max(1, min(end - begin, other.end - other.begin))
                replaces = same_range or begin == other.begin or overlap / shorter >= 0.75
                if not replaces:
                    continue
                if other.final and not sentence_final and begin >= other.begin and end <= other.end:
                    self.retired.add(segment_id)
                    return
                del self.segments[other_id]
                self.retired.add(other_id)
        self.segments[segment_id] = Segment(text, sentence_final, begin, end)
