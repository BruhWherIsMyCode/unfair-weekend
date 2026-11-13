"""JPEG sample -> image. The only cv2 import of the streamed-task code lives here, in one small function."""
from __future__ import annotations

import base64
from typing import Any, Dict


def decode_jpeg(sample: Dict[str, Any]):
    """Decode one MediaSample-shaped dict into a BGR image, or None when the bytes are not a readable JPEG.

    The sample carries `data` (bytes) or `data_base64` (str). Never raises on bad media.
    """
    import cv2  # imported here so the rest of the package works without opencv
    import numpy as np

    try:
        data = sample.get("data")
        if data is None:
            data = base64.b64decode(sample["data_base64"], validate=True)
        image = cv2.imdecode(np.frombuffer(bytes(data), dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None
    return image
