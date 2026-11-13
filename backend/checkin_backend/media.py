"""Decoding and bounds for client input (contract x-media-policy). Never interprets health."""

import base64
import binascii

MAX_SAMPLE_BYTES = 131072
MAX_ATTEMPT_BYTES = 10 * 1024 * 1024
MAX_ANSWER_BYTES = 10 * 1024 * 1024
MAX_PCM_CHUNK_BYTES = 8000
MAX_VIDEO_WIDTH = 640
MAX_VIDEO_HEIGHT = 480
ANSWER_TYPES = ("audio/mp4", "audio/webm", "audio/wav")
SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


class MediaRejected(ValueError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def decode_base64(text: str) -> bytes:
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise MediaRejected("invalid_request", "Sample data is not valid base64.", 400) from None
    if len(data) == 0:
        raise MediaRejected("invalid_request", "Sample data is empty.", 400)
    if len(data) > MAX_SAMPLE_BYTES:
        raise MediaRejected("too_large", "Sample is larger than the per-sample limit.", 413)
    return data


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from the first frame header, or None when this is not a complete JPEG."""
    if len(data) < 4:
        return None
    if data[0:2] != b"\xff\xd8":
        return None
    if data[-2:] != b"\xff\xd9":
        return None
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if length < 2:
            return None
        if marker in SOF_MARKERS:
            if index + 9 > len(data):
                return None
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        index += 2 + length
    return None


def check_sample(media_format: str, data: bytes) -> None:
    if media_format == "jpeg":
        size = jpeg_size(data)
        if size is None:
            raise MediaRejected("unsupported_media", "Sample is not a complete JPEG image.", 415)
        width, height = size
        if width == 0 or height == 0:
            raise MediaRejected("unsupported_media", "JPEG has no image size.", 415)
        if width > MAX_VIDEO_WIDTH or height > MAX_VIDEO_HEIGHT:
            if width > MAX_VIDEO_HEIGHT or height > MAX_VIDEO_WIDTH:
                raise MediaRejected("too_large", "JPEG is larger than 640x480.", 413)
        return
    if media_format == "pcm_s16le_16000_mono":
        if len(data) % 2 != 0:
            raise MediaRejected("unsupported_media", "PCM chunk has an odd byte count.", 415)
        if len(data) > MAX_PCM_CHUNK_BYTES:
            raise MediaRejected("too_large", "PCM chunk is longer than 250 ms.", 413)
        return
    raise MediaRejected("unsupported_media", "Unknown sample format.", 415)


def check_answer(content_type: str | None, data: bytes) -> str:
    media_type = (content_type or "").split(";")[0].strip().lower()
    if media_type not in ANSWER_TYPES:
        raise MediaRejected("unsupported_media", "Answer audio must be mp4, webm or wav.", 415)
    if len(data) == 0:
        raise MediaRejected("invalid_request", "Answer audio is empty.", 400)
    if len(data) > MAX_ANSWER_BYTES:
        raise MediaRejected("too_large", "Answer audio is larger than 10 MiB.", 413)
    return media_type
