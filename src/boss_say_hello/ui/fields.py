from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
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


def visibility_icon(visible):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#31565c"), 1.8))
    eye = QPainterPath()
    eye.moveTo(2, 12)
    eye.cubicTo(7, 3, 17, 3, 22, 12)
    eye.cubicTo(17, 21, 7, 21, 2, 12)
    painter.drawPath(eye)
    painter.drawEllipse(9, 9, 6, 6)
    if visible:
        painter.drawLine(3, 3, 21, 21)
    painter.end()
    return QIcon(pixmap)
