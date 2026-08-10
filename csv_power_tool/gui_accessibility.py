"""Pure helpers and lightweight Tk hooks for the desktop shell.

The functions in this module deliberately avoid creating a Tk root.  That
keeps contrast and widget-contract tests runnable in headless CI while the
launcher applies the optional focus behavior only when a GUI is requested.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


FOCUSABLE_CLASS_NAMES = frozenset(
    {
        "Button",
        "Checkbutton",
        "CTkButton",
        "CTkCheckBox",
        "CTkComboBox",
        "CTkEntry",
        "CTkOptionMenu",
        "CTkRadioButton",
        "CTkScale",
        "CTkSegmentedButton",
        "CTkSlider",
        "CTkSwitch",
        "CTkTextbox",
        "Entry",
        "Radiobutton",
        "Scale",
        "Spinbox",
        "Text",
    }
)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    candidate = value.strip().lstrip("#")
    if len(candidate) == 3:
        candidate = "".join(char * 2 for char in candidate)
    if len(candidate) != 6:
        raise ValueError(f"Expected a six-digit RGB color, got {value!r}")
    try:
        return tuple(int(candidate[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError as exc:
        raise ValueError(f"Invalid RGB color: {value!r}") from exc


def _relative_luminance(value: str) -> float:
    channels = []
    for channel in _hex_rgb(value):
        normalized = channel / 255
        channels.append(
            normalized / 12.92
            if normalized <= 0.03928
            else ((normalized + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG 2 contrast ratio for two opaque RGB colors."""

    first = _relative_luminance(foreground)
    second = _relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def validate_theme_contrast(colors: dict[str, str], minimum: float = 4.5) -> dict[str, float]:
    """Measure the normal-text combinations used by both shipped themes."""

    pairs = {
        "primary_on_surface": ("text_primary", "bg_secondary"),
        "secondary_on_surface": ("text_secondary", "bg_secondary"),
        "muted_on_surface": ("text_muted", "bg_secondary"),
        "primary_on_dark": ("text_primary", "bg_dark"),
        "secondary_on_dark": ("text_secondary", "bg_dark"),
        "muted_on_dark": ("text_muted", "bg_dark"),
    }
    measured = {
        name: contrast_ratio(colors[foreground], colors[background])
        for name, (foreground, background) in pairs.items()
    }
    failures = {name: ratio for name, ratio in measured.items() if ratio < minimum}
    if failures:
        details = ", ".join(f"{name}={ratio:.2f}" for name, ratio in failures.items())
        raise ValueError(f"Theme contrast below {minimum:.1f}: {details}")
    return measured


def iter_widgets(root: Any) -> Iterator[Any]:
    """Yield a widget tree in Tk creation order without requiring Tk imports."""

    yield root
    try:
        children = root.winfo_children()
    except Exception:
        return
    for child in children:
        yield from iter_widgets(child)


def is_focusable(widget: Any) -> bool:
    """Identify controls that should participate in the shell's Tab order."""

    if getattr(widget, "_csv_power_skip_focus", False):
        return False
    if widget.__class__.__name__ not in FOCUSABLE_CLASS_NAMES:
        return False
    try:
        state = str(widget.cget("state"))
    except Exception:
        state = "normal"
    return state != "disabled"


def collect_focusables(root: Any) -> list[Any]:
    """Return enabled controls in deterministic visual/creation order."""

    return [widget for widget in iter_widgets(root) if is_focusable(widget)]


def set_accessible_name(widget: Any, name: str, description: str | None = None) -> Any:
    """Attach an inspectable accessible name/description to a Tk widget."""

    widget._csv_power_accessible_name = name
    if description:
        widget._csv_power_accessible_description = description
    return widget


def accessible_name(widget: Any) -> str:
    """Read the explicit name or derive a useful text fallback for testing."""

    explicit = getattr(widget, "_csv_power_accessible_name", "")
    if explicit:
        return str(explicit)
    try:
        text = widget.cget("text")
    except Exception:
        text = ""
    if text:
        return str(text)
    return widget.__class__.__name__


def widget_contains(parent: Any, child: Any) -> bool:
    """Return whether a focused Tk child belongs to a candidate control."""

    parent_path = str(getattr(parent, "_w", parent))
    child_path = str(getattr(child, "_w", child))
    return child_path == parent_path or child_path.startswith(parent_path + ".")


def prepare_focus_widget(widget: Any) -> None:
    """Make customtkinter's internal canvas/entry eligible for Tk focus."""

    targets = [
        widget,
        getattr(widget, "_canvas", None),
        getattr(widget, "_entry", None),
        getattr(widget, "_text_label", None),
        getattr(widget, "_image_label", None),
    ]
    for target in targets:
        if target is None:
            continue
        try:
            target.configure(takefocus=1)
        except Exception:
            continue


def set_focus_ring(widget: Any, focused: bool, color: str) -> None:
    """Apply a visible, theme-colored focus ring where the widget supports it."""

    if not hasattr(widget, "_csv_power_focus_style"):
        style: dict[str, Any] = {}
        for option in ("border_width", "border_color", "highlightthickness", "highlightbackground", "highlightcolor"):
            try:
                style[option] = widget.cget(option)
            except Exception:
                continue
        widget._csv_power_focus_style = style

    style = widget._csv_power_focus_style
    if "border_width" in style:
        try:
            widget.configure(
                border_width=2 if focused else style["border_width"],
                border_color=color if focused else style.get("border_color"),
            )
            return
        except Exception:
            pass
    if "highlightthickness" in style:
        try:
            widget.configure(
                highlightthickness=2 if focused else style["highlightthickness"],
                highlightbackground=color if focused else style.get("highlightbackground"),
                highlightcolor=color if focused else style.get("highlightcolor"),
            )
        except Exception:
            pass
