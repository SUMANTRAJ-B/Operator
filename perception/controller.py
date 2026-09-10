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
from perception.grounding import GroundingEvidence, GroundingSource, get_grounding_registry
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
class PixelActionAudit:
    """Detailed audit record for a perception-grounded pixel interaction."""

    observation_id: str
    target_description: str
    source: str
    control_metadata: Dict[str, Any]
    bounding_box: Optional[Tuple[int, int, int, int]]
    chosen_coordinate: Tuple[int, int]
    screen_dimensions: Tuple[int, int]
    observation_timestamp: float
    freshness_result: bool
    safety_decision: Dict[str, Any]
    action: str
    action_result: Dict[str, Any]
    verification_result: Dict[str, Any]
    audit_timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "target_description": self.target_description,
            "source": self.source,
            "control_metadata": self.control_metadata,
            "bounding_box": self.bounding_box,
            "chosen_coordinate": self.chosen_coordinate,
            "screen_dimensions": self.screen_dimensions,
            "observation_timestamp": self.observation_timestamp,
            "freshness_result": self.freshness_result,
            "safety_decision": self.safety_decision,
            "action": self.action,
            "action_result": self.action_result,
            "verification_result": self.verification_result,
            "audit_timestamp": self.audit_timestamp,
        }


@dataclass
class ScreenObservation:
    """Represents a timestamped, grounded perception observation of the desktop."""

    observation_id: str
    timestamp: float
    screen_size: Tuple[int, int]
    screenshot_path: Optional[Path] = None
    detected_elements: List[DetectedUIElement] = field(default_factory=list)
    modal_dialog: Optional[Dict[str, Any]] = None
    active_window: Optional[Dict[str, Any]] = None
    vision_available: bool = False
    max_age_seconds: float = 5.0

    def is_stale(self, max_age_seconds: Optional[float] = None) -> bool:
        """Return True if observation is older than max_age_seconds."""
        threshold = max_age_seconds if max_age_seconds is not None else self.max_age_seconds
        return (time.time() - self.timestamp) > threshold

    @property
    def is_valid(self) -> bool:
        """Return True if observation has valid dimensions and is not stale."""
        w, h = self.screen_size
        return w > 0 and h > 0 and not self.is_stale()

    def find_element_by_label(self, label: str) -> Optional[DetectedUIElement]:
        """Find a detected element matching the given label."""
        query_l = label.strip().lower()
        for elem in self.detected_elements:
            if query_l == elem.label.lower() or query_l in elem.label.lower():
                return elem
        return None


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
        self._audit_trail: List[PixelActionAudit] = []
        self._last_observation: Optional[ScreenObservation] = None

    @property
    def is_vision_available(self) -> bool:
        """Check if an image-capable vision provider is connected and available."""
        return self.vision.is_available()

    def fresh_screen_observation(
        self,
        capture_image: bool = True,
        max_age_seconds: Optional[float] = None,
    ) -> ScreenObservation:
        """Produce a timestamped ScreenObservation representing the current desktop state."""
        obs_id = f"obs_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        now = time.time()
        w, h = self.geometry.get_screen_size()
        configured_max_age = (
            max_age_seconds
            if max_age_seconds is not None
            else getattr(self.settings, "observation_max_age_seconds", 5.0)
        )

        shot_path: Optional[Path] = None
        if capture_image:
            try:
                shot_path = self.screen_capture.generate_screenshot_filename(prefix=f"obs_{obs_id}")
                self.screen_capture.capture_full_screen(save_path=shot_path)
            except Exception as e:
                logger.warning(f"Error capturing observation screenshot: {e}")
                shot_path = None

        # Capture active window metadata
        active_win_dict = None
        top_hwnd = None
        try:
            import win32gui
            import win32process
            top_hwnd = win32gui.GetForegroundWindow()
            if top_hwnd and win32gui.IsWindow(top_hwnd):
                _, pid = win32process.GetWindowThreadProcessId(top_hwnd)
                title = win32gui.GetWindowText(top_hwnd)
                rect = win32gui.GetWindowRect(top_hwnd)
                active_win_dict = {
                    "hwnd": top_hwnd,
                    "pid": pid,
                    "title": title,
                    "rect": (rect[0], rect[1], rect[2], rect[3]),
                }
        except Exception as e:
            logger.debug(f"Error querying active window for observation: {e}")

        # Detect active modal dialog via UIAutomation
        modal = None
        try:
            modal = self.uia.detect_modal_dialog()
        except Exception as e:
            logger.debug(f"Modal detection error: {e}")

        # Discover UI controls in foreground window
        detected_elements: List[DetectedUIElement] = []
        if top_hwnd and win32gui.IsWindow(top_hwnd):
            try:
                detected_elements = self.uia.find_controls_in_window(top_hwnd)
            except Exception as e:
                logger.debug(f"Error extracting controls for observation: {e}")

        obs = ScreenObservation(
            observation_id=obs_id,
            timestamp=now,
            screen_size=(w, h),
            screenshot_path=shot_path,
            detected_elements=detected_elements,
            modal_dialog=modal,
            active_window=active_win_dict,
            vision_available=self.is_vision_available,
            max_age_seconds=configured_max_age,
        )
        self._observation_history[obs_id] = obs
        self._last_observation = obs

        # Register grounded elements with GroundingRegistry
        grounding_evidences: List[GroundingEvidence] = []
        for elem in detected_elements:
            grounding_evidences.append(
                GroundingEvidence(
                    observation_id=obs_id,
                    observation_timestamp=now,
                    source=elem.attributes.get("source", GroundingSource.UI_AUTOMATION),
                    target_query=elem.label,
                    bounding_box=elem.bounds,
                    click_point=elem.click_point,
                    control_type=elem.element_type,
                    control_metadata=elem.attributes,
                    screen_size=(w, h),
                    max_age_seconds=configured_max_age,
                    target_hwnd=active_win_dict.get("hwnd") if active_win_dict else None,
                    target_window_title=active_win_dict.get("title", "") if active_win_dict else "",
                )
            )

        # If modal dialog controls present, register them too
        if modal and "controls" in modal:
            for c_dict in modal["controls"]:
                b_tuple = c_dict.get("bounds", (0, 0, 0, 0))
                if b_tuple[2] > b_tuple[0] and b_tuple[3] > b_tuple[1]:
                    bbox = BoundingBox(left=b_tuple[0], top=b_tuple[1], right=b_tuple[2], bottom=b_tuple[3])
                    grounding_evidences.append(
                        GroundingEvidence(
                            observation_id=obs_id,
                            observation_timestamp=now,
                            source=GroundingSource.UI_AUTOMATION_MODAL,
                            target_query=c_dict.get("label", ""),
                            bounding_box=bbox,
                            click_point=bbox.center,
                            control_type=c_dict.get("type", "button"),
                            screen_size=(w, h),
                            max_age_seconds=configured_max_age,
                            target_hwnd=modal.get("hwnd"),
                            target_window_title=modal.get("title", ""),
                        )
                    )

        get_grounding_registry().register_observation(
            observation_id=obs_id,
            timestamp=now,
            screen_size=(w, h),
            evidences=grounding_evidences,
        )

        logger.debug(
            f"Produced fresh observation {obs_id} (screen: {w}x{h}, controls: {len(detected_elements)}, vision={obs.vision_available})"
        )
        return obs

    def get_observation(self, observation_id: str) -> Optional[ScreenObservation]:
        """Retrieve an observation by its ID."""
        return self._observation_history.get(observation_id)

    def get_latest_observation(self) -> Optional[ScreenObservation]:
        """Retrieve the most recent screen observation."""
        return self._last_observation

    def cleanup_observation_screenshot(
        self,
        observation_id: str,
        success: bool = True,
        preserve: Optional[bool] = None,
    ) -> None:
        """Manage ephemeral screenshot lifecycle.

        SUCCESS: deletes temporary screenshot if screenshot_cleanup_on_success is True.
        FAILURE: retains temporary screenshot for diagnostics and reporting.
        """
        obs = self._observation_history.get(observation_id)
        if not obs or not obs.screenshot_path:
            return

        should_preserve = preserve if preserve is not None else (not success)
        cleanup_enabled = getattr(self.settings, "screenshot_cleanup_on_success", True)

        if not should_preserve and cleanup_enabled:
            try:
                if obs.screenshot_path.exists():
                    obs.screenshot_path.unlink()
                    logger.debug(f"Deleted ephemeral observation screenshot {obs.screenshot_path} upon verified success.")
            except Exception as e:
                logger.debug(f"Could not delete observation screenshot: {e}")
        else:
            logger.info(
                f"Preserving diagnostic observation screenshot: {obs.screenshot_path} (success={success}, preserve={preserve})"
            )

    def record_pixel_action_audit(
        self,
        observation_id: str,
        target_description: str,
        source: str,
        chosen_coordinate: Tuple[int, int],
        action: str,
        safety_decision: Dict[str, Any],
        action_result: Dict[str, Any],
        verification_result: Dict[str, Any],
        bounding_box: Optional[Tuple[int, int, int, int]] = None,
        control_metadata: Optional[Dict[str, Any]] = None,
    ) -> PixelActionAudit:
        """Record a comprehensive audit trail entry for a pixel mouse action."""
        obs = self.get_observation(observation_id)
        obs_ts = obs.timestamp if obs else time.time()
        scr_dim = obs.screen_size if obs else self.geometry.get_screen_size()
        fresh = not obs.is_stale() if obs else False

        audit = PixelActionAudit(
            observation_id=observation_id,
            target_description=target_description,
            source=source,
            control_metadata=control_metadata or {},
            bounding_box=bounding_box,
            chosen_coordinate=chosen_coordinate,
            screen_dimensions=scr_dim,
            observation_timestamp=obs_ts,
            freshness_result=fresh,
            safety_decision=safety_decision,
            action=action,
            action_result=action_result,
            verification_result=verification_result,
        )
        self._audit_trail.append(audit)
        logger.info(f"Audited pixel action '{action}' at {chosen_coordinate} grounded in {observation_id}")
        return audit

    def get_audit_trail(self) -> List[PixelActionAudit]:
        """Return all recorded pixel action audits."""
        return list(self._audit_trail)

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
