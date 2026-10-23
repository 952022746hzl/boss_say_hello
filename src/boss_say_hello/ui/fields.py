from PyQt6.QtWidgets import QLabel


def required_label(text):
    return QLabel(f'{text} <span style="color:#c62828;font-weight:700">*</span>')


def mark_invalid(widget, invalid):
    widget.setProperty("invalid", bool(invalid))
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def watch_field(widget, signal):
    signal.connect(lambda: mark_invalid(widget, False))
