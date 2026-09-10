"""Vision provider interface and implementations for visual desktop perception."""

from providers.vision.base import (
    BoundingBox,
    DetectedUIElement,
    VisionAnalysisResult,
    VisionProvider,
    UnavailableVisionProvider,
    get_vision_provider,
)

__all__ = [
    "BoundingBox",
    "DetectedUIElement",
    "VisionAnalysisResult",
    "VisionProvider",
    "UnavailableVisionProvider",
    "get_vision_provider",
]
