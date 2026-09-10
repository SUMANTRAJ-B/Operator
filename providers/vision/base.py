"""Abstract base classes and schemas for image-capable vision perception providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from PIL import Image

from app.config import get_settings
from app.logging import get_logger

logger = get_logger("providers.vision.base")


@dataclass
class BoundingBox:
    """Represents a screen rectangular region (left, top, right, bottom)."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def center(self) -> Tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def to_tuple(self) -> Tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)


@dataclass
class DetectedUIElement:
    """Represents a grounded UI control detected visually or through accessibility APIs."""

    label: str
    element_type: str  # e.g., "button", "dialog", "edit", "checkbox", "menu"
    bounds: BoundingBox
    confidence: float = 1.0
    attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def click_point(self) -> Tuple[int, int]:
        """Derive safe click coordinates from verified bounding box center."""
        return self.bounds.center


@dataclass
class VisionAnalysisResult:
    """Encapsulates output from visual perception analysis."""

    success: bool
    elements: List[DetectedUIElement] = field(default_factory=list)
    description: str = ""
    error: Optional[str] = None
    model_name: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


class VisionProvider(ABC):
    """Abstract interface for image-capable vision perception models."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if an actual image-capable vision model is connected and ready."""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Return model identifier."""
        pass

    @abstractmethod
    def analyze_image(
        self,
        image_path: Union[str, Path],
        prompt: str,
        target_schema: Optional[Dict[str, Any]] = None,
    ) -> VisionAnalysisResult:
        """Analyze a screenshot image using a vision-capable backend."""
        pass

    @abstractmethod
    def detect_elements(
        self,
        image_path: Union[str, Path],
        query: Optional[str] = None,
    ) -> List[DetectedUIElement]:
        """Detect UI controls and bounding boxes in a screenshot."""
        pass


class UnavailableVisionProvider(VisionProvider):
    """Fallback vision provider when no image-capable vision model is installed.

    Exposes a clean 'vision provider unavailable' state and prevents any hallucinations
    or fake coordinate generation from image filenames.
    """

    def __init__(self, reason: str = "No vision-capable model is currently configured or available."):
        self.reason = reason

    def is_available(self) -> bool:
        return False

    def get_model_name(self) -> str:
        return "none (unavailable)"

    def analyze_image(
        self,
        image_path: Union[str, Path],
        prompt: str,
        target_schema: Optional[Dict[str, Any]] = None,
    ) -> VisionAnalysisResult:
        logger.info(f"Vision analysis requested but vision provider is unavailable: {self.reason}")
        return VisionAnalysisResult(
            success=False,
            error=self.reason,
            description="Visual perception unavailable; system must fall back to UIAutomation.",
            model_name=self.get_model_name(),
        )

    def detect_elements(
        self,
        image_path: Union[str, Path],
        query: Optional[str] = None,
    ) -> List[DetectedUIElement]:
        logger.info(f"Visual element detection skipped: {self.reason}")
        return []


class OllamaVisionProvider(VisionProvider):
    """Client for local Ollama multimodal/vision models (e.g. qwen2.5-vl, llava)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 15.0,
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        # Vision model name can be distinct from text LLM model
        self.model = model or getattr(settings, "ollama_vision_model", "qwen2.5-vl:7b")
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        """Check if Ollama server is reachable AND has the requested vision model with vision capability."""
        try:
            import urllib.request
            import json
            req = urllib.request.Request(f"{self.base_url}/api/tags", headers={"User-Agent": "Operator"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode())
                for m in data.get("models", []):
                    m_name = m.get("name", "")
                    if self.model in m_name or m_name in self.model:
                        caps = m.get("capabilities", [])
                        if "vision" in caps or "vl" in m_name.lower() or "llava" in m_name.lower():
                            return True
            return False
        except Exception:
            return False

    def get_model_name(self) -> str:
        return self.model

    def analyze_image(
        self,
        image_path: Union[str, Path],
        prompt: str,
        target_schema: Optional[Dict[str, Any]] = None,
    ) -> VisionAnalysisResult:
        if not self.is_available():
            return VisionAnalysisResult(
                success=False,
                error=f"Ollama vision model '{self.model}' is not available or does not support vision.",
                model_name=self.model,
            )
        # Vision request implementation using Ollama base64 image input
        try:
            import base64
            import requests
            with open(image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")

            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [img_b64],
                    }
                ],
                "stream": False,
            }
            resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout_seconds)
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("message", {}).get("content", "")
                return VisionAnalysisResult(
                    success=True,
                    description=content,
                    model_name=self.model,
                    raw_response=data,
                )
            return VisionAnalysisResult(
                success=False,
                error=f"Ollama returned HTTP {resp.status_code}: {resp.text}",
                model_name=self.model,
            )
        except Exception as e:
            return VisionAnalysisResult(
                success=False,
                error=f"Vision model call error: {e}",
                model_name=self.model,
            )

    def detect_elements(
        self,
        image_path: Union[str, Path],
        query: Optional[str] = None,
    ) -> List[DetectedUIElement]:
        if not self.is_available():
            return []
        prompt = (
            f"Identify the UI elements corresponding to: {query or 'interactive controls'}. "
            "Return element label and bounding box [left, top, right, bottom]."
        )
        res = self.analyze_image(image_path, prompt)
        # Parse detected elements if available
        return res.elements


_vision_provider_instance: Optional[VisionProvider] = None


def get_vision_provider() -> VisionProvider:
    """Retrieve shared VisionProvider instance, returning UnavailableVisionProvider if no vision model is detected."""
    global _vision_provider_instance
    if _vision_provider_instance is None:
        ollama_vision = OllamaVisionProvider()
        if ollama_vision.is_available():
            _vision_provider_instance = ollama_vision
        else:
            _vision_provider_instance = UnavailableVisionProvider(
                reason=(
                    "No vision-capable model is installed on the local Ollama instance. "
                    "Current models: qwen3:8b (text), phi4-mini (text), qwen2.5 (text). "
                    "Perception controller will safely fall back to Windows UIAutomation."
                )
            )
    return _vision_provider_instance
