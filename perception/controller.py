"""Perception controller integrating UIAutomation, Window Geometry, and modular Vision providers."""

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from PIL import Image

from app.config import get_settings
from app.logging import get_logger
from perception.geometry import WindowGeometryProvider
from perception.screenshot import ScreenCapture, get_screen_capture
from perception.uiautomation import UIAutomationProvider
from providers.vision.base import (
    BoundingBox,
    DetectedUIElement,
    VisionProvider,
    get_vision_provider,
)

logger = get_logger("perception.controller")


@dataclass
class ScreenObservation:
    """Represents a timestamped, grounded perception observation of the desktop."""

    observation_id: str
    timestamp: float
    screen_size: Tuple[int, int]
    screenshot_path: Optional[Path] = None
    detected_elements: List[DetectedUIElement] = field(default_factory=list)
    modal_dialog: Optional[Dict[str, Any]] = None
    vision_available: bool = False

    def is_stale(self, max_age_seconds: float = 5.0) -> bool:
        """Return True if observation is older than max_age_seconds."""
        return (time.time() - self.timestamp) > max_age_seconds


class PerceptionController:
    """Perception controller orchestrating UIAutomation, Window Geometry, and Vision perception.

    Enforces the layered perception hierarchy:
    1. UIAutomation (structured, native OS controls) when available.
    2. Window Geometry metadata.
    3. Vision / Multimodal analysis when UIAutomation is insufficient and a vision backend is connected.
    4. If vision backend is unavailable, exposes an explicit unavailable state and falls back safely.
    """

    def __init__(
        self,
        screen_capture: Optional[ScreenCapture] = None,
        geometry: Optional[WindowGeometryProvider] = None,
        uiautomation: Optional[UIAutomationProvider] = None,
        vision: Optional[VisionProvider] = None,
    ):
        self.settings = get_settings()
        self.screen_capture = screen_capture or get_screen_capture()
        self.geometry = geometry or WindowGeometryProvider()
        self.uia = uiautomation or UIAutomationProvider()
        self.vision = vision or get_vision_provider()
        self._observation_history: Dict[str, ScreenObservation] = {}

    @property
    def is_vision_available(self) -> bool:
        """Check if an image-capable vision provider is connected and available."""
        return self.vision.is_available()

    def fresh_screen_observation(self, capture_image: bool = True) -> ScreenObservation:
        """Produce a timestamped ScreenObservation representing the current desktop state."""
        obs_id = f"obs_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        now = time.time()
        w, h = self.geometry.get_screen_size()

        shot_path: Optional[Path] = None
        if capture_image:
            try:
                shot_path = self.screen_capture.generate_screenshot_filename(prefix=f"obs_{obs_id}")
                self.screen_capture.capture_full_screen(save_path=shot_path)
            except Exception as e:
                logger.warning(f"Error capturing observation screenshot: {e}")
                shot_path = None

        # Detect active modal dialog via UIAutomation
        modal = None
        try:
            modal = self.uia.detect_modal_dialog()
        except Exception as e:
            logger.debug(f"Modal detection error: {e}")

        obs = ScreenObservation(
            observation_id=obs_id,
            timestamp=now,
            screen_size=(w, h),
            screenshot_path=shot_path,
            modal_dialog=modal,
            vision_available=self.is_vision_available,
        )
        self._observation_history[obs_id] = obs
        logger.debug(f"Produced fresh observation {obs_id} (screen: {w}x{h}, vision={obs.vision_available})")
        return obs

    def get_observation(self, observation_id: str) -> Optional[ScreenObservation]:
        """Retrieve an observation by its ID."""
        return self._observation_history.get(observation_id)

    def cleanup_observation_screenshot(self, observation_id: str, preserve: bool = False) -> None:
        """Manage ephemeral screenshot lifecycle."""
        obs = self._observation_history.get(observation_id)
        if not obs or not obs.screenshot_path:
            return

        if not preserve:
            try:
                if obs.screenshot_path.exists():
                    obs.screenshot_path.unlink()
                    logger.debug(f"Deleted ephemeral observation screenshot {obs.screenshot_path}")
            except Exception as e:
                logger.debug(f"Could not delete observation screenshot: {e}")
        else:
            logger.info(f"Preserving diagnostic observation screenshot: {obs.screenshot_path}")

    def find_element(
        self,
        target_query: str,
        window_hwnd: Optional[int] = None,
        observation: Optional[ScreenObservation] = None,
    ) -> Tuple[Optional[DetectedUIElement], str]:
        """Find a target UI element using the layered perception hierarchy.

        Returns (DetectedUIElement, perception_source).
        Never fabricates coordinates if the element cannot be found.
        """
        query_l = target_query.strip().lower()

        # 1. UIAutomation structured search (Primary)
        if window_hwnd:
            controls = self.uia.find_controls_in_window(window_hwnd)
            for c in controls:
                if query_l in c.label.lower() or c.label.lower() in query_l:
                    return c, "uiautomation"

        # Check controls in active modal dialog if present
        curr_obs = observation or self.fresh_screen_observation(capture_image=False)
        if curr_obs.modal_dialog and "controls" in curr_obs.modal_dialog:
            for c_dict in curr_obs.modal_dialog["controls"]:
                label = c_dict.get("label", "").lower()
                if query_l in label or label in query_l:
                    b_tuple = c_dict.get("bounds", (0, 0, 0, 0))
                    elem = DetectedUIElement(
                        label=c_dict.get("label", ""),
                        element_type=c_dict.get("type", "button"),
                        bounds=BoundingBox(left=b_tuple[0], top=b_tuple[1], right=b_tuple[2], bottom=b_tuple[3]),
                        confidence=float(c_dict.get("confidence", 1.0)),
                        attributes={"source": "uiautomation_modal"},
                    )
                    return elem, "uiautomation_modal"

        # 2. Vision fallback (Only if actual vision model is available)
        if self.is_vision_available and curr_obs.screenshot_path:
            logger.info(f"UIAutomation did not find '{target_query}'; querying vision provider '{self.vision.get_model_name()}'")
            detected = self.vision.detect_elements(curr_obs.screenshot_path, query=target_query)
            for d in detected:
                if query_l in d.label.lower():
                    return d, "vision"
        else:
            if not self.is_vision_available:
                logger.debug("Vision provider unavailable; vision fallback skipped.")

        return None, "none"


_perception_controller_instance: Optional[PerceptionController] = None


def get_perception_controller() -> PerceptionController:
    """Retrieve shared PerceptionController instance."""
    global _perception_controller_instance
    if _perception_controller_instance is None:
        _perception_controller_instance = PerceptionController()
    return _perception_controller_instance
