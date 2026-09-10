"""Windows UIAutomation structured control perception provider."""

from typing import Any, Dict, List, Optional, Tuple
import win32gui
import win32process

from app.logging import get_logger
from providers.vision.base import BoundingBox, DetectedUIElement

logger = get_logger("perception.uiautomation")


class UIAutomationProvider:
    """Extracts structured controls, dialogs, and button bounding boxes via UIAutomation and Win32."""

    def __init__(self):
        self._uia_available = False
        try:
            import comtypes.client
            self._uia_available = True
        except Exception:
            self._uia_available = False

    def is_available(self) -> bool:
        return self._uia_available

    def find_controls_in_window(
        self,
        hwnd: int,
        control_types: Optional[List[str]] = None,
    ) -> List[DetectedUIElement]:
        """Find interactive controls (buttons, edits, dialogs) inside target window."""
        if not hwnd or not win32gui.IsWindow(hwnd):
            return []

        elements: List[DetectedUIElement] = []

        # 1. UIAutomation via comtypes
        if self._uia_available:
            try:
                import comtypes.client
                from comtypes import CoInitialize, CoUninitialize
                CoInitialize()
                try:
                    mod = comtypes.client.GetModule("UIAutomationCore.dll")
                    uia = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
                    root = uia.ElementFromHandle(hwnd)
                    if root:
                        # Find buttons (UIA_ButtonControlTypeId = 50000)
                        cond_btn = uia.CreatePropertyCondition(30003, 50000)
                        found_buttons = root.FindAll(mod.TreeScope_Descendants, cond_btn)
                        if found_buttons:
                            count = found_buttons.Length
                            for i in range(count):
                                elem = found_buttons.GetElement(i)
                                name = getattr(elem, "CurrentName", "") or ""
                                rect = getattr(elem, "CurrentBoundingRectangle", None)
                                if rect:
                                    # rect is (left, top, width, height) or tuple of 4 coords
                                    # In comtypes UIAutomation, CurrentBoundingRectangle is a tagRECT (left, top, right, bottom)
                                    left = getattr(rect, "left", None)
                                    top = getattr(rect, "top", None)
                                    right = getattr(rect, "right", None)
                                    bottom = getattr(rect, "bottom", None)
                                    if left is not None and right is not None and (right > left) and (bottom > top):
                                        bbox = BoundingBox(left=left, top=top, right=right, bottom=bottom)
                                        elements.append(
                                            DetectedUIElement(
                                                label=name.strip(),
                                                element_type="button",
                                                bounds=bbox,
                                                confidence=1.0,
                                                attributes={"source": "uiautomation"},
                                            )
                                        )
                finally:
                    CoUninitialize()
            except Exception as e:
                logger.debug(f"UIAutomation extraction error: {e}")

        # 2. Fallback: Win32 child window enumeration
        if not elements:
            try:
                def enum_child(child_hwnd, _):
                    if win32gui.IsWindowVisible(child_hwnd):
                        cls = win32gui.GetClassName(child_hwnd)
                        txt = win32gui.GetWindowText(child_hwnd)
                        rect = win32gui.GetWindowRect(child_hwnd)
                        if rect[2] > rect[0] and rect[3] > rect[1]:
                            elem_type = "button" if "button" in cls.lower() else ("edit" if "edit" in cls.lower() else "control")
                            bbox = BoundingBox(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
                            elements.append(
                                DetectedUIElement(
                                    label=txt.strip() or cls,
                                    element_type=elem_type,
                                    bounds=bbox,
                                    confidence=0.9,
                                    attributes={"class_name": cls, "hwnd": child_hwnd, "source": "win32"},
                                )
                            )

                win32gui.EnumChildWindows(hwnd, enum_child, None)
            except Exception as e:
                logger.debug(f"Win32 EnumChildWindows fallback error: {e}")

        return elements

    def detect_modal_dialog(self, parent_hwnd: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Detect if an active modal dialog (e.g. confirmation, save, replace) is present."""
        top_hwnd = win32gui.GetForegroundWindow()
        if not top_hwnd or not win32gui.IsWindow(top_hwnd):
            return None

        # Check window style for modal / popup characteristics
        import win32con
        style = win32gui.GetWindowLong(top_hwnd, win32con.GWL_STYLE)
        ex_style = win32gui.GetWindowLong(top_hwnd, win32con.GWL_EXSTYLE)
        is_dialog = bool(style & win32con.WS_POPUP) or bool(ex_style & win32con.WS_EX_DLGMODALFRAME)

        title = win32gui.GetWindowText(top_hwnd)
        cls_name = win32gui.GetClassName(top_hwnd)

        # Common dialog class names: #32770 (standard Windows dialog)
        if cls_name == "#32770" or is_dialog or "confirm" in title.lower() or "dialog" in cls_name.lower():
            controls = self.find_controls_in_window(top_hwnd)
            control_dicts = [
                {
                    "label": c.label,
                    "type": c.element_type,
                    "bounds": c.bounds.to_tuple(),
                    "confidence": c.confidence,
                }
                for c in controls
            ]
            return {
                "type": "modal_dialog",
                "hwnd": top_hwnd,
                "title": title,
                "class_name": cls_name,
                "controls": control_dicts,
            }

        return None
