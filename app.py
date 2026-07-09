import os
import sys
import fitz

from PySide6.QtCore import Qt, QSize, QRect, QPoint, Signal, QTimer
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QFont, QFontMetrics, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
    QSplitter,
    QGroupBox,
    QTabWidget,
)

APP_TITLE = "PDF Organizer & Editor"


class PageListWidget(QListWidget):
    order_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setIconSize(QSize(120, 170))
        self.setDragDropMode(QListWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setMovement(QListWidget.Snap)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()


class PdfPreviewLabel(QLabel):
    edit_clicked = Signal(QPoint)
    commit_requested = Signal()
    cancel_requested = Signal()

    def __init__(self):
        super().__init__("Öffne eine PDF-Datei.")
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: #f2f2f2; border: 1px solid #ccc;")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._edit_enabled = False
        self._pixmap_rect = QRect()

        self.overlay_active = False
        self.overlay_rect = QRect()
        self.overlay_text = ""
        self.overlay_cursor = 0
        self.overlay_font = QFont("Arial", 12)
        self.overlay_color = QColor(0, 0, 0)
        self.overlay_cover_background = False
        self._cursor_visible = True

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_cursor)
        self._blink_timer.start(520)

    def _toggle_cursor(self):
        if self.overlay_active:
            self._cursor_visible = not self._cursor_visible
            self.update()

    def set_edit_enabled(self, enabled: bool):
        self._edit_enabled = enabled
        self.setCursor(Qt.IBeamCursor if enabled else Qt.ArrowCursor)

    def set_pixmap_rect(self, rect: QRect):
        self._pixmap_rect = rect

    def start_overlay(self, rect: QRect, text: str = "", cursor_pos: int | None = None, cover_background: bool = False):
        self.overlay_active = True
        self.overlay_rect = rect
        self.overlay_text = text or ""
        self.overlay_cursor = len(self.overlay_text) if cursor_pos is None else max(0, min(cursor_pos, len(self.overlay_text)))
        self.overlay_cover_background = cover_background
        self._cursor_visible = True
        self.setFocus()
        self.update()

    def clear_overlay(self):
        self.overlay_active = False
        self.overlay_text = ""
        self.overlay_cursor = 0
        self.overlay_cover_background = False
        self.update()

    def mousePressEvent(self, event):
        if self._edit_enabled and event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            if self._pixmap_rect.contains(pos):
                self.edit_clicked.emit(pos)
                return
        return super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if not self.overlay_active:
            return super().keyPressEvent(event)

        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.commit_requested.emit()
            return
        if key == Qt.Key_Escape:
            self.cancel_requested.emit()
            return
        if key == Qt.Key_Left:
            self.overlay_cursor = max(0, self.overlay_cursor - 1)
        elif key == Qt.Key_Right:
            self.overlay_cursor = min(len(self.overlay_text), self.overlay_cursor + 1)
        elif key == Qt.Key_Home:
            self.overlay_cursor = 0
        elif key == Qt.Key_End:
            self.overlay_cursor = len(self.overlay_text)
        elif key == Qt.Key_Backspace:
            if self.overlay_cursor > 0:
                self.overlay_text = self.overlay_text[: self.overlay_cursor - 1] + self.overlay_text[self.overlay_cursor :]
                self.overlay_cursor -= 1
        elif key == Qt.Key_Delete:
            if self.overlay_cursor < len(self.overlay_text):
                self.overlay_text = self.overlay_text[: self.overlay_cursor] + self.overlay_text[self.overlay_cursor + 1 :]
        else:
            txt = event.text()
            if txt and txt >= " " and not (event.modifiers() & Qt.ControlModifier):
                self.overlay_text = self.overlay_text[: self.overlay_cursor] + txt + self.overlay_text[self.overlay_cursor :]
                self.overlay_cursor += len(txt)
            else:
                return super().keyPressEvent(event)

        self._cursor_visible = True
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.overlay_active:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(self.overlay_font)

        if self.overlay_cover_background:
            painter.fillRect(self.overlay_rect.adjusted(-1, -1, 3, 2), QColor(255, 255, 255))

        painter.setPen(self.overlay_color)
        baseline_rect = self.overlay_rect.adjusted(0, 0, 1000, 10)
        painter.drawText(baseline_rect, Qt.AlignLeft | Qt.AlignVCenter, self.overlay_text)

        if self._cursor_visible:
            metrics = painter.fontMetrics()
            left_text = self.overlay_text[: self.overlay_cursor]
            cursor_x = self.overlay_rect.left() + metrics.horizontalAdvance(left_text)
            cursor_top = self.overlay_rect.top() + 2
            cursor_bottom = self.overlay_rect.bottom() - 2
            painter.setPen(QPen(QColor(20, 90, 210), 2))
            painter.drawLine(cursor_x, cursor_top, cursor_x, cursor_bottom)


class PdfOrganizerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1150, 760)
        self.setMinimumSize(950, 620)

        self.pdf_path: str | None = None
        self.doc: fitz.Document | None = None
        self.page_order: list[int] = []
        self.current_preview_pixmap: QPixmap | None = None
        self.current_zoom = 1.35

        self.inline_pdf_rect: fitz.Rect | None = None
        self.inline_page_index: int | None = None
        self.inline_fontsize = 11
        self.inline_fontname = "helv"
        self.inline_font_family = "Arial"
        self.inline_fontfile: str | None = None
        self.inline_exact_pdf_font = False
        self.inline_color = (0, 0, 0)
        self.inline_original_text: str | None = None
        self.inline_text_origin: fitz.Point | None = None
        self.inline_screen_font_px: int = 12
        self.inline_edit_existing = False

        self._build_ui()

    def _build_ui(self):
        main = QWidget()
        self.setCentralWidget(main)
        root_layout = QVBoxLayout(main)

        top_bar = QHBoxLayout()
        self.open_button = QPushButton("PDF öffnen")
        self.save_button = QPushButton("Speichern als...")
        self.save_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_pdf)
        self.save_button.clicked.connect(self.save_pdf)
        top_bar.addWidget(self.open_button)
        top_bar.addWidget(self.save_button)
        top_bar.addStretch()
        self.mode_info = QLabel("Modus: Organisieren")
        top_bar.addWidget(self.mode_info)
        root_layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter, stretch=1)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.on_tab_changed)
        left_layout.addWidget(self.tabs)

        organize_tab = QWidget()
        organize_layout = QVBoxLayout(organize_tab)
        organize_group = QGroupBox("Organisieren")
        organize_group_layout = QVBoxLayout(organize_group)

        self.page_list = PageListWidget()
        self.page_list.currentRowChanged.connect(self.show_selected_page)
        self.page_list.order_changed.connect(self.sync_order_from_list)
        organize_group_layout.addWidget(self.page_list)

        hint = QLabel("Tipp: Ziehe eine Seite mit der Maus an die gewünschte Position.")
        hint.setWordWrap(True)
        organize_group_layout.addWidget(hint)

        button_row = QHBoxLayout()
        self.delete_button = QPushButton("Seite löschen")
        self.add_pdf_button = QPushButton("PDF-Seiten hinzufügen")
        self.reset_button = QPushButton("Zurücksetzen")
        button_row.addWidget(self.delete_button)
        button_row.addWidget(self.add_pdf_button)
        button_row.addWidget(self.reset_button)
        organize_group_layout.addLayout(button_row)

        self.delete_button.clicked.connect(self.delete_selected_page)
        self.add_pdf_button.clicked.connect(self.add_pdf_pages)
        self.reset_button.clicked.connect(self.reset_order)
        organize_layout.addWidget(organize_group)
        self.tabs.addTab(organize_tab, "Organisieren")

        edit_tab = QWidget()
        edit_layout = QVBoxLayout(edit_tab)
        edit_group = QGroupBox("PDF-Text bearbeiten")
        edit_group_layout = QVBoxLayout(edit_group)

        edit_help = QLabel(
            "1. Seite auswählen\n"
            "2. Direkt auf vorhandenen Text klicken\n"
            "3. Der Cursor blinkt direkt im PDF-Text, ohne sichtbares Textfeld\n"
            "4. Mit Tastatur schreiben, Backspace/Entf benutzen\n"
            "5. Enter oder Änderung übernehmen klicken, danach PDF speichern"
        )
        edit_help.setWordWrap(True)
        edit_group_layout.addWidget(edit_help)

        edit_button_row = QHBoxLayout()
        self.apply_text_button = QPushButton("Änderung übernehmen")
        self.cancel_text_button = QPushButton("Cursor schließen")
        edit_button_row.addWidget(self.apply_text_button)
        edit_button_row.addWidget(self.cancel_text_button)
        edit_group_layout.addLayout(edit_button_row)

        self.apply_text_button.clicked.connect(self.apply_inline_text_edit)
        self.cancel_text_button.clicked.connect(self.close_inline_editor)
        edit_layout.addWidget(edit_group)
        edit_layout.addStretch()
        self.tabs.addTab(edit_tab, "PDF bearbeiten")

        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        self.preview_label = PdfPreviewLabel()
        self.preview_label.edit_clicked.connect(self.start_inline_text_edit)
        self.preview_label.commit_requested.connect(self.apply_inline_text_edit)
        self.preview_label.cancel_requested.connect(self.close_inline_editor)
        right_layout.addWidget(self.preview_label, stretch=1)
        self.status_label = QLabel("Bereit")
        right_layout.addWidget(self.status_label)
        splitter.addWidget(right_panel)
        splitter.setSizes([390, 760])

        self._set_buttons_enabled(False)

    def _set_buttons_enabled(self, enabled: bool):
        self.save_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.add_pdf_button.setEnabled(enabled)
        self.reset_button.setEnabled(enabled)
        self.apply_text_button.setEnabled(enabled)
        self.cancel_text_button.setEnabled(enabled)

    def on_tab_changed(self, index: int):
        self.close_inline_editor()
        is_edit_mode = self.tabs.tabText(index) == "PDF bearbeiten"
        self.mode_info.setText("Modus: PDF bearbeiten" if is_edit_mode else "Modus: Organisieren")
        self.preview_label.set_edit_enabled(is_edit_mode and self.doc is not None)
        self.show_selected_page(self.page_list.currentRow())

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "PDF auswählen", os.path.expanduser("~"), "PDF-Dateien (*.pdf)")
        if not path:
            return
        try:
            self.doc = fitz.open(path)
            if self.doc.page_count == 0:
                raise ValueError("Diese PDF enthält keine Seiten.")
        except Exception as exc:
            QMessageBox.critical(self, "Fehler", f"PDF konnte nicht geöffnet werden:\n{exc}")
            return

        self.pdf_path = path
        self.page_order = list(range(self.doc.page_count))
        self.refresh_page_list()
        self._set_buttons_enabled(True)
        self.preview_label.set_edit_enabled(self.tabs.tabText(self.tabs.currentIndex()) == "PDF bearbeiten")
        self.status_label.setText(f"Geöffnet: {os.path.basename(path)} ({self.doc.page_count} Seiten)")

    def refresh_page_list(self, keep_row: int | None = None):
        self.page_list.blockSignals(True)
        self.page_list.clear()
        if not self.doc:
            self.page_list.blockSignals(False)
            return

        for display_index, original_page_index in enumerate(self.page_order, start=1):
            page = self.doc.load_page(original_page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(0.18, 0.18), alpha=False)
            image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(image.copy())
            item = QListWidgetItem(f"Seite {display_index}  (Original: {original_page_index + 1})")
            item.setIcon(pixmap)
            item.setData(Qt.UserRole, original_page_index)
            self.page_list.addItem(item)

        self.page_list.blockSignals(False)
        if self.page_list.count() > 0:
            row = 0 if keep_row is None else max(0, min(keep_row, self.page_list.count() - 1))
            self.page_list.setCurrentRow(row)

    def sync_order_from_list(self):
        self.page_order = [self.page_list.item(i).data(Qt.UserRole) for i in range(self.page_list.count())]
        for i in range(self.page_list.count()):
            original_index = self.page_list.item(i).data(Qt.UserRole)
            self.page_list.item(i).setText(f"Seite {i + 1}  (Original: {original_index + 1})")
        self.status_label.setText("Reihenfolge per Drag & Drop geändert.")
        self.show_selected_page(self.page_list.currentRow())

    def show_selected_page(self, row: int):
        self.close_inline_editor()
        if not self.doc or row < 0 or row >= len(self.page_order):
            self.preview_label.setText("Keine Seite ausgewählt.")
            return
        original_page_index = self.page_order[row]
        page = self.doc.load_page(original_page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(self.current_zoom, self.current_zoom), alpha=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
        self.current_preview_pixmap = QPixmap.fromImage(image.copy())
        self._draw_preview_pixmap()
        self.status_label.setText(f"Aktuelle Seite: {row + 1} von {len(self.page_order)}")

    def _draw_preview_pixmap(self):
        if not self.current_preview_pixmap:
            return
        scaled = self.current_preview_pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)
        x = (self.preview_label.width() - scaled.width()) // 2
        y = (self.preview_label.height() - scaled.height()) // 2
        self.preview_label.set_pixmap_rect(QRect(x, y, scaled.width(), scaled.height()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._draw_preview_pixmap()
        self.close_inline_editor()

    def delete_selected_page(self):
        row = self.page_list.currentRow()
        if row < 0 or row >= len(self.page_order):
            return
        answer = QMessageBox.question(self, "Seite löschen", f"Soll Seite {row + 1} wirklich entfernt werden?")
        if answer != QMessageBox.Yes:
            return
        self.page_order.pop(row)
        self.refresh_page_list(keep_row=row)
        if not self.page_order:
            self.preview_label.setText("Alle Seiten wurden entfernt.")

    def add_pdf_pages(self):
        if not self.doc:
            return
        path, _ = QFileDialog.getOpenFileName(self, "PDF zum Hinzufügen auswählen", os.path.expanduser("~"), "PDF-Dateien (*.pdf)")
        if not path:
            return
        try:
            extra_doc = fitz.open(path)
            old_count = self.doc.page_count
            self.doc.insert_pdf(extra_doc)
            extra_doc.close()
            new_pages = list(range(old_count, self.doc.page_count))
            self.page_order.extend(new_pages)
            self.refresh_page_list(keep_row=len(self.page_order) - len(new_pages))
            self.status_label.setText(f"Hinzugefügt: {len(new_pages)} Seiten aus {os.path.basename(path)}")
        except Exception as exc:
            QMessageBox.critical(self, "Fehler", f"PDF-Seiten konnten nicht hinzugefügt werden:\n{exc}")

    def reset_order(self):
        if not self.doc:
            return
        self.page_order = list(range(self.doc.page_count))
        self.refresh_page_list()
        self.status_label.setText("Reihenfolge zurückgesetzt.")

    def _screen_point_to_pdf_point(self, pos: QPoint) -> tuple[int, fitz.Point] | None:
        row = self.page_list.currentRow()
        if not self.doc or row < 0 or row >= len(self.page_order):
            return None
        pixmap_rect = self.preview_label._pixmap_rect
        if not pixmap_rect.contains(pos):
            return None
        page_index = self.page_order[row]
        page = self.doc.load_page(page_index)
        page_rect = page.rect
        x_scale = page_rect.width / pixmap_rect.width()
        y_scale = page_rect.height / pixmap_rect.height()
        x = (pos.x() - pixmap_rect.left()) * x_scale
        y = (pos.y() - pixmap_rect.top()) * y_scale
        return page_index, fitz.Point(x, y)

    def _pdf_rect_to_screen_rect(self, page: fitz.Page, rect: fitz.Rect) -> QRect:
        pixmap_rect = self.preview_label._pixmap_rect
        page_rect = page.rect
        x_scale = pixmap_rect.width() / page_rect.width
        y_scale = pixmap_rect.height() / page_rect.height
        x0 = pixmap_rect.left() + int(rect.x0 * x_scale)
        y0 = pixmap_rect.top() + int(rect.y0 * y_scale)
        x1 = pixmap_rect.left() + int(rect.x1 * x_scale)
        y1 = pixmap_rect.top() + int(rect.y1 * y_scale)
        return QRect(QPoint(x0, y0), QPoint(x1, y1)).normalized()

    def _pdf_color_to_rgb(self, value) -> tuple[float, float, float]:
        try:
            value = int(value)
            r = ((value >> 16) & 255) / 255
            g = ((value >> 8) & 255) / 255
            b = (value & 255) / 255
            return (r, g, b)
        except Exception:
            return (0, 0, 0)

    def _pdf_color_to_qcolor(self, rgb: tuple[float, float, float]) -> QColor:
        return QColor(int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255))

    def _normalize_pdf_text(self, text: str) -> str:
        return (
            text
            .replace("’", "'")
            .replace("‘", "'")
            .replace("`", "'")
            .replace("´", "'")
            .replace("“", '\"')
            .replace("”", '\"')
            .replace("–", "-")
            .replace("—", "-")
        )

    def _windows_font_path(self, names: list[str]) -> str | None:
        font_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
        for name in names:
            path = os.path.join(font_dir, name)
            if os.path.exists(path):
                return path
        return None

    def _clean_font_name(self, font_name: str) -> str:
        name = (font_name or "").lower().replace(" ", "").replace("-", "")
        if "+" in name:
            name = name.split("+", 1)[1]
        return name

    def _map_pdf_font(self, font_name: str) -> tuple[str, str, str | None]:
        raw = font_name or ""
        name = self._clean_font_name(raw)
        italic = "italic" in name or "oblique" in name
        bold = "bold" in name and "barlow" in name

        if "aptos" in name:
            if italic:
                fontfile = self._windows_font_path(["aptos-italic.ttf", "Aptos-Italic.ttf", "aptosi.ttf", "Aptos-Italic.otf"])
            else:
                fontfile = self._windows_font_path(["aptos.ttf", "Aptos.ttf", "aptosn.ttf", "Aptos-Regular.ttf", "Aptos.otf"])
            return ("customfont" if fontfile else "helv", "Aptos", fontfile)

        if "liberationsans" in name or "liberation" in name:
            fontfile = self._windows_font_path(["LiberationSans-Regular.ttf", "LiberationSans-Regular.otf", "arial.ttf"])
            return ("customfont" if fontfile else "helv", "Arial", fontfile)

        if "calibri" in name:
            fontfile = self._windows_font_path(["calibri.ttf"])
            return ("customfont" if fontfile else "helv", "Calibri", fontfile)

        if "arial" in name or "helvetica" in name:
            fontfile = self._windows_font_path(["arial.ttf"])
            return ("customfont" if fontfile else "helv", "Arial", fontfile)

        if "barlow" in name:
            files = ["Barlow-Bold.ttf", "Barlow-Regular.ttf"] if bold else ["Barlow-Regular.ttf", "arial.ttf"]
            fontfile = self._windows_font_path(files)
            return ("customfont" if fontfile else "helv", "Barlow" if fontfile else "Arial", fontfile)

        if "courier" in name or "mono" in name or "consola" in name:
            fontfile = self._windows_font_path(["consola.ttf", "cour.ttf"])
            return ("customfont" if fontfile else ("coit" if italic else "cour"), "Courier New", fontfile)

        if "times" in name or "serif" in name or "georgia" in name:
            fontfile = self._windows_font_path(["times.ttf", "georgia.ttf"])
            return ("customfont" if fontfile else ("tiit" if italic else "tiro"), "Times New Roman", fontfile)

        fontfile = self._windows_font_path(["arial.ttf"])
        return ("customfont" if fontfile else "helv", "Arial", fontfile)

    def _text_fill_opacity(self) -> float:
        if self.inline_exact_pdf_font:
            return 1.0
        r, g, b = self.inline_color
        if r < 0.08 and g < 0.08 and b < 0.08:
            return 0.86
        return 0.94

    def _find_text_span_at_point(self, page: fitz.Page, point: fitz.Point) -> tuple[fitz.Rect, str, float, str, tuple[float, float, float], fitz.Point] | None:
        data = page.get_text("dict")
        best: tuple[float, fitz.Rect, str, float, str, tuple[float, float, float], fitz.Point] | None = None
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text.strip():
                        continue
                    rect = fitz.Rect(span["bbox"])
                    hit_rect = fitz.Rect(rect.x0 - 3, rect.y0 - 4, rect.x1 + 3, rect.y1 + 4)
                    if hit_rect.contains(point):
                        center_y = (rect.y0 + rect.y1) / 2
                        distance = abs(center_y - point.y)
                        size = float(span.get("size", 11))
                        font_name = str(span.get("font", ""))
                        color = self._pdf_color_to_rgb(span.get("color", 0))
                        origin = fitz.Point(span.get("origin", (rect.x0, rect.y1))[0], span.get("origin", (rect.x0, rect.y1))[1])
                        candidate = (distance, rect, text, size, font_name, color, origin)
                        if best is None or candidate[0] < best[0]:
                            best = candidate
        if best:
            return best[1], best[2], best[3], best[4], best[5], best[6]
        return None

    def _cursor_index_from_x(self, text: str, click_x: int, rect: QRect) -> int:
        font = self.preview_label.overlay_font
        metrics = QFontMetrics(font)
        relative_x = max(0, click_x - rect.left())
        best_index = 0
        best_distance = 10**9
        for i in range(len(text) + 1):
            dist = abs(metrics.horizontalAdvance(text[:i]) - relative_x)
            if dist < best_distance:
                best_distance = dist
                best_index = i
        return best_index

    def start_inline_text_edit(self, pos: QPoint):
        mapped = self._screen_point_to_pdf_point(pos)
        if mapped is None or not self.doc:
            return
        self.close_inline_editor()
        page_index, pdf_point = mapped
        page = self.doc.load_page(page_index)

        found = self._find_text_span_at_point(page, pdf_point)
        if found:
            pdf_rect, old_text, font_size, original_font_name, original_color, original_origin = found
            self.inline_fontsize = max(7, min(72, int(round(font_size))))
            mapped_fontname, self.inline_font_family, mapped_fontfile = self._map_pdf_font(original_font_name)
            self.inline_fontname = mapped_fontname
            self.inline_fontfile = mapped_fontfile
            self.inline_exact_pdf_font = False
            self.inline_color = original_color
            self.inline_text_origin = original_origin
            pdf_rect = fitz.Rect(pdf_rect.x0 - 0.5, pdf_rect.y0 - 0.5, pdf_rect.x1 + 0.5, pdf_rect.y1 + 0.5)
            screen_rect = self._pdf_rect_to_screen_rect(page, pdf_rect)
            pixmap_rect = self.preview_label._pixmap_rect
            y_scale = pixmap_rect.height() / page.rect.height if page.rect.height else 1
            self.inline_screen_font_px = max(6, int(round(font_size * y_scale)))
            screen_rect.setWidth(max(screen_rect.width() + 80, 120))
            screen_rect.setHeight(max(screen_rect.height(), self.inline_screen_font_px + 6))
            cursor_index = self._cursor_index_from_x(old_text, pos.x(), screen_rect)
            self.inline_pdf_rect = pdf_rect
            self.inline_page_index = page_index
            self.inline_original_text = old_text
            self.inline_edit_existing = True
            font = QFont(self.inline_font_family)
            font.setPixelSize(self.inline_screen_font_px)
            font.setBold(False)
            self.preview_label.overlay_font = font
            self.preview_label.overlay_color = self._pdf_color_to_qcolor(self.inline_color)
            self.preview_label.start_overlay(screen_rect, old_text, cursor_index, cover_background=True)
            self.status_label.setText("Textabschnitt erkannt: Änderung bleibt in fester Fläche, ohne umliegenden Text zu verändern.")
            return

        self.inline_fontsize = 11
        self.inline_fontname = "helv"
        self.inline_font_family = "Arial"
        self.inline_fontfile = None
        self.inline_exact_pdf_font = False
        self.inline_color = (0, 0, 0)
        pdf_rect = fitz.Rect(pdf_point.x, pdf_point.y - self.inline_fontsize, pdf_point.x + 420, pdf_point.y + self.inline_fontsize * 1.8)
        screen_rect = self._pdf_rect_to_screen_rect(page, pdf_rect)
        screen_rect.setWidth(max(screen_rect.width(), 220))
        screen_rect.setHeight(max(screen_rect.height(), 26))

        self.inline_pdf_rect = pdf_rect
        self.inline_page_index = page_index
        self.inline_original_text = None
        self.inline_text_origin = None
        self.inline_fontname = "helv"
        self.inline_font_family = "Arial"
        self.inline_fontfile = None
        self.inline_exact_pdf_font = False
        self.inline_color = (0, 0, 0)
        self.inline_edit_existing = False
        fallback_font = QFont(self.inline_font_family, self.inline_fontsize)
        fallback_font.setBold(False)
        self.preview_label.overlay_font = fallback_font
        self.preview_label.overlay_color = self._pdf_color_to_qcolor(self.inline_color)
        self.preview_label.start_overlay(screen_rect, "", 0, cover_background=False)
        self.status_label.setText("Kein bearbeitbarer PDF-Text erkannt: neuer Text wird an dieser Stelle eingefügt.")

    def close_inline_editor(self):
        self.preview_label.clear_overlay()
        self.inline_pdf_rect = None
        self.inline_page_index = None
        self.inline_original_text = None
        self.inline_text_origin = None
        self.inline_fontname = "helv"
        self.inline_font_family = "Arial"
        self.inline_color = (0, 0, 0)
        self.inline_exact_pdf_font = False
        self.inline_edit_existing = False

    def apply_inline_text_edit(self):
        if not self.doc or not self.preview_label.overlay_active or self.inline_pdf_rect is None or self.inline_page_index is None:
            return
        text = self._normalize_pdf_text(self.preview_label.overlay_text)
        page = self.doc.load_page(self.inline_page_index)
        rect = self.inline_pdf_rect
        try:
            if self.inline_edit_existing:
                cover_rect = fitz.Rect(rect.x0 - 1.2, rect.y0 - 1.2, rect.x1 + 1.8, rect.y1 + 1.2)
                page.draw_rect(cover_rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
                if text:
                    baseline = self.inline_text_origin or fitz.Point(rect.x0, rect.y1 - self.inline_fontsize * 0.22)
                    page.insert_text(
                        baseline,
                        text,
                        fontsize=float(self.inline_fontsize),
                        fontname=self.inline_fontname,
                        fontfile=self.inline_fontfile,
                        color=self.inline_color,
                        fill_opacity=self._text_fill_opacity(),
                        border_width=0,
                        overlay=True,
                    )
            else:
                if text:
                    baseline = fitz.Point(rect.x0, rect.y1 - self.inline_fontsize * 0.55)
                    page.insert_text(
                        baseline,
                        text,
                        fontsize=self.inline_fontsize,
                        fontname=self.inline_fontname,
                        fontfile=self.inline_fontfile,
                        color=self.inline_color,
                        fill_opacity=self._text_fill_opacity(),
                        border_width=0,
                        overlay=True,
                    )
            row = self.page_list.currentRow()
            self.close_inline_editor()
            self.show_selected_page(row)
            self.refresh_page_list(keep_row=row)
            self.status_label.setText("Textänderung übernommen. Jetzt mit 'Speichern als...' dauerhaft speichern.")
        except Exception as exc:
            QMessageBox.critical(self, "Fehler", f"Text konnte nicht übernommen werden:\n{exc}")

    def save_pdf(self):
        if self.preview_label.overlay_active:
            self.apply_inline_text_edit()
        if not self.doc or not self.page_order:
            QMessageBox.warning(self, "Nicht möglich", "Es gibt keine Seiten zum Speichern.")
            return
        default_name = "bearbeitete_pdf.pdf"
        if self.pdf_path:
            base, _ = os.path.splitext(os.path.basename(self.pdf_path))
            default_name = f"{base}_bearbeitet.pdf"
        save_path, _ = QFileDialog.getSaveFileName(self, "PDF speichern als", os.path.join(os.path.expanduser("~"), default_name), "PDF-Dateien (*.pdf)")
        if not save_path:
            return
        if not save_path.lower().endswith(".pdf"):
            save_path += ".pdf"
        try:
            new_doc = fitz.open()
            for page_index in self.page_order:
                new_doc.insert_pdf(self.doc, from_page=page_index, to_page=page_index)
            new_doc.save(save_path, garbage=4, deflate=True)
            new_doc.close()
            QMessageBox.information(self, "Gespeichert", f"PDF wurde gespeichert:\n{save_path}")
            self.status_label.setText(f"Gespeichert: {save_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Fehler", f"PDF konnte nicht gespeichert werden:\n{exc}")


def main():
    app = QApplication(sys.argv)
    window = PdfOrganizerApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
