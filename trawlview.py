import csv
import json
import os
import sqlite3
import struct
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtCore import QDateTime, QSettings, QTime, QUrl, Qt
from PyQt6.QtGui import QAction, QCursor, QImage, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

try:
    import cv2
except ImportError:
    cv2 = None

# Build Metadata
APP_NAME = "Trawl video-telemetry viewer"
APP_VERSION = "v2.3.0"
APP_BUILD_DATE = "October 24, 2026"
DEVELOPER_NAME = "Sean Rohan"
DEVELOPER_EMAIL = "sean.rohan@noaa.gov"

TIMEZONE_OPTIONS = [
    ("UTC-12:00", -12),
    ("UTC-11:00", -11),
    ("UTC-10:00 (HST)", -10),
    ("UTC-09:00 (AKST)", -9),
    ("UTC-08:00 (PST / Default)", -8),
    ("UTC-07:00 (MST/PDT)", -7),
    ("UTC-06:00 (CST)", -6),
    ("UTC-05:00 (EST)", -5),
    ("UTC-04:00 (AST)", -4),
    ("UTC-03:00", -3),
    ("UTC-02:00", -2),
    ("UTC-01:00", -1),
    ("UTC+00:00 (UTC)", 0),
    ("UTC+01:00 (CET)", 1),
    ("UTC+02:00 (EET)", 2),
    ("UTC+03:00 (MSK)", 3),
    ("UTC+04:00", 4),
    ("UTC+05:00", 5),
    ("UTC+06:00", 6),
    ("UTC+07:00", 7),
    ("UTC+08:00 (AWST)", 8),
    ("UTC+09:00 (JST)", 9),
    ("UTC+10:00 (AEST)", 10),
    ("UTC+11:00", 11),
    ("UTC+12:00 (NZST)", 12),
]


class VideoCatalogDB:
    """Manages SQLite storage for scanned video metadata, survey fields, and comments."""

    def __init__(self, db_path="video_catalog.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS video_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name TEXT NOT NULL,
                    file_path TEXT UNIQUE NOT NULL,
                    raw_start_epoch REAL,
                    start_epoch REAL,
                    end_epoch REAL,
                    duration_sec REAL,
                    haul TEXT DEFAULT '',
                    position TEXT DEFAULT '',
                    time_offset REAL DEFAULT 0.0,
                    description TEXT DEFAULT '',
                    comments TEXT DEFAULT ''
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS timestamped_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    video_pos_ms INTEGER NOT NULL,
                    epoch_sec REAL NOT NULL,
                    dt_display TEXT NOT NULL,
                    comment TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(video_catalog);")
            cols = [row[1] for row in cursor.fetchall()]
            migrations = [
                ("raw_start_epoch", "REAL", "0.0"),
                ("haul", "TEXT", "''"),
                ("position", "TEXT", "''"),
                ("time_offset", "REAL", "0.0"),
            ]
            for col_name, col_type, default_val in migrations:
                if col_name not in cols:
                    conn.execute(
                        f"ALTER TABLE video_catalog ADD COLUMN {col_name} {col_type} DEFAULT {default_val};"
                    )
            conn.commit()

    def upsert_scanned_video(
        self,
        file_name: str,
        file_path: str,
        raw_start_epoch: float,
        duration_sec: float,
    ):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT time_offset FROM video_catalog WHERE file_path = ?;",
                (file_path,),
            )
            row = cursor.fetchone()
            offset = row[0] if (row and row[0] is not None) else 0.0
            start_epoch = raw_start_epoch + offset
            end_epoch = start_epoch + duration_sec

            conn.execute(
                """
                INSERT INTO video_catalog (
                    file_name, file_path, raw_start_epoch, start_epoch, end_epoch, duration_sec, time_offset
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_name = excluded.file_name,
                    raw_start_epoch = excluded.raw_start_epoch,
                    start_epoch = excluded.raw_start_epoch + video_catalog.time_offset,
                    end_epoch = excluded.raw_start_epoch + video_catalog.time_offset + excluded.duration_sec,
                    duration_sec = excluded.duration_sec;
            """,
                (
                    file_name,
                    file_path,
                    raw_start_epoch,
                    start_epoch,
                    end_epoch,
                    duration_sec,
                    offset,
                ),
            )
            conn.commit()

    def rename_video(self, old_path: str, new_path: str, new_name: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE video_catalog
                SET file_name = ?, file_path = ?
                WHERE file_path = ?;
            """,
                (new_name, new_path, old_path),
            )
            conn.execute(
                """
                UPDATE timestamped_comments
                SET file_name = ?, file_path = ?
                WHERE file_path = ?;
            """,
                (new_name, new_path, old_path),
            )
            conn.commit()

    def update_video_details(
        self,
        file_path: str,
        haul: str,
        position: str,
        time_offset: float,
        description: str,
        comments: str,
        effective_start: float,
        duration_sec: float,
    ):
        end_epoch = effective_start + duration_sec
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE video_catalog
                SET haul = ?,
                    position = ?,
                    time_offset = ?,
                    description = ?,
                    comments = ?,
                    start_epoch = ?,
                    end_epoch = ?
                WHERE file_path = ?;
            """,
                (
                    haul,
                    position,
                    time_offset,
                    description,
                    comments,
                    effective_start,
                    end_epoch,
                    file_path,
                ),
            )
            conn.commit()

    def import_catalog_records(self, records: list[dict], mode: str = "append"):
        with sqlite3.connect(self.db_path) as conn:
            if mode == "replace":
                conn.execute("DELETE FROM video_catalog;")
            for r in records:
                try:
                    raw_start = float(r.get("raw_start_epoch") or 0.0)
                    start_ep = float(r.get("start_epoch") or 0.0)
                    end_ep = float(r.get("end_epoch") or 0.0)
                    dur = float(r.get("duration_sec") or 0.0)
                    offset = float(
                        r.get("time_offset_s") or r.get("time_offset") or 0.0
                    )
                except (ValueError, TypeError):
                    raw_start = start_ep = end_ep = dur = offset = 0.0

                conn.execute(
                    """
                    INSERT INTO video_catalog (
                        file_name, file_path, raw_start_epoch, start_epoch, end_epoch,
                        duration_sec, haul, position, time_offset, description, comments
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        file_name = excluded.file_name,
                        raw_start_epoch = excluded.raw_start_epoch,
                        start_epoch = excluded.start_epoch,
                        end_epoch = excluded.end_epoch,
                        duration_sec = excluded.duration_sec,
                        haul = excluded.haul,
                        position = excluded.position,
                        time_offset = excluded.time_offset,
                        description = excluded.description,
                        comments = excluded.comments;
                """,
                    (
                        r.get("file_name", ""),
                        r.get("file_path", ""),
                        raw_start,
                        start_ep,
                        end_ep,
                        dur,
                        r.get("haul", ""),
                        r.get("position", ""),
                        offset,
                        r.get("description", ""),
                        r.get("comments", ""),
                    ),
                )
            conn.commit()

    def add_timestamped_comment(
        self,
        file_path: str,
        file_name: str,
        pos_ms: int,
        epoch_sec: float,
        dt_display: str,
        comment: str,
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO timestamped_comments (file_path, file_name, video_pos_ms, epoch_sec, dt_display, comment)
                VALUES (?, ?, ?, ?, ?, ?);
            """,
                (
                    file_path,
                    file_name,
                    pos_ms,
                    epoch_sec,
                    dt_display,
                    comment,
                ),
            )
            conn.commit()

    def import_comments_records(self, records: list[dict], mode: str = "append"):
        with sqlite3.connect(self.db_path) as conn:
            if mode == "replace":
                conn.execute("DELETE FROM timestamped_comments;")
            for r in records:
                try:
                    pos_ms = int(float(r.get("video_pos_ms") or 0))
                    epoch = float(r.get("epoch_sec") or 0.0)
                except (ValueError, TypeError):
                    pos_ms = 0
                    epoch = 0.0

                created = r.get("created_at") or datetime.now(timezone.utc).isoformat()

                conn.execute(
                    """
                    INSERT INTO timestamped_comments (
                        file_path, file_name, video_pos_ms, epoch_sec, dt_display, comment, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                    (
                        r.get("file_path", ""),
                        r.get("file_name", ""),
                        pos_ms,
                        epoch,
                        r.get("dt_display", ""),
                        r.get("comment", ""),
                        created,
                    ),
                )
            conn.commit()

    def get_comments_for_video(self, file_path: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM timestamped_comments WHERE file_path = ? ORDER BY epoch_sec ASC;",
                (file_path,),
            )
            return cursor.fetchall()

    def get_all_comments(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM timestamped_comments ORDER BY file_name ASC, epoch_sec ASC;"
            )
            return cursor.fetchall()

    def delete_comment(self, comment_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM timestamped_comments WHERE id = ?;", (comment_id,)
            )
            conn.commit()

    def get_all_videos(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM video_catalog ORDER BY start_epoch ASC, file_name ASC;"
            )
            return cursor.fetchall()

    def get_video(self, file_path: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM video_catalog WHERE file_path = ?;", (file_path,)
            )
            return cursor.fetchone()

    def find_videos_covering_time(
        self, target_epoch: float, window_sec: float = 3600.0
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = """
                SELECT *, 
                       CASE WHEN (? >= start_epoch AND ? <= end_epoch) THEN 1 ELSE 0 END AS is_exact
                FROM video_catalog
                WHERE (? >= (start_epoch - ?) AND ? <= (end_epoch + ?))
                ORDER BY is_exact DESC, ABS(start_epoch - ?) ASC;
            """
            cursor.execute(
                query,
                (
                    target_epoch,
                    target_epoch,
                    target_epoch,
                    window_sec,
                    target_epoch,
                    window_sec,
                    target_epoch,
                ),
            )
            return cursor.fetchall()


def extract_video_info(
    video_path: str, tz_offset_hours: int
) -> tuple[float, float, str]:
    """Pure-Python QuickTime atom parser to extract creation timestamp without FFmpeg."""
    QUICKTIME_EPOCH_OFFSET = 2082844800
    offset_seconds = tz_offset_hours * 3600

    raw_start_epoch = None
    duration_sec = 0.0
    timecode = "00:00:00:00"

    try:
        with open(video_path, "rb") as f:
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                atom_size, atom_type = struct.unpack(">I4s", header)
                header_len = 8
                if atom_size == 1:
                    atom_size = struct.unpack(">Q", f.read(8))[0]
                    header_len = 16
                elif atom_size == 0:
                    break

                if atom_type == b"moov":
                    moov_bytes_left = atom_size - header_len
                    while moov_bytes_left > 8:
                        sub_header = f.read(8)
                        if len(sub_header) < 8:
                            break
                        sub_size, sub_type = struct.unpack(">I4s", sub_header)
                        sub_header_len = 8
                        if sub_size == 1:
                            sub_size = struct.unpack(">Q", f.read(8))[0]
                            sub_header_len = 16

                        if sub_type == b"mvhd":
                            version = struct.unpack(">B", f.read(1))[0]
                            f.seek(3, 1)

                            if version == 0:
                                (
                                    raw_creation,
                                    raw_mod,
                                    timescale,
                                    raw_dur,
                                ) = struct.unpack(">IIII", f.read(16))
                            elif version == 1:
                                (
                                    raw_creation,
                                    raw_mod,
                                    timescale,
                                    raw_dur,
                                ) = struct.unpack(">QQIQ", f.read(28))
                            else:
                                break

                            local_epoch = raw_creation - QUICKTIME_EPOCH_OFFSET
                            if local_epoch > 0:
                                raw_start_epoch = float(
                                    local_epoch - offset_seconds
                                )
                            if timescale > 0:
                                duration_sec = float(raw_dur / timescale)
                            break
                        else:
                            f.seek(sub_size - sub_header_len, 1)
                            moov_bytes_left -= sub_size
                    break
                else:
                    f.seek(atom_size - header_len, 1)
    except Exception:
        pass

    if raw_start_epoch is None:
        raw_start_epoch = os.path.getmtime(video_path)

    if duration_sec <= 0.0 and cv2 is not None:
        try:
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            if fps > 0 and frames > 0:
                duration_sec = frames / fps
            cap.release()
        except Exception:
            pass

    return raw_start_epoch, duration_sec, timecode


class DynamicTimezoneDateAxisItem(pg.DateAxisItem):

    def __init__(self, tz: timezone, orientation="bottom", **kwargs):
        super().__init__(orientation=orientation, **kwargs)
        self.tz = tz

    def tickStrings(self, values, scale, spacing):
        strings = []
        for val in values:
            try:
                dt = datetime.fromtimestamp(val, tz=self.tz)
                if spacing >= 86400:
                    strings.append(dt.strftime("%Y-%m-%d"))
                elif spacing >= 3600:
                    strings.append(dt.strftime("%m-%d %H:%M"))
                elif spacing >= 1:
                    strings.append(dt.strftime("%H:%M:%S"))
                else:
                    strings.append(dt.strftime("%H:%M:%S.%f")[:12])
            except Exception:
                strings.append("")
        return strings


class ThumbnailPopup(QWidget):

    def __init__(self):
        super().__init__(
            None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("""
            QWidget {
                background-color: #1e1e1e;
                border: 1px solid #555;
                border-radius: 4px;
                color: #ffffff;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.lbl_image = QLabel()
        self.lbl_image.setFixedSize(160, 90)
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_image.setStyleSheet("background-color: #000;")
        layout.addWidget(self.lbl_image)

        self.lbl_text = QLabel("--:--:--")
        self.lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_text.setStyleSheet(
            "font-size: 11px; font-family: monospace; font-weight: bold;"
        )
        layout.addWidget(self.lbl_text)

    def update_preview(self, pixmap: QPixmap, text: str):
        if pixmap and not pixmap.isNull():
            self.lbl_image.setVisible(True)
            self.lbl_image.setPixmap(pixmap)
        else:
            self.lbl_image.setVisible(False)
        self.lbl_text.setText(text)
        self.adjustSize()


class ThumbnailSlider(QSlider):

    def __init__(self, orientation, parent=None, thumbnail_callback=None):
        super().__init__(orientation, parent)
        self.thumbnail_callback = thumbnail_callback
        self.setMouseTracking(True)
        self.popup = ThumbnailPopup()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self.maximum() <= 0 or not self.thumbnail_callback:
            return

        ratio = max(0.0, min(1.0, event.position().x() / self.width()))
        pos_ms = int(ratio * self.maximum())

        pixmap, time_str = self.thumbnail_callback(pos_ms)
        self.popup.update_preview(pixmap, time_str)

        global_pos = self.mapToGlobal(event.position().toPoint())
        popup_x = int(global_pos.x() - (self.popup.width() / 2))
        popup_y = int(global_pos.y() - self.popup.height() - 10)
        self.popup.move(popup_x, popup_y)
        self.popup.show()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.popup.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            ratio = max(0.0, min(1.0, event.position().x() / self.width()))
            self.setValue(int(ratio * self.maximum()))
            self.sliderMoved.emit(self.value())
        super().mousePressEvent(event)


class ColumnSelectorDialog(QDialog):
    """Dual-list selector dialog for choosing which telemetry columns to plot."""

    def __init__(self, all_columns, pre_selected=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Telemetry Data Columns")
        self.resize(600, 420)

        self.all_columns = all_columns
        self.pre_selected = pre_selected or []

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        instruction = QLabel(
            "Select which telemetry data streams to display in the plot dropdown menus:"
        )
        instruction.setStyleSheet("font-size: 12px; margin-bottom: 4px;")
        main_layout.addWidget(instruction)

        lists_layout = QHBoxLayout()

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Available Columns:"))
        self.list_available = QListWidget()
        self.list_available.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        left_layout.addWidget(self.list_available)
        lists_layout.addLayout(left_layout, stretch=1)

        btn_layout = QVBoxLayout()
        btn_layout.addStretch()
        self.btn_add = QPushButton(">")
        self.btn_add.setToolTip("Add selected")
        self.btn_add.clicked.connect(self.add_selected)
        btn_layout.addWidget(self.btn_add)

        self.btn_remove = QPushButton("<")
        self.btn_remove.setToolTip("Remove selected")
        self.btn_remove.clicked.connect(self.remove_selected)
        btn_layout.addWidget(self.btn_remove)

        self.btn_add_all = QPushButton(">>")
        self.btn_add_all.setToolTip("Add all")
        self.btn_add_all.clicked.connect(self.add_all)
        btn_layout.addWidget(self.btn_add_all)

        self.btn_remove_all = QPushButton("<<")
        self.btn_remove_all.setToolTip("Remove all")
        self.btn_remove_all.clicked.connect(self.remove_all)
        btn_layout.addWidget(self.btn_remove_all)
        btn_layout.addStretch()
        lists_layout.addLayout(btn_layout)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Selected Columns (Plot Menus):"))
        self.list_selected = QListWidget()
        self.list_selected.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        right_layout.addWidget(self.list_selected)
        lists_layout.addLayout(right_layout, stretch=1)

        main_layout.addLayout(lists_layout)

        bottom_box = QHBoxLayout()
        bottom_box.addStretch()
        self.btn_ok = QPushButton("Apply Columns")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        bottom_box.addWidget(self.btn_ok)
        bottom_box.addWidget(self.btn_cancel)
        main_layout.addLayout(bottom_box)

        self.populate_lists()

    def populate_lists(self):
        selected_set = [c for c in self.pre_selected if c in self.all_columns]
        if not selected_set and self.all_columns:
            selected_set = list(self.all_columns)

        available_set = [c for c in self.all_columns if c not in selected_set]

        self.list_available.addItems(available_set)
        self.list_selected.addItems(selected_set)

    def add_selected(self):
        for item in self.list_available.selectedItems():
            self.list_selected.addItem(item.text())
            self.list_available.takeItem(self.list_available.row(item))

    def remove_selected(self):
        for item in self.list_selected.selectedItems():
            self.list_available.addItem(item.text())
            self.list_selected.takeItem(self.list_selected.row(item))

    def add_all(self):
        while self.list_available.count() > 0:
            item = self.list_available.takeItem(0)
            self.list_selected.addItem(item.text())

    def remove_all(self):
        while self.list_selected.count() > 0:
            item = self.list_selected.takeItem(0)
            self.list_available.addItem(item.text())

    def get_selected_columns(self) -> list[str]:
        return [
            self.list_selected.item(i).text()
            for i in range(self.list_selected.count())
        ]


class FindVideoByDateDialog(QDialog):

    def __init__(self, catalog_db: VideoCatalogDB, tz: timezone, parent=None):
        super().__init__(parent)
        self.catalog_db = catalog_db
        self.tz = tz
        self.selected_file_path = None

        self.setWindowTitle("Find Video by Date & Time")
        self.resize(850, 480)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        search_bar = QHBoxLayout()
        search_bar.addWidget(QLabel("Target Date & Time:"))

        self.dt_picker = QDateTimeEdit()
        self.dt_picker.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_picker.setCalendarPopup(True)
        self.dt_picker.setDateTime(QDateTime.currentDateTime())
        search_bar.addWidget(self.dt_picker)

        self.btn_search = QPushButton("Search Catalog")
        self.btn_search.clicked.connect(self.perform_search)
        search_bar.addWidget(self.btn_search)
        search_bar.addStretch()
        layout.addLayout(search_bar)

        self.lbl_info = QLabel("Enter a timestamp and click 'Search Catalog'.")
        self.lbl_info.setStyleSheet("color: #777; font-size: 11px;")
        layout.addWidget(self.lbl_info)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Match",
            "File Name",
            "Haul",
            "Position",
            "Start Time",
            "End Time",
            "Description",
        ])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch
        )
        self.table.cellDoubleClicked.connect(self.on_row_double_clicked)
        layout.addWidget(self.table)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        self.btn_load = QPushButton("Load Selected Video")
        self.btn_load.setEnabled(False)
        self.btn_load.clicked.connect(self.accept_selection)
        btn_box.addWidget(self.btn_load)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)
        layout.addLayout(btn_box)

        self.table.itemSelectionChanged.connect(self.on_selection_changed)

    def perform_search(self):
        qdt = self.dt_picker.dateTime()
        py_dt = datetime(
            qdt.date().year(),
            qdt.date().month(),
            qdt.date().day(),
            qdt.time().hour(),
            qdt.time().minute(),
            qdt.time().second(),
            tzinfo=self.tz,
        )
        target_epoch = py_dt.timestamp()

        results = self.catalog_db.find_videos_covering_time(
            target_epoch, window_sec=7200.0
        )
        self.table.setRowCount(len(results))

        if not results:
            self.lbl_info.setText(
                "No videos found within 2 hours of this timestamp."
            )
            self.btn_load.setEnabled(False)
            return

        exact_count = sum(1 for r in results if r["is_exact"] == 1)
        self.lbl_info.setText(
            f"Found {len(results)} candidate(s) ({exact_count} exact time overlap)."
        )

        for row, r in enumerate(results):
            match_str = "EXACT" if r["is_exact"] == 1 else "Near"
            item_match = QTableWidgetItem(match_str)
            if r["is_exact"] == 1:
                item_match.setForeground(Qt.GlobalColor.green)

            item_file = QTableWidgetItem(r["file_name"])
            item_file.setData(Qt.ItemDataRole.UserRole, r["file_path"])

            item_haul = QTableWidgetItem(r["haul"] or "")
            item_pos = QTableWidgetItem(r["position"] or "")

            dt_s = datetime.fromtimestamp(r["start_epoch"], tz=self.tz).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            dt_e = datetime.fromtimestamp(r["end_epoch"], tz=self.tz).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            item_start = QTableWidgetItem(dt_s)
            item_end = QTableWidgetItem(dt_e)
            item_desc = QTableWidgetItem(r["description"] or "")

            self.table.setItem(row, 0, item_match)
            self.table.setItem(row, 1, item_file)
            self.table.setItem(row, 2, item_haul)
            self.table.setItem(row, 3, item_pos)
            self.table.setItem(row, 4, item_start)
            self.table.setItem(row, 5, item_end)
            self.table.setItem(row, 6, item_desc)

        if len(results) > 0:
            self.table.selectRow(0)

    def on_selection_changed(self):
        self.btn_load.setEnabled(len(self.table.selectedItems()) > 0)

    def on_row_double_clicked(self, row, col):
        self.accept_selection()

    def accept_selection(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return
        row = selected_rows[0].row()
        item = self.table.item(row, 1)
        if item:
            self.selected_file_path = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


class TrawlSyncViewer(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1920, 960)

        self.settings = QSettings("NOAA_AFSC", "TrawlSyncViewer")

        # Initialize Catalog DB
        saved_catalog_db = self.settings.value(
            "catalog_db_path", "video_catalog.db"
        )
        self.catalog_db = VideoCatalogDB(saved_catalog_db)

        # Timezone configuration
        self.tz_offset_hours = -8
        self.tz = timezone(timedelta(hours=self.tz_offset_hours), name="UTC-8")

        # Video state
        self.current_video_path = None
        self.video_cap = None
        self.video_raw_epoch = None
        self.video_start_epoch = None
        self.video_duration_ms = 0
        self.base_timecode = "00:00:00:00"

        # Telemetry data & selected columns
        self.df = None
        self.all_db_columns = []
        self.selected_columns = []

        self._init_menu()
        self._init_ui()
        self._init_player()
        self.refresh_catalog_list()

        # Restore telemetry DB if previously loaded
        saved_telem_db = self.settings.value("telemetry_db_path", "")
        if saved_telem_db and os.path.exists(saved_telem_db):
            self.load_sqlite_db_from_path(
                saved_telem_db, silent=True, prompt_columns=False
            )

    def _init_menu(self):
        menubar = self.menuBar()

        # ================= FILE MENU =================
        file_menu = menubar.addMenu("File")

        act_load_vid = QAction("Load Single .MOV Video...", self)
        act_load_vid.triggered.connect(self.load_video_dialog)
        file_menu.addAction(act_load_vid)

        act_scan_dir = QAction("Scan Directory for .MOV Files...", self)
        act_scan_dir.triggered.connect(self.scan_directory)
        file_menu.addAction(act_scan_dir)

        act_find_date = QAction("Find Video by Date/Time...", self)
        act_find_date.triggered.connect(self.open_find_video_dialog)
        file_menu.addAction(act_find_date)

        file_menu.addSeparator()

        act_open_cat_db = QAction("Open Video Catalog Database...", self)
        act_open_cat_db.triggered.connect(self.open_existing_catalog_db)
        file_menu.addAction(act_open_cat_db)

        file_menu.addSeparator()

        act_exit = QAction("Exit", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # ================= IMPORT MENU =================
        import_menu = menubar.addMenu("Import")

        act_load_db = QAction("Load Telemetry SQLite Database...", self)
        act_load_db.triggered.connect(self.load_sqlite_db_dialog)
        import_menu.addAction(act_load_db)

        import_menu.addSeparator()

        act_import_cat = QAction("Import Video Catalog from CSV...", self)
        act_import_cat.triggered.connect(self.import_catalog_from_csv)
        import_menu.addAction(act_import_cat)

        act_import_comments = QAction(
            "Import Timestamped Comments from CSV...", self
        )
        act_import_comments.triggered.connect(self.import_comments_from_csv)
        import_menu.addAction(act_import_comments)

        # ================= TOOLS MENU =================
        tools_menu = menubar.addMenu("Tools")

        act_select_cols = QAction("Select Data Columns...", self)
        act_select_cols.triggered.connect(self.open_column_selector_manual)
        tools_menu.addAction(act_select_cols)

        # ================= EXPORT MENU =================
        export_menu = menubar.addMenu("Export")

        act_export_cat = QAction("Export Video Catalog...", self)
        act_export_cat.triggered.connect(self.export_catalog_unified)
        export_menu.addAction(act_export_cat)

        act_export_comments = QAction("Export Timestamped Comments...", self)
        act_export_comments.triggered.connect(self.export_comments_unified)
        export_menu.addAction(act_export_comments)

        # ================= ABOUT MENU =================
        about_menu = menubar.addMenu("About")

        act_about = QAction(f"About {APP_NAME}", self)
        act_about.triggered.connect(self.show_about_dialog)
        about_menu.addAction(act_about)

    def _init_ui(self):
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # ================= PANE 1: RESIZABLE VIDEO CATALOG =================
        self.catalog_splitter = QSplitter(Qt.Orientation.Vertical)

        cat_top_widget = QWidget()
        cat_top_layout = QVBoxLayout(cat_top_widget)
        cat_top_layout.setContentsMargins(4, 4, 4, 4)

        cat_header = QLabel("Video Catalog")
        cat_header.setStyleSheet("font-size: 13px; font-weight: bold;")
        cat_top_layout.addWidget(cat_header)

        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("Filter haul, pos, file, desc...")
        self.txt_filter.textChanged.connect(self.filter_catalog_list)
        cat_top_layout.addWidget(self.txt_filter)

        self.list_videos = QListWidget()
        self.list_videos.itemSelectionChanged.connect(
            self.on_catalog_item_selected
        )
        cat_top_layout.addWidget(self.list_videos)

        self.catalog_splitter.addWidget(cat_top_widget)

        cat_details_widget = QWidget()
        cat_details_layout = QVBoxLayout(cat_details_widget)
        cat_details_layout.setContentsMargins(4, 4, 4, 4)

        cat_details_layout.addWidget(QLabel("<b>Catalog Video Details</b>"))
        self.lbl_cat_file = QLabel("File: None")
        self.lbl_cat_file.setWordWrap(True)
        cat_details_layout.addWidget(self.lbl_cat_file)

        self.lbl_cat_timing = QLabel("Raw Start: --\nStart: --\nEnd: --")
        self.lbl_cat_timing.setStyleSheet(
            "font-family: monospace; font-size: 11px; color: #888;"
        )
        cat_details_layout.addWidget(self.lbl_cat_timing)

        form = QFormLayout()
        self.txt_haul = QLineEdit()
        self.txt_haul.setPlaceholderText("e.g. Haul 04")
        form.addRow("Haul:", self.txt_haul)

        self.txt_position = QLineEdit()
        self.txt_position.setPlaceholderText("e.g. Headrope / Port Door")
        form.addRow("Position:", self.txt_position)

        self.spin_time_offset = QDoubleSpinBox()
        self.spin_time_offset.setRange(-864000.0, 864000.0)
        self.spin_time_offset.setDecimals(2)
        self.spin_time_offset.setSingleStep(1.0)
        self.spin_time_offset.setSuffix(" s")
        self.spin_time_offset.valueChanged.connect(
            self.on_offset_spinbox_changed
        )
        form.addRow("Time Offset:", self.spin_time_offset)

        self.txt_description = QLineEdit()
        form.addRow("Description:", self.txt_description)
        cat_details_layout.addLayout(form)

        cat_details_layout.addWidget(QLabel("Comments:"))
        self.txt_comments = QTextEdit()
        self.txt_comments.setFixedHeight(45)
        cat_details_layout.addWidget(self.txt_comments)

        action_btn_layout = QHBoxLayout()
        self.btn_save_notes = QPushButton("Save Details to DB")
        self.btn_save_notes.clicked.connect(self.save_video_details)

        self.btn_rename_file = QPushButton("Rename MOV File...")
        self.btn_rename_file.clicked.connect(self.rename_selected_video)

        action_btn_layout.addWidget(self.btn_save_notes)
        action_btn_layout.addWidget(self.btn_rename_file)
        cat_details_layout.addLayout(action_btn_layout)

        self.catalog_splitter.addWidget(cat_details_widget)
        self.catalog_splitter.setSizes([350, 320])

        self.main_splitter.addWidget(self.catalog_splitter)

        # ================= PANE 2: VIDEO PLAYER & SNAPSHOT =================
        video_widget_container = QWidget()
        video_layout = QVBoxLayout(video_widget_container)
        video_layout.setContentsMargins(4, 4, 4, 4)

        self.video_widget = QVideoWidget()
        video_layout.addWidget(self.video_widget, stretch=8)

        vid_btn_layout = QHBoxLayout()
        self.btn_play = QPushButton("Play")
        self.btn_play.setEnabled(False)
        self.btn_play.clicked.connect(self.toggle_play)

        self.btn_snapshot = QPushButton("Capture Screen (PNG)")
        self.btn_snapshot.clicked.connect(self.capture_video_frame)

        vid_btn_layout.addWidget(self.btn_play)
        vid_btn_layout.addWidget(self.btn_snapshot)

        vid_btn_layout.addWidget(QLabel("Speed:"))
        self.speed_selector = QComboBox()
        self.speed_selector.addItems(
            ["0.25x", "0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x", "4.0x"]
        )
        self.speed_selector.setCurrentText("1.0x")
        self.speed_selector.currentTextChanged.connect(self.on_speed_changed)
        vid_btn_layout.addWidget(self.speed_selector)

        vid_btn_layout.addWidget(QLabel("Timezone:"))
        self.tz_selector = QComboBox()
        for label, offset in TIMEZONE_OPTIONS:
            self.tz_selector.addItem(label, offset)
        self.tz_selector.setCurrentIndex(4)  # UTC-8
        self.tz_selector.currentIndexChanged.connect(self.on_timezone_changed)
        vid_btn_layout.addWidget(self.tz_selector)

        video_layout.addLayout(vid_btn_layout)

        self.slider = ThumbnailSlider(
            Qt.Orientation.Horizontal,
            thumbnail_callback=self.get_thumbnail_preview,
        )
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.set_position)
        video_layout.addWidget(self.slider)

        timing_layout = QHBoxLayout()
        self.lbl_start_label = QLabel("Video Start (UTC-8):")
        timing_layout.addWidget(self.lbl_start_label)

        self.dt_video_start = QDateTimeEdit()
        self.dt_video_start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_video_start.setCalendarPopup(True)
        self.dt_video_start.dateTimeChanged.connect(
            self.on_start_datetime_changed
        )
        timing_layout.addWidget(self.dt_video_start)

        self.lbl_time_info = QLabel("Elapsed: 00:00:00 | Video DT: --")
        self.lbl_time_info.setStyleSheet(
            "font-family: monospace; font-size: 12px; color: #555;"
        )
        timing_layout.addWidget(self.lbl_time_info)
        timing_layout.addStretch()

        video_layout.addLayout(timing_layout)
        self.main_splitter.addWidget(video_widget_container)

        # ================= PANE 3: 3 DATA PLOTS & COMMENTS PANE =================
        plot_and_comments_container = QWidget()
        right_layout = QVBoxLayout(plot_and_comments_container)
        right_layout.setContentsMargins(4, 4, 4, 4)

        db_controls = QHBoxLayout()
        self.btn_fit_video = QPushButton("Fit Video")
        self.btn_fit_video.clicked.connect(self.lock_x_axes_to_video)
        self.btn_fit_data = QPushButton("Fit All Data")
        self.btn_fit_data.clicked.connect(self.fit_all_data)
        db_controls.addWidget(self.btn_fit_video)
        db_controls.addWidget(self.btn_fit_data)
        db_controls.addStretch()
        right_layout.addLayout(db_controls)

        pg.setConfigOptions(antialias=True)

        # --- Plot 1 (Upper) ---
        p1_bar = QHBoxLayout()
        p1_bar.addWidget(QLabel("Plot 1:"))
        self.stream_selector_1 = QComboBox()
        self.stream_selector_1.setMinimumWidth(320)
        self.stream_selector_1.currentTextChanged.connect(
            lambda name: self.update_plot_data(name, plot_idx=1)
        )
        p1_bar.addWidget(self.stream_selector_1)
        self.lbl_stream_value_1 = QLabel("Value: --")
        self.lbl_stream_value_1.setStyleSheet(
            "font-weight: bold; padding-left: 6px;"
        )
        p1_bar.addWidget(self.lbl_stream_value_1)
        p1_bar.addStretch()
        right_layout.addLayout(p1_bar)

        self.date_axis_1 = DynamicTimezoneDateAxisItem(
            self.tz, orientation="bottom"
        )
        self.plot_1 = pg.PlotWidget(axisItems={"bottom": self.date_axis_1})
        self.plot_1.showGrid(x=True, y=True, alpha=0.3)
        self.plot_1.setLabel(
            "bottom", f"Time ({self.tz_selector.currentText().split()[0]})"
        )
        self.plot_1.plotItem.vb.setMouseEnabled(x=False, y=True)
        self.plot_1.scene().sigMouseClicked.connect(
            lambda ev: self.on_plot_clicked(ev, self.plot_1)
        )
        self.plot_1.scene().sigMouseMoved.connect(
            lambda pos: self.on_plot_hover(pos, 1)
        )
        self.curve_1 = self.plot_1.plot(
            pen=pg.mkPen(color="#00E5FF", width=1.5),
            symbol="o",
            symbolSize=3,
            symbolBrush="#00E5FF",
        )
        self.time_line_1 = pg.InfiniteLine(
            pos=0,
            angle=90,
            movable=False,
            pen=pg.mkPen(color="#FF1744", width=2, style=Qt.PenStyle.DashLine),
        )
        self.plot_1.addItem(self.time_line_1)
        right_layout.addWidget(self.plot_1, stretch=2)

        # --- Plot 2 (Middle) ---
        p2_bar = QHBoxLayout()
        p2_bar.addWidget(QLabel("Plot 2:"))
        self.stream_selector_2 = QComboBox()
        self.stream_selector_2.setMinimumWidth(320)
        self.stream_selector_2.currentTextChanged.connect(
            lambda name: self.update_plot_data(name, plot_idx=2)
        )
        p2_bar.addWidget(self.stream_selector_2)
        self.lbl_stream_value_2 = QLabel("Value: --")
        self.lbl_stream_value_2.setStyleSheet(
            "font-weight: bold; padding-left: 6px;"
        )
        p2_bar.addWidget(self.lbl_stream_value_2)
        p2_bar.addStretch()
        right_layout.addLayout(p2_bar)

        self.date_axis_2 = DynamicTimezoneDateAxisItem(
            self.tz, orientation="bottom"
        )
        self.plot_2 = pg.PlotWidget(axisItems={"bottom": self.date_axis_2})
        self.plot_2.showGrid(x=True, y=True, alpha=0.3)
        self.plot_2.setLabel(
            "bottom", f"Time ({self.tz_selector.currentText().split()[0]})"
        )
        self.plot_2.plotItem.vb.setMouseEnabled(x=False, y=True)
        self.plot_2.scene().sigMouseClicked.connect(
            lambda ev: self.on_plot_clicked(ev, self.plot_2)
        )
        self.plot_2.scene().sigMouseMoved.connect(
            lambda pos: self.on_plot_hover(pos, 2)
        )
        self.plot_2.setXLink(self.plot_1)
        self.curve_2 = self.plot_2.plot(
            pen=pg.mkPen(color="#FFD600", width=1.5),
            symbol="o",
            symbolSize=3,
            symbolBrush="#FFD600",
        )
        self.time_line_2 = pg.InfiniteLine(
            pos=0,
            angle=90,
            movable=False,
            pen=pg.mkPen(color="#FF1744", width=2, style=Qt.PenStyle.DashLine),
        )
        self.plot_2.addItem(self.time_line_2)
        right_layout.addWidget(self.plot_2, stretch=2)

        # --- Plot 3 (Lower) ---
        p3_bar = QHBoxLayout()
        p3_bar.addWidget(QLabel("Plot 3:"))
        self.stream_selector_3 = QComboBox()
        self.stream_selector_3.setMinimumWidth(320)
        self.stream_selector_3.currentTextChanged.connect(
            lambda name: self.update_plot_data(name, plot_idx=3)
        )
        p3_bar.addWidget(self.stream_selector_3)
        self.lbl_stream_value_3 = QLabel("Value: --")
        self.lbl_stream_value_3.setStyleSheet(
            "font-weight: bold; padding-left: 6px;"
        )
        p3_bar.addWidget(self.lbl_stream_value_3)
        p3_bar.addStretch()
        right_layout.addLayout(p3_bar)

        self.date_axis_3 = DynamicTimezoneDateAxisItem(
            self.tz, orientation="bottom"
        )
        self.plot_3 = pg.PlotWidget(axisItems={"bottom": self.date_axis_3})
        self.plot_3.showGrid(x=True, y=True, alpha=0.3)
        self.plot_3.setLabel(
            "bottom", f"Time ({self.tz_selector.currentText().split()[0]})"
        )
        self.plot_3.plotItem.vb.setMouseEnabled(x=False, y=True)
        self.plot_3.scene().sigMouseClicked.connect(
            lambda ev: self.on_plot_clicked(ev, self.plot_3)
        )
        self.plot_3.scene().sigMouseMoved.connect(
            lambda pos: self.on_plot_hover(pos, 3)
        )
        self.plot_3.setXLink(self.plot_1)
        self.curve_3 = self.plot_3.plot(
            pen=pg.mkPen(color="#00E676", width=1.5),
            symbol="o",
            symbolSize=3,
            symbolBrush="#00E676",
        )
        self.time_line_3 = pg.InfiniteLine(
            pos=0,
            angle=90,
            movable=False,
            pen=pg.mkPen(color="#FF1744", width=2, style=Qt.PenStyle.DashLine),
        )
        self.plot_3.addItem(self.time_line_3)
        right_layout.addWidget(self.plot_3, stretch=2)

        # --- Comments Table Pane ---
        comments_box = QFrame()
        comments_box.setFrameShape(QFrame.Shape.StyledPanel)
        comments_layout = QVBoxLayout(comments_box)
        comments_layout.setContentsMargins(4, 4, 4, 4)

        comm_input_layout = QHBoxLayout()
        self.txt_new_comment = QLineEdit()
        self.txt_new_comment.setPlaceholderText(
            "Type observation comment and click 'Add Comment'..."
        )
        self.txt_new_comment.returnPressed.connect(self.add_comment_at_playback)
        self.btn_add_comment = QPushButton("Add Comment at Timestamp")
        self.btn_add_comment.clicked.connect(self.add_comment_at_playback)
        comm_input_layout.addWidget(self.txt_new_comment)
        comm_input_layout.addWidget(self.btn_add_comment)
        comments_layout.addLayout(comm_input_layout)

        self.table_comments = QTableWidget()
        self.table_comments.setColumnCount(4)
        self.table_comments.setHorizontalHeaderLabels(
            ["Elapsed", "Timestamp", "Comment", "Del"]
        )
        self.table_comments.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_comments.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_comments.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.table_comments.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_comments.cellDoubleClicked.connect(
            self.on_comment_double_clicked
        )
        comments_layout.addWidget(self.table_comments)

        right_layout.addWidget(comments_box, stretch=3)

        self.lbl_diagnostics = QLabel("DB Status: No telemetry DB loaded.")
        self.lbl_diagnostics.setStyleSheet(
            "font-size: 11px; color: #666; padding: 2px;"
        )
        right_layout.addWidget(self.lbl_diagnostics)

        self.main_splitter.addWidget(plot_and_comments_container)
        self.main_splitter.setSizes([350, 750, 550])

        self.setCentralWidget(self.main_splitter)

    def _init_player(self):
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)

    # ================= Prompt Append or Replace =================
    def prompt_append_or_replace(self, entity_name: str) -> str | None:
        """Prompts the user whether to append to or replace existing database records."""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(f"Import {entity_name}")
        msg_box.setText(
            f"How would you like to import the {entity_name.lower()} records?"
        )
        msg_box.setInformativeText(
            "• Append: Updates matching records and adds new entries.\n"
            "• Replace: Deletes all existing records in this table and replaces them."
        )
        btn_append = msg_box.addButton(
            "Append", QMessageBox.ButtonRole.AcceptRole
        )
        btn_replace = msg_box.addButton(
            "Replace", QMessageBox.ButtonRole.DestructiveRole
        )
        btn_cancel = msg_box.addButton(
            "Cancel", QMessageBox.ButtonRole.RejectRole
        )
        msg_box.setDefaultButton(btn_append)
        msg_box.exec()

        if msg_box.clickedButton() == btn_append:
            return "append"
        elif msg_box.clickedButton() == btn_replace:
            return "replace"
        return None

    # ================= CSV Import Tools =================
    def import_catalog_from_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Video Catalog CSV",
            "",
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                records = list(reader)

            if not records:
                QMessageBox.warning(
                    self, "Empty File", "No records found in the selected CSV file."
                )
                return

            mode = self.prompt_append_or_replace("Video Catalog")
            if not mode:
                return

            self.catalog_db.import_catalog_records(records, mode=mode)
            self.refresh_catalog_list()

            # Refresh details pane if current video was updated
            if self.current_video_path:
                rec = self.catalog_db.get_video(self.current_video_path)
                if rec:
                    self.txt_haul.setText(rec["haul"] or "")
                    self.txt_position.setText(rec["position"] or "")
                    self.txt_description.setText(rec["description"] or "")
                    self.txt_comments.setText(rec["comments"] or "")
                    offset_val = rec["time_offset"] or 0.0
                    self.spin_time_offset.blockSignals(True)
                    self.spin_time_offset.setValue(offset_val)
                    self.spin_time_offset.blockSignals(False)
                    if self.video_raw_epoch is not None:
                        self.video_start_epoch = self.video_raw_epoch + offset_val
                        self.update_video_start_display()
                        self.lock_x_axes_to_video()

            QMessageBox.information(
                self,
                "Import Successful",
                f"Successfully imported {len(records)} catalog records ({mode.capitalize()} mode).",
            )

        except Exception as e:
            QMessageBox.critical(
                self, "Import Error", f"Failed to import video catalog:\n{e}"
            )

    def import_comments_from_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Timestamped Comments CSV",
            "",
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                records = list(reader)

            if not records:
                QMessageBox.warning(
                    self, "Empty File", "No comments found in the selected CSV file."
                )
                return

            mode = self.prompt_append_or_replace("Timestamped Comments")
            if not mode:
                return

            self.catalog_db.import_comments_records(records, mode=mode)
            self.load_comments_for_current_video()

            QMessageBox.information(
                self,
                "Import Successful",
                f"Successfully imported {len(records)} comments ({mode.capitalize()} mode).",
            )

        except Exception as e:
            QMessageBox.critical(
                self, "Import Error", f"Failed to import comments:\n{e}"
            )

    # ================= About Dialog =================
    def show_about_dialog(self):
        about_text = (
            f"<h3>{APP_NAME}</h3>"
            f"<p><b>Version:</b> {APP_VERSION}<br>"
            f"<b>Build Date:</b> {APP_BUILD_DATE}</p>"
            f"<p><b>Developer:</b> {DEVELOPER_NAME}<br>"
            f"<b>Email:</b> <a href='mailto:{DEVELOPER_EMAIL}'>{DEVELOPER_EMAIL}</a><br>"
            f"<b>Organization:</b> NOAA Alaska Fisheries Science Center (AFSC)</p>"
            f"<p>A synchronized playback and multi-stream telemetry visualization tool "
            f"designed for marine fisheries and trawl gear surveys.</p>"
        )
        QMessageBox.about(self, f"About {APP_NAME}", about_text)

    # ================= Rename MOV Feature =================
    def rename_selected_video(self):
        items = self.list_videos.selectedItems()
        if not items:
            QMessageBox.warning(
                self, "Warning", "No video selected from the catalog."
            )
            return

        old_path = items[0].data(Qt.ItemDataRole.UserRole)
        old_name = os.path.basename(old_path)

        new_name, ok = QInputDialog.getText(
            self, "Rename MOV Video", "Enter new file name:", text=old_name
        )
        if not ok or not new_name.strip():
            return

        new_name = new_name.strip()
        if not new_name.lower().endswith(".mov"):
            new_name += ".mov"

        if new_name == old_name:
            return

        dir_name = os.path.dirname(old_path)
        new_path = os.path.join(dir_name, new_name)

        if os.path.exists(new_path):
            QMessageBox.critical(
                self,
                "File Exists",
                f"A file named '{new_name}' already exists in this folder.",
            )
            return

        is_active = self.current_video_path == old_path
        cur_pos = 0
        was_playing = False

        if is_active:
            was_playing = (
                self.player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
            )
            cur_pos = self.player.position()
            self.player.stop()
            self.player.setSource(QUrl())
            if self.video_cap is not None:
                self.video_cap.release()
                self.video_cap = None

        try:
            if os.path.exists(old_path):
                os.rename(old_path, new_path)

            self.catalog_db.rename_video(old_path, new_path, new_name)

            if is_active:
                self.load_video_from_path(new_path)
                self.player.setPosition(cur_pos)
                if was_playing:
                    self.player.play()

            self.refresh_catalog_list()

            for i in range(self.list_videos.count()):
                item = self.list_videos.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == new_path:
                    self.list_videos.setCurrentItem(item)
                    break

            QMessageBox.information(
                self, "Renamed", f"File successfully renamed to:\n{new_name}"
            )

        except Exception as e:
            QMessageBox.critical(
                self, "Rename Error", f"Failed to rename video:\n{e}"
            )
            if is_active:
                self.load_video_from_path(old_path)

    # ================= Column Selection Logic =================
    def open_column_selector_manual(self):
        if not self.all_db_columns:
            QMessageBox.warning(
                self,
                "No Data Loaded",
                "Please import a Telemetry SQLite database first.",
            )
            return
        self.prompt_column_selector()

    def prompt_column_selector(self):
        saved_cols_json = self.settings.value("selected_columns_json", "")
        pre_selected = self.selected_columns
        if not pre_selected and saved_cols_json:
            try:
                pre_selected = json.loads(saved_cols_json)
            except Exception:
                pass

        dialog = ColumnSelectorDialog(
            self.all_db_columns, pre_selected=pre_selected, parent=self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            chosen = dialog.get_selected_columns()
            if not chosen:
                QMessageBox.warning(
                    self,
                    "Warning",
                    "No columns selected. Keeping previous selection.",
                )
                return
            self.selected_columns = chosen
            self.settings.setValue(
                "selected_columns_json", json.dumps(self.selected_columns)
            )
            self.populate_stream_selectors()

    def populate_stream_selectors(self):
        selectors = [
            self.stream_selector_1,
            self.stream_selector_2,
            self.stream_selector_3,
        ]
        for sel in selectors:
            cur = sel.currentText()
            sel.blockSignals(True)
            sel.clear()
            sel.addItems(self.selected_columns)
            sel.blockSignals(False)

            if cur in self.selected_columns:
                sel.setCurrentText(cur)

        for idx, sel in enumerate(selectors, start=1):
            if sel.currentIndex() == -1 and self.selected_columns:
                col_idx = min(idx - 1, len(self.selected_columns) - 1)
                sel.setCurrentIndex(col_idx)
            self.update_plot_data(sel.currentText(), plot_idx=idx)

    # ================= Unified Export Tools =================
    def export_catalog_unified(self):
        videos = self.catalog_db.get_all_videos()
        if not videos:
            QMessageBox.information(
                self, "Export", "Catalog is empty. Nothing to export."
            )
            return

        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Video Catalog",
            "video_catalog_export.csv",
            "CSV Files (*.csv);;Text Files (*.txt)",
        )
        if not path:
            return

        is_csv = path.lower().endswith(".csv") or (
            "csv" in selected_filter.lower() and not path.lower().endswith(".txt")
        )
        if not (path.lower().endswith(".csv") or path.lower().endswith(".txt")):
            path += ".csv" if is_csv else ".txt"

        try:
            if is_csv:
                with open(
                    path, "w", newline="", encoding="utf-8"
                ) as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow([
                        "file_name",
                        "file_path",
                        "raw_start_epoch",
                        "start_epoch",
                        "end_epoch",
                        "duration_sec",
                        "haul",
                        "position",
                        "time_offset_s",
                        "description",
                        "comments",
                    ])
                    for v in videos:
                        writer.writerow([
                            v["file_name"],
                            v["file_path"],
                            v["raw_start_epoch"],
                            v["start_epoch"],
                            v["end_epoch"],
                            v["duration_sec"],
                            v["haul"],
                            v["position"],
                            v["time_offset"],
                            v["description"],
                            v["comments"],
                        ])
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"{APP_NAME} — Video Catalog Export\n" + "=" * 80 + "\n\n")
                    for v in videos:
                        f.write(f"File Name:    {v['file_name']}\n")
                        f.write(f"File Path:    {v['file_path']}\n")
                        f.write(
                            f"Haul / Pos:   {v['haul']} / {v['position']}\n"
                        )
                        f.write(f"Time Offset:  {v['time_offset']} s\n")
                        f.write(f"Duration:     {v['duration_sec']} s\n")
                        f.write(f"Description:  {v['description']}\n")
                        f.write(f"Comments:     {v['comments']}\n")
                        f.write("-" * 80 + "\n")

            QMessageBox.information(
                self, "Export Successful", f"Video catalog exported to:\n{path}"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Export Error", f"Failed to export catalog:\n{e}"
            )

    def export_comments_unified(self):
        comments = self.catalog_db.get_all_comments()
        if not comments:
            QMessageBox.information(
                self, "Export", "No timestamped comments found to export."
            )
            return

        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Timestamped Comments",
            "video_comments_export.csv",
            "CSV Files (*.csv);;Text Files (*.txt)",
        )
        if not path:
            return

        is_csv = path.lower().endswith(".csv") or (
            "csv" in selected_filter.lower() and not path.lower().endswith(".txt")
        )
        if not (path.lower().endswith(".csv") or path.lower().endswith(".txt")):
            path += ".csv" if is_csv else ".txt"

        try:
            if is_csv:
                with open(
                    path, "w", newline="", encoding="utf-8"
                ) as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow([
                        "file_name",
                        "file_path",
                        "video_pos_ms",
                        "epoch_sec",
                        "dt_display",
                        "comment",
                        "created_at",
                    ])
                    for c in comments:
                        writer.writerow([
                            c["file_name"],
                            c["file_path"],
                            c["video_pos_ms"],
                            c["epoch_sec"],
                            c["dt_display"],
                            c["comment"],
                            c["created_at"],
                        ])
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(
                        f"{APP_NAME} — Timestamped Comments Export\n"
                        + "=" * 80
                        + "\n\n"
                    )
                    for c in comments:
                        f.write(
                            f"[{c['file_name']}] @ {c['dt_display']} (Pos: {c['video_pos_ms']} ms)\n"
                        )
                        f.write(f"Comment:   {c['comment']}\n")
                        f.write(f"Logged At: {c['created_at']}\n")
                        f.write("-" * 80 + "\n")

            QMessageBox.information(
                self, "Export Successful", f"Comments exported to:\n{path}"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Export Error", f"Failed to export comments:\n{e}"
            )

    # ================= Plot Hover Tooltip Feature =================
    def on_plot_hover(self, pos, plot_idx: int):
        plot_map = {1: self.plot_1, 2: self.plot_2, 3: self.plot_3}
        selector_map = {
            1: self.stream_selector_1,
            2: self.stream_selector_2,
            3: self.stream_selector_3,
        }

        plot_widget = plot_map[plot_idx]
        vb = plot_widget.plotItem.vb

        if not vb.sceneBoundingRect().contains(pos):
            return

        mouse_point = vb.mapSceneToView(pos)
        epoch = mouse_point.x()

        tz_name = self.tz_selector.currentText().split()[0]
        dt_str = datetime.fromtimestamp(epoch, tz=self.tz).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        col_name = selector_map[plot_idx].currentText()

        tooltip_lines = [f"Time: {dt_str} {tz_name}"]

        if self.df is not None and col_name in self.df:
            stream_df = self.df.dropna(subset=["epoch_sec", col_name])
            if not stream_df.empty:
                timestamps = stream_df["epoch_sec"].to_numpy()
                idx = np.searchsorted(timestamps, epoch)
                idx = np.clip(idx, 0, len(timestamps) - 1)
                val = stream_df[col_name].iloc[idx]
                pt_time = datetime.fromtimestamp(
                    timestamps[idx], tz=self.tz
                ).strftime("%H:%M:%S")
                tooltip_lines.append(f"{col_name}: {val:.2f} (at {pt_time})")

        QToolTip.showText(QCursor.pos(), "\n".join(tooltip_lines), plot_widget)

    # ================= Date/Time Video Finder Dialog =================
    def open_find_video_dialog(self):
        dialog = FindVideoByDateDialog(self.catalog_db, self.tz, self)
        if self.video_start_epoch is not None:
            current_epoch = self.video_start_epoch + (
                self.player.position() / 1000.0
            )
            dt_local = datetime.fromtimestamp(current_epoch, tz=self.tz)
            dialog.dt_picker.setDateTime(
                QDateTime(
                    dt_local.year,
                    dt_local.month,
                    dt_local.day,
                    dt_local.hour,
                    dt_local.minute,
                    dt_local.second,
                )
            )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_path = dialog.selected_file_path
            if selected_path and os.path.exists(selected_path):
                self.load_video_from_path(selected_path)
            elif selected_path:
                QMessageBox.warning(
                    self,
                    "File Not Found",
                    f"Video file could not be found at path:\n{selected_path}",
                )

    # ================= Catalog DB Switching =================
    def open_existing_catalog_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Existing Video Catalog DB",
            "",
            "SQLite DB (*.db *.sqlite *.sqlite3)",
        )
        if not path:
            return

        try:
            self.catalog_db = VideoCatalogDB(path)
            self.settings.setValue("catalog_db_path", path)
            self.refresh_catalog_list()
            QMessageBox.information(
                self, "Catalog Loaded", f"Successfully opened catalog:\n{path}"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to open catalog database:\n{e}"
            )

    # ================= Screen Capture Tool =================
    def capture_video_frame(self):
        if (
            self.player.playbackState()
            != QMediaPlayer.PlaybackState.PausedState
        ):
            QMessageBox.warning(
                self,
                "Paused Video Required",
                "Screen capture is only permitted when the video is paused.\nPlease pause playback first.",
            )
            return

        pos_ms = self.player.position()
        video_name = (
            os.path.splitext(os.path.basename(self.current_video_path))[0]
            if self.current_video_path
            else "capture"
        )
        time_str = (
            QTime(0, 0, 0).addMSecs(pos_ms).toString("hh_mm_ss_zzz")
        )
        default_filename = f"{video_name}_{time_str}.png"

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Screen Capture",
            default_filename,
            "PNG Image (*.png)",
        )
        if not save_path:
            return

        success = False
        if self.video_cap is not None and self.video_cap.isOpened():
            self.video_cap.set(cv2.CAP_PROP_POS_MSEC, pos_ms)
            ret, frame = self.video_cap.read()
            if ret and frame is not None:
                cv2.imwrite(save_path, frame)
                success = True

        if not success:
            pixmap = self.video_widget.grab()
            pixmap.save(save_path, "PNG")

        QMessageBox.information(
            self, "Frame Saved", f"Frame snapshot successfully saved to:\n{save_path}"
        )

    # ================= Timestamped Comments =================
    def add_comment_at_playback(self):
        text = self.txt_new_comment.text().strip()
        if not text:
            return
        if not self.current_video_path or self.video_start_epoch is None:
            QMessageBox.warning(
                self, "Video Required", "Please load a video first."
            )
            return

        pos_ms = self.player.position()
        epoch_sec = self.video_start_epoch + (pos_ms / 1000.0)

        dt_display = datetime.fromtimestamp(epoch_sec, tz=self.tz).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        file_name = os.path.basename(self.current_video_path)

        self.catalog_db.add_timestamped_comment(
            self.current_video_path,
            file_name,
            pos_ms,
            epoch_sec,
            f"{dt_display} {self.tz_selector.currentText().split()[0]}",
            text,
        )

        self.txt_new_comment.clear()
        self.load_comments_for_current_video()

    def load_comments_for_current_video(self):
        self.table_comments.setRowCount(0)
        if not self.current_video_path:
            return

        comments = self.catalog_db.get_comments_for_video(
            self.current_video_path
        )
        self.table_comments.setRowCount(len(comments))

        for row, c in enumerate(comments):
            elapsed_str = (
                QTime(0, 0, 0).addMSecs(c["video_pos_ms"]).toString("hh:mm:ss")
            )
            item_elapsed = QTableWidgetItem(elapsed_str)
            item_elapsed.setData(Qt.ItemDataRole.UserRole, c["video_pos_ms"])

            item_dt = QTableWidgetItem(c["dt_display"])
            item_dt.setData(Qt.ItemDataRole.UserRole, c["video_pos_ms"])

            item_text = QTableWidgetItem(c["comment"])
            item_text.setData(Qt.ItemDataRole.UserRole, c["video_pos_ms"])

            btn_del = QPushButton("X")
            btn_del.setFixedSize(24, 20)
            btn_del.setStyleSheet("color: red; font-weight: bold;")
            comment_id = c["id"]
            btn_del.clicked.connect(
                lambda _, cid=comment_id: self.delete_comment_item(cid)
            )

            self.table_comments.setItem(row, 0, item_elapsed)
            self.table_comments.setItem(row, 1, item_dt)
            self.table_comments.setItem(row, 2, item_text)
            self.table_comments.setCellWidget(row, 3, btn_del)

    def on_comment_double_clicked(self, row, column):
        item = self.table_comments.item(row, 0)
        if item is not None:
            target_ms = item.data(Qt.ItemDataRole.UserRole)
            if target_ms is not None:
                self.player.setPosition(int(target_ms))

    def delete_comment_item(self, comment_id: int):
        self.catalog_db.delete_comment(comment_id)
        self.load_comments_for_current_video()

    # ================= Catalog Scanning & Management =================
    def scan_directory(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Directory to Scan for .MOV Files"
        )
        if not dir_path:
            return

        mov_files = []
        for root, _, files in os.walk(dir_path):
            for f in files:
                if f.lower().endswith(".mov"):
                    mov_files.append(os.path.join(root, f))

        if not mov_files:
            QMessageBox.information(
                self, "Scan Complete", "No .MOV files found in chosen folder."
            )
            return

        progress = QProgressDialog(
            "Scanning .MOV files...", "Cancel", 0, len(mov_files), self
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)

        for i, fpath in enumerate(mov_files):
            if progress.wasCanceled():
                break
            fname = os.path.basename(fpath)
            progress.setValue(i)
            progress.setLabelText(f"Scanning ({i+1}/{len(mov_files)}): {fname}")
            QApplication.processEvents()

            raw_start_epoch, duration_sec, _ = extract_video_info(
                fpath, self.tz_offset_hours
            )
            self.catalog_db.upsert_scanned_video(
                fname, fpath, raw_start_epoch, duration_sec
            )

        progress.setValue(len(mov_files))
        self.refresh_catalog_list()
        QMessageBox.information(
            self,
            "Scan Complete",
            f"Successfully scanned {len(mov_files)} files into catalog database.",
        )

    def refresh_catalog_list(self):
        self.list_videos.clear()
        videos = self.catalog_db.get_all_videos()
        filter_text = self.txt_filter.text().strip().lower()

        for vid in videos:
            name = vid["file_name"]
            haul = vid["haul"] or ""
            pos = vid["position"] or ""
            desc = vid["description"] or ""

            combined_search = f"{name} {haul} {pos} {desc}".lower()
            if filter_text and (filter_text not in combined_search):
                continue

            dt_start = datetime.fromtimestamp(
                vid["start_epoch"], tz=self.tz
            ).strftime("%m/%d %H:%M:%S")
            dur_str = str(timedelta(seconds=int(vid["duration_sec"] or 0)))

            header_str = name
            if haul or pos:
                header_str += f" [{haul} | {pos}]".strip()

            item_text = (
                f"{header_str}\n  Start: {dt_start} (Offset: {vid['time_offset']:+.1f}s) | "
                f"Dur: {dur_str}"
            )
            if desc:
                item_text += f"\n  Desc: {desc}"

            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, vid["file_path"])
            self.list_videos.addItem(item)

    def filter_catalog_list(self):
        self.refresh_catalog_list()

    def on_catalog_item_selected(self):
        items = self.list_videos.selectedItems()
        if not items:
            return
        file_path = items[0].data(Qt.ItemDataRole.UserRole)
        rec = self.catalog_db.get_video(file_path)
        if rec:
            self.lbl_cat_file.setText(f"<b>File:</b> {rec['file_name']}")
            self.lbl_cat_file.setToolTip(rec["file_path"])

            self.txt_haul.setText(rec["haul"] or "")
            self.txt_position.setText(rec["position"] or "")

            offset_val = rec["time_offset"] or 0.0
            self.spin_time_offset.blockSignals(True)
            self.spin_time_offset.setValue(offset_val)
            self.spin_time_offset.blockSignals(False)

            self.txt_description.setText(rec["description"] or "")
            self.txt_comments.setText(rec["comments"] or "")

            if os.path.exists(file_path):
                self.load_video_from_path(file_path, stored_offset=offset_val)

    def save_video_details(self):
        items = self.list_videos.selectedItems()
        if not items:
            QMessageBox.warning(
                self, "Warning", "No video selected from the catalog."
            )
            return
        file_path = items[0].data(Qt.ItemDataRole.UserRole)
        haul = self.txt_haul.text().strip()
        pos = self.txt_position.text().strip()
        time_offset = self.spin_time_offset.value()
        desc = self.txt_description.text().strip()
        comments = self.txt_comments.toPlainText().strip()

        duration_sec = (
            (self.video_duration_ms / 1000.0)
            if self.video_duration_ms > 0
            else 0.0
        )
        effective_start = (
            self.video_start_epoch
            if self.video_start_epoch is not None
            else 0.0
        )

        self.catalog_db.update_video_details(
            file_path,
            haul,
            pos,
            time_offset,
            desc,
            comments,
            effective_start,
            duration_sec,
        )
        self.refresh_catalog_list()
        self.update_timing_labels()
        QMessageBox.information(
            self, "Saved", "Video details and time offset updated in database."
        )

    # ================= Offset & Timing Synchronization =================
    def on_offset_spinbox_changed(self, offset_val: float):
        if self.video_raw_epoch is None:
            return

        self.video_start_epoch = self.video_raw_epoch + offset_val
        self.update_video_start_display()
        self.lock_x_axes_to_video()
        self.on_position_changed(self.player.position())
        self.update_timing_labels()

    def on_start_datetime_changed(self, qdt: QDateTime):
        if self.video_raw_epoch is None:
            return

        py_dt = datetime(
            qdt.date().year(),
            qdt.date().month(),
            qdt.date().day(),
            qdt.time().hour(),
            qdt.time().minute(),
            qdt.time().second(),
            tzinfo=self.tz,
        )
        new_start_epoch = py_dt.timestamp()
        self.video_start_epoch = new_start_epoch

        offset_val = new_start_epoch - self.video_raw_epoch
        self.spin_time_offset.blockSignals(True)
        self.spin_time_offset.setValue(offset_val)
        self.spin_time_offset.blockSignals(False)

        self.lock_x_axes_to_video()
        self.on_position_changed(self.player.position())
        self.update_timing_labels()

    def update_timing_labels(self):
        if self.video_raw_epoch is None or self.video_start_epoch is None:
            return
        tz_label = self.tz_selector.currentText().split()[0]
        dt_raw = datetime.fromtimestamp(
            self.video_raw_epoch, tz=self.tz
        ).strftime("%Y-%m-%d %H:%M:%S")
        dt_eff = datetime.fromtimestamp(
            self.video_start_epoch, tz=self.tz
        ).strftime("%Y-%m-%d %H:%M:%S")

        end_epoch = self.video_start_epoch + (self.video_duration_ms / 1000.0)
        dt_end = datetime.fromtimestamp(end_epoch, tz=self.tz).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        dur_str = str(timedelta(seconds=int(self.video_duration_ms / 1000.0)))

        self.lbl_cat_timing.setText(
            f"Raw Start: {dt_raw} {tz_label}\n"
            f"Start:     {dt_eff} {tz_label}\n"
            f"End:       {dt_end} {tz_label}\n"
            f"Duration:  {dur_str}"
        )

    # ================= Video Playback Logic =================
    def load_video_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open MOV Video", "", "Video Files (*.mov *.mp4)"
        )
        if path:
            self.load_video_from_path(path)

    def load_video_from_path(self, path: str, stored_offset: float = None):
        self.current_video_path = path

        if cv2 is not None:
            if self.video_cap is not None:
                self.video_cap.release()
            self.video_cap = cv2.VideoCapture(self.current_video_path)

        (
            self.video_raw_epoch,
            duration_sec,
            self.base_timecode,
        ) = extract_video_info(path, self.tz_offset_hours)

        if stored_offset is None:
            rec = self.catalog_db.get_video(path)
            stored_offset = (
                rec["time_offset"]
                if (rec and rec["time_offset"] is not None)
                else 0.0
            )

        self.spin_time_offset.blockSignals(True)
        self.spin_time_offset.setValue(stored_offset)
        self.spin_time_offset.blockSignals(False)

        self.video_start_epoch = self.video_raw_epoch + stored_offset

        self.player.setSource(QUrl.fromLocalFile(path))
        self.btn_play.setEnabled(True)

        self.update_video_start_display()
        self.time_line_1.setValue(self.video_start_epoch)
        self.time_line_2.setValue(self.video_start_epoch)
        self.time_line_3.setValue(self.video_start_epoch)
        self.lock_x_axes_to_video()
        self.update_timing_labels()
        self.load_comments_for_current_video()

    def update_video_start_display(self):
        if self.video_start_epoch is None:
            return
        dt_local = datetime.fromtimestamp(self.video_start_epoch, tz=self.tz)
        qdt = QDateTime(
            dt_local.year,
            dt_local.month,
            dt_local.day,
            dt_local.hour,
            dt_local.minute,
            dt_local.second,
        )
        self.dt_video_start.blockSignals(True)
        self.dt_video_start.setDateTime(qdt)
        self.dt_video_start.blockSignals(False)

    def on_duration_changed(self, duration_ms):
        self.video_duration_ms = duration_ms
        self.slider.setRange(0, duration_ms)
        self.lock_x_axes_to_video()
        self.update_timing_labels()

    def on_speed_changed(self, text: str):
        speed_factor = float(text.replace("x", ""))
        self.player.setPlaybackRate(speed_factor)

    def on_timezone_changed(self):
        self.tz_offset_hours = self.tz_selector.currentData()
        tz_name = self.tz_selector.currentText().split()[0]
        self.tz = timezone(timedelta(hours=self.tz_offset_hours), name=tz_name)

        self.lbl_start_label.setText(f"Video Start ({tz_name}):")
        for axis, plot in [
            (self.date_axis_1, self.plot_1),
            (self.date_axis_2, self.plot_2),
            (self.date_axis_3, self.plot_3),
        ]:
            axis.tz = self.tz
            plot.setLabel("bottom", f"Time ({tz_name})")
            plot.getAxis("bottom").picture = None
            plot.update()

        self.update_video_start_display()
        self.refresh_catalog_list()
        self.update_timing_labels()
        self.load_comments_for_current_video()
        self.on_position_changed(self.player.position())

    def get_thumbnail_preview(self, pos_ms: int) -> tuple[QPixmap, str]:
        time_display = (
            QTime(0, 0, 0).addMSecs(pos_ms).toString("hh:mm:ss")
        )
        if self.video_start_epoch is not None:
            current_epoch = self.video_start_epoch + (pos_ms / 1000.0)
            dt_local = datetime.fromtimestamp(current_epoch, tz=self.tz)
            time_str = f"{time_display} | {dt_local.strftime('%H:%M:%S')}"
        else:
            time_str = time_display

        pixmap = None
        if self.video_cap is not None and self.video_cap.isOpened():
            self.video_cap.set(cv2.CAP_PROP_POS_MSEC, pos_ms)
            ret, frame = self.video_cap.read()
            if ret:
                frame_rgb = cv2.cvtColor(
                    cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2RGB
                )
                h, w, ch = frame_rgb.shape
                qimg = QImage(
                    frame_rgb.data, w, h, ch * w, QImage.Format.Format_RGB888
                )
                pixmap = QPixmap.fromImage(qimg)

        return pixmap, time_str

    def lock_x_axes_to_video(self):
        if self.video_start_epoch is not None and self.video_duration_ms > 0:
            start_x = self.video_start_epoch
            end_x = start_x + (self.video_duration_ms / 1000.0)
            self.plot_1.setXRange(start_x, end_x, padding=0.0)

    def fit_all_data(self):
        self.plot_1.enableAutoRange()
        self.plot_2.enableAutoRange()
        self.plot_3.enableAutoRange()

    # ================= SQLite Telemetry Loading =================
    def load_sqlite_db_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open SQLite Database",
            "",
            "SQLite DB (*.db *.sqlite *.sqlite3)",
        )
        if path:
            self.load_sqlite_db_from_path(
                path, silent=False, prompt_columns=True
            )

    def load_sqlite_db_from_path(
        self, path: str, silent: bool = False, prompt_columns: bool = True
    ):
        try:
            conn = sqlite3.connect(path)
            table_info = pd.read_sql_query(
                "PRAGMA table_info(trawl_data);", conn
            )
            if table_info.empty:
                raise ValueError("Table 'trawl_data' not found in database.")

            existing_cols = table_info["name"].tolist()
            if "DT" not in existing_cols:
                raise ValueError(
                    "Required column 'DT' not found in 'trawl_data'."
                )

            df_sample = pd.read_sql_query(
                "SELECT * FROM trawl_data LIMIT 5;", conn
            )
            numeric_cols = [
                col
                for col in df_sample.select_dtypes(include=[np.number]).columns
                if col.upper() not in ["DT", "ID", "INDEX"]
            ]

            if not numeric_cols:
                numeric_cols = [c for c in existing_cols if c.upper() != "DT"]

            self.all_db_columns = numeric_cols

            cols_to_query = ["DT"] + self.all_db_columns
            query = f"SELECT {', '.join(cols_to_query)} FROM trawl_data ORDER BY DT ASC"
            self.df = pd.read_sql_query(query, conn)
            conn.close()

            self.df["epoch_sec"] = pd.to_numeric(
                self.df["DT"], errors="coerce"
            )
            self.df = self.df.dropna(subset=["epoch_sec"]).sort_values(
                "epoch_sec"
            )

            if not self.df.empty and self.df["epoch_sec"].median() > 1e11:
                self.df["epoch_sec"] = self.df["epoch_sec"] / 1000.0

            self.settings.setValue("telemetry_db_path", path)

            total_rows = len(self.df)
            if total_rows > 0:
                min_dt = datetime.fromtimestamp(
                    self.df["epoch_sec"].min(), tz=self.tz
                ).strftime("%Y-%m-%d %H:%M:%S")
                max_dt = datetime.fromtimestamp(
                    self.df["epoch_sec"].max(), tz=self.tz
                ).strftime("%Y-%m-%d %H:%M:%S")
                self.lbl_diagnostics.setText(
                    f"DB: {total_rows} rows | {min_dt} to {max_dt}"
                )
            else:
                self.lbl_diagnostics.setText("DB: Table is empty.")

            saved_cols_json = self.settings.value("selected_columns_json", "")
            if saved_cols_json:
                try:
                    loaded = json.loads(saved_cols_json)
                    self.selected_columns = [
                        c for c in loaded if c in self.all_db_columns
                    ]
                except Exception:
                    self.selected_columns = []

            if prompt_columns or not self.selected_columns:
                self.prompt_column_selector()
            else:
                self.populate_stream_selectors()

            if self.video_start_epoch is None:
                self.fit_all_data()

            if not silent:
                QMessageBox.information(
                    self,
                    "Database Loaded",
                    f"Telemetry loaded successfully ({total_rows} rows).",
                )

        except Exception as e:
            if not silent:
                QMessageBox.critical(
                    self, "Database Error", f"Failed to load SQLite data:\n{e}"
                )

    # ================= 3-Plot Synchronization =================
    def update_plot_data(self, column_name: str, plot_idx: int):
        if self.df is None or not column_name or column_name not in self.df:
            return

        curve_map = {1: self.curve_1, 2: self.curve_2, 3: self.curve_3}
        plot_map = {1: self.plot_1, 2: self.plot_2, 3: self.plot_3}

        curve = curve_map[plot_idx]
        plot_widget = plot_map[plot_idx]

        stream_df = self.df.dropna(subset=["epoch_sec", column_name])
        if stream_df.empty:
            curve.clear()
            return

        x = stream_df["epoch_sec"].to_numpy()
        y = stream_df[column_name].to_numpy()

        curve.setData(x, y)
        plot_widget.setLabel("left", column_name)
        plot_widget.enableAutoRange(axis=pg.ViewBox.YAxis)

        if self.video_start_epoch is not None and self.video_duration_ms > 0:
            self.lock_x_axes_to_video()

    def on_plot_clicked(self, event, target_plot):
        if event.button() == Qt.MouseButton.LeftButton:
            vb = target_plot.plotItem.vb
            pos = event.scenePos()

            if vb.sceneBoundingRect().contains(pos):
                mouse_point = vb.mapSceneToView(pos)
                clicked_epoch = mouse_point.x()

                if (
                    self.video_start_epoch is not None
                    and self.video_duration_ms > 0
                ):
                    offset_sec = clicked_epoch - self.video_start_epoch
                    target_ms = int(offset_sec * 1000.0)
                    target_ms = max(0, min(target_ms, self.video_duration_ms))
                    self.player.setPosition(target_ms)

    def on_position_changed(self, position_ms):
        if not self.slider.isSliderDown():
            self.slider.setValue(position_ms)

        time_display = (
            QTime(0, 0, 0).addMSecs(position_ms).toString("hh:mm:ss")
        )

        if self.video_start_epoch is not None:
            current_epoch = self.video_start_epoch + (position_ms / 1000.0)

            self.time_line_1.setValue(current_epoch)
            self.time_line_2.setValue(current_epoch)
            self.time_line_3.setValue(current_epoch)

            tz_name = self.tz_selector.currentText().split()[0]
            current_dt_str = datetime.fromtimestamp(
                current_epoch, tz=self.tz
            ).strftime(f"%Y-%m-%d %H:%M:%S {tz_name}")
            self.lbl_time_info.setText(
                f"Elapsed: {time_display} | Video DT: {current_dt_str}"
            )

            self._update_readout(
                self.stream_selector_1.currentText(),
                current_epoch,
                self.lbl_stream_value_1,
            )
            self._update_readout(
                self.stream_selector_2.currentText(),
                current_epoch,
                self.lbl_stream_value_2,
            )
            self._update_readout(
                self.stream_selector_3.currentText(),
                current_epoch,
                self.lbl_stream_value_3,
            )
        else:
            self.lbl_time_info.setText(f"Elapsed: {time_display}")

    def _update_readout(
        self, column_name: str, current_epoch: float, label: QLabel
    ):
        if self.df is not None and column_name in self.df:
            stream_df = self.df.dropna(subset=["epoch_sec", column_name])
            if not stream_df.empty:
                timestamps = stream_df["epoch_sec"].to_numpy()
                idx = np.searchsorted(timestamps, current_epoch)
                idx = np.clip(idx, 0, len(timestamps) - 1)
                val = stream_df[column_name].iloc[idx]
                label.setText(f"Value: {val:.2f}")
                return
        label.setText("Value: --")

    def set_position(self, position_ms):
        self.player.setPosition(position_ms)

    def toggle_play(self):
        if (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        ):
            self.player.pause()
            self.btn_play.setText("Play")
        else:
            self.player.play()
            self.btn_play.setText("Pause")

    def closeEvent(self, event):
        if self.video_cap is not None:
            self.video_cap.release()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    viewer = TrawlSyncViewer()
    viewer.show()
    sys.exit(app.exec())