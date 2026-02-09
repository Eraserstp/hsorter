"""GUI-приложение для ведения библиотеки фильмов/сериалов."""

import datetime
import json
import os
import sqlite3
import subprocess
import shutil
import colorsys
import hashlib
import html

import gi
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Требуем конкретные версии GTK/GDK для корректной работы
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk


# Базовый набор статусов качества, который может быть расширен позже.
STATUS_OPTIONS = [
    "полный",
    "отсутствует",
    "хардсаб",
    "проблемы перевода",
    "частично отсутствует",
    "водяной знак",
    "проблема озвучки",
    "дефекты видео",
    "известна лучшая версия",
    "импортировано",
]


# Обёртка над SQLite для хранения карточек тайтлов и медиафайлов.
class Database:
    """Обёртка над SQLite с операциями для UI и импорта."""
    # Инициализируем соединение и создаём таблицы, если их ещё нет.
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    # Создание схемы БД.
    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS titles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                main_title TEXT NOT NULL,
                alt_titles TEXT DEFAULT "",
                rating INTEGER,
                personal_rating INTEGER,
                censored INTEGER DEFAULT 0,
                year_start INTEGER,
                year_end INTEGER,
                episodes INTEGER DEFAULT 0,
                total_duration TEXT DEFAULT "",
                description TEXT DEFAULT "",
                country TEXT DEFAULT "",
                production TEXT DEFAULT "",
                director TEXT DEFAULT "",
                character_designer TEXT DEFAULT "",
                author TEXT DEFAULT "",
                composer TEXT DEFAULT "",
                subtitles_author TEXT DEFAULT "",
                voice_author TEXT DEFAULT "",
                title_comment TEXT DEFAULT "",
                url TEXT DEFAULT "",
                created_at TEXT DEFAULT "",
                updated_at TEXT DEFAULT "",
                status_json TEXT DEFAULT "{}",
                tags TEXT DEFAULT "",
                cover_path TEXT DEFAULT ""
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                path TEXT NOT NULL,
                info TEXT DEFAULT "",
                sort_order INTEGER DEFAULT 0,
                thumbnail_path TEXT DEFAULT "",
                comment TEXT DEFAULT "",
                FOREIGN KEY(title_id) REFERENCES titles(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS video_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS video_track_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL,
                track_type TEXT NOT NULL,
                track_index INTEGER NOT NULL,
                language TEXT DEFAULT "",
                hardsub INTEGER DEFAULT 0,
                hardsub_language TEXT DEFAULT "",
                FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE
            )
            """
        )
        if not self._column_exists("media", "sort_order"):
            cur.execute("ALTER TABLE media ADD COLUMN sort_order INTEGER DEFAULT 0")
        if not self._column_exists("media", "thumbnail_path"):
            cur.execute("ALTER TABLE media ADD COLUMN thumbnail_path TEXT DEFAULT ''")
        if not self._column_exists("media", "comment"):
            cur.execute("ALTER TABLE media ADD COLUMN comment TEXT DEFAULT ''")
        if not self._column_exists("titles", "title_comment"):
            cur.execute("ALTER TABLE titles ADD COLUMN title_comment TEXT DEFAULT ''")
        if not self._column_exists("titles", "url"):
            cur.execute("ALTER TABLE titles ADD COLUMN url TEXT DEFAULT ''")
        if not self._column_exists("titles", "created_at"):
            cur.execute("ALTER TABLE titles ADD COLUMN created_at TEXT DEFAULT ''")
        if not self._column_exists("titles", "updated_at"):
            cur.execute("ALTER TABLE titles ADD COLUMN updated_at TEXT DEFAULT ''")
        self.conn.commit()

    # Проверяем наличие колонки в таблице.
    def _column_exists(self, table: str, column: str) -> bool:
        cur = self.conn.cursor()
        columns = cur.execute(f"PRAGMA table_info({table})").fetchall()
        return any(col["name"] == column for col in columns)

    # Получение списка тайтлов с фильтрами по названию, тегам и статусам.
    def list_titles(
        self,
        query: str = "",
        tags: str = "",
        status_filter: str = "",
        sort_by: str = "title",
    ):
        cur = self.conn.cursor()
        sql = "SELECT * FROM titles"
        params = []
        clauses = []
        if query:
            clauses.append("main_title LIKE ?")
            params.append(f"%{query}%")
        if tags:
            clauses.append("tags LIKE ?")
            params.append(f"%{tags}%")
        if status_filter:
            clauses.append("status_json LIKE ?")
            params.append(f"%{status_filter}%")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if sort_by == "created_at":
            sql += " ORDER BY created_at DESC, main_title COLLATE NOCASE"
        else:
            sql += " ORDER BY main_title COLLATE NOCASE"
        return cur.execute(sql, params).fetchall()

    # Добавление нового тайтла и возврат его идентификатора.
    def add_title(self, data: dict) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO titles (
                main_title, alt_titles, rating, personal_rating, censored,
                year_start, year_end, episodes, total_duration, description,
                country, production, director, character_designer, author,
                composer, subtitles_author, voice_author, title_comment,
                url, created_at, updated_at,
                status_json, tags, cover_path
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data.get("main_title", ""),
                data.get("alt_titles", ""),
                data.get("rating"),
                data.get("personal_rating"),
                1 if data.get("censored") else 0,
                data.get("year_start"),
                data.get("year_end"),
                data.get("episodes", 0),
                data.get("total_duration", ""),
                data.get("description", ""),
                data.get("country", ""),
                data.get("production", ""),
                data.get("director", ""),
                data.get("character_designer", ""),
                data.get("author", ""),
                data.get("composer", ""),
                data.get("subtitles_author", ""),
                data.get("voice_author", ""),
                data.get("title_comment", ""),
                data.get("url", ""),
                data.get("created_at", ""),
                data.get("updated_at", ""),
                json.dumps(data.get("status", {}), ensure_ascii=False),
                data.get("tags", ""),
                data.get("cover_path", ""),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    # Обновление существующего тайтла.
    def update_title(self, title_id: int, data: dict) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE titles SET
                main_title=?,
                alt_titles=?,
                rating=?,
                personal_rating=?,
                censored=?,
                year_start=?,
                year_end=?,
                episodes=?,
                total_duration=?,
                description=?,
                country=?,
                production=?,
                director=?,
                character_designer=?,
                author=?,
                composer=?,
                subtitles_author=?,
                voice_author=?,
                title_comment=?,
                url=?,
                created_at=?,
                updated_at=?,
                status_json=?,
                tags=?,
                cover_path=?
            WHERE id=?
            """,
            (
                data.get("main_title", ""),
                data.get("alt_titles", ""),
                data.get("rating"),
                data.get("personal_rating"),
                1 if data.get("censored") else 0,
                data.get("year_start"),
                data.get("year_end"),
                data.get("episodes", 0),
                data.get("total_duration", ""),
                data.get("description", ""),
                data.get("country", ""),
                data.get("production", ""),
                data.get("director", ""),
                data.get("character_designer", ""),
                data.get("author", ""),
                data.get("composer", ""),
                data.get("subtitles_author", ""),
                data.get("voice_author", ""),
                data.get("title_comment", ""),
                data.get("url", ""),
                data.get("created_at", ""),
                data.get("updated_at", ""),
                json.dumps(data.get("status", {}), ensure_ascii=False),
                data.get("tags", ""),
                data.get("cover_path", ""),
                title_id,
            ),
        )
        self.conn.commit()

    # Обновление обложки тайтла.
    def update_title_cover(self, title_id: int, cover_path: str) -> None:
        cur = self.conn.cursor()
        cur.execute("UPDATE titles SET cover_path=? WHERE id=?", (cover_path, title_id))
        self.conn.commit()

    # Получение карточки тайтла по id.
    def get_title(self, title_id: int):
        cur = self.conn.cursor()
        return cur.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()

    # Удаление тайтла.
    def delete_title(self, title_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM titles WHERE id=?", (title_id,))
        self.conn.commit()

    # Проверка наличия тайтла с таким названием (исключая текущий id при редактировании).
    def title_exists(self, name: str, exclude_id: int | None = None) -> bool:
        cur = self.conn.cursor()
        if exclude_id:
            row = cur.execute(
                "SELECT 1 FROM titles WHERE main_title=? AND id<>? LIMIT 1",
                (name, exclude_id),
            ).fetchone()
        else:
            row = cur.execute(
                "SELECT 1 FROM titles WHERE main_title=? LIMIT 1",
                (name,),
            ).fetchone()
        return row is not None

    # Получить значение настройки по ключу.
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Читает значение настройки по ключу."""
        cur = self.conn.cursor()
        row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row:
            return row["value"]
        return default

    # Сохранить значение настройки по ключу.
    def set_setting(self, key: str, value: str) -> None:
        """Записывает значение настройки по ключу."""
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    # Список медиафайлов по типу (image/video).
    def list_media(self, title_id: int, media_type: str):
        cur = self.conn.cursor()
        return cur.execute(
            "SELECT * FROM media WHERE title_id=? AND media_type=? ORDER BY sort_order, id",
            (title_id, media_type),
        ).fetchall()

    # Добавление медиафайла к тайтлу.
    def add_media(self, title_id: int, media_type: str, path: str, info: str) -> None:
        cur = self.conn.cursor()
        sort_order = self._next_media_order(title_id, media_type)
        cur.execute(
            "INSERT INTO media (title_id, media_type, path, info, sort_order) VALUES (?,?,?,?,?)",
            (title_id, media_type, path, info, sort_order),
        )
        self.conn.commit()

    # Обновление миниатюры и комментария для видео.
    def update_media_details(self, media_id: int, thumbnail_path: str, comment: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE media SET thumbnail_path=?, comment=? WHERE id=?",
            (thumbnail_path, comment, media_id),
        )
        self.conn.commit()

    # Обновление только миниатюры видео.
    def update_media_thumbnail(self, media_id: int, thumbnail_path: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE media SET thumbnail_path=? WHERE id=?",
            (thumbnail_path, media_id),
        )
        self.conn.commit()

    # Обновление описания медиафайла.
    def update_media_info(self, media_id: int, info: str) -> None:
        cur = self.conn.cursor()
        cur.execute("UPDATE media SET info=? WHERE id=?", (info, media_id))
        self.conn.commit()

    # Обновление пути к медиафайлу.
    def update_media_path(self, media_id: int, path: str) -> None:
        cur = self.conn.cursor()
        cur.execute("UPDATE media SET path=? WHERE id=?", (path, media_id))
        self.conn.commit()

    # Удаление медиафайла по id.
    def delete_media(self, media_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM media WHERE id=?", (media_id,))
        self.conn.commit()

    # Обновление порядка медиафайлов.
    def update_media_order(self, media_ids: list[int]) -> None:
        cur = self.conn.cursor()
        for order, media_id in enumerate(media_ids):
            cur.execute("UPDATE media SET sort_order=? WHERE id=?", (order, media_id))
        self.conn.commit()

    # Следующий номер сортировки для нового медиа.
    def _next_media_order(self, title_id: int, media_type: str) -> int:
        cur = self.conn.cursor()
        row = cur.execute(
            "SELECT MAX(sort_order) AS max_order FROM media WHERE title_id=? AND media_type=?",
            (title_id, media_type),
        ).fetchone()
        return (row["max_order"] or 0) + 1

    # Список изображений, привязанных к видеофайлу.
    def list_video_images(self, media_id: int):
        cur = self.conn.cursor()
        return cur.execute(
            "SELECT * FROM video_images WHERE media_id=? ORDER BY sort_order, id",
            (media_id,),
        ).fetchall()

    # Добавить изображение к видеофайлу.
    def add_video_image(self, media_id: int, path: str) -> None:
        cur = self.conn.cursor()
        sort_order = self._next_video_image_order(media_id)
        cur.execute(
            "INSERT INTO video_images (media_id, path, sort_order) VALUES (?,?,?)",
            (media_id, path, sort_order),
        )
        self.conn.commit()

    # Обновить путь изображения видеофайла.
    def update_video_image_path(self, image_id: int, path: str) -> None:
        cur = self.conn.cursor()
        cur.execute("UPDATE video_images SET path=? WHERE id=?", (path, image_id))
        self.conn.commit()

    # Удалить изображение видеофайла.
    def delete_video_image(self, image_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM video_images WHERE id=?", (image_id,))
        self.conn.commit()

    # Обновить порядок изображений видеофайла.
    def update_video_image_order(self, image_ids: list[int]) -> None:
        cur = self.conn.cursor()
        for order, image_id in enumerate(image_ids):
            cur.execute("UPDATE video_images SET sort_order=? WHERE id=?", (order, image_id))
        self.conn.commit()

    # Следующий порядок для изображения видео.
    def _next_video_image_order(self, media_id: int) -> int:
        cur = self.conn.cursor()
        row = cur.execute(
            "SELECT MAX(sort_order) AS max_order FROM video_images WHERE media_id=?",
            (media_id,),
        ).fetchone()
        return (row["max_order"] or 0) + 1

    # Получение/обновление ручных корректировок для дорожек.
    def list_track_overrides(self, media_id: int):
        cur = self.conn.cursor()
        return cur.execute(
            "SELECT * FROM video_track_overrides WHERE media_id=?",
            (media_id,),
        ).fetchall()

    def upsert_track_override(
        self,
        media_id: int,
        track_type: str,
        track_index: int,
        language: str,
        hardsub: bool,
        hardsub_language: str,
    ) -> None:
        cur = self.conn.cursor()
        row = cur.execute(
            """
            SELECT id FROM video_track_overrides
            WHERE media_id=? AND track_type=? AND track_index=?
            """,
            (media_id, track_type, track_index),
        ).fetchone()
        if row:
            cur.execute(
                """
                UPDATE video_track_overrides
                SET language=?, hardsub=?, hardsub_language=?
                WHERE id=?
                """,
                (language, 1 if hardsub else 0, hardsub_language, row["id"]),
            )
        else:
            cur.execute(
                """
                INSERT INTO video_track_overrides
                (media_id, track_type, track_index, language, hardsub, hardsub_language)
                VALUES (?,?,?,?,?,?)
                """,
                (media_id, track_type, track_index, language, 1 if hardsub else 0, hardsub_language),
            )
        self.conn.commit()


# Извлечение информации о видео через pymediainfo или CLI mediainfo.
class MediaInfo:
    """Получение информации о видео через mediainfo."""
    @staticmethod
    # Главная точка входа для описания видео.
    def describe_video(path: str) -> str:
        """Короткое описание (для списка видео)."""
        if os.path.isdir(path):
            return ""
        size_bytes = None
        if os.path.exists(path):
            try:
                size_bytes = os.path.getsize(path)
            except OSError:
                size_bytes = None
        info = MediaInfo._summary_from_pymediainfo(path)
        if not info:
            info = MediaInfo._summary_from_cli(path)
        if not info:
            return MediaInfo._format_size(size_bytes)
        return MediaInfo._format_summary(info, size_bytes)

    @staticmethod
    # Извлечение через библиотеку pymediainfo, если она установлена.
    def _summary_from_pymediainfo(path: str) -> dict:
        try:
            from pymediainfo import MediaInfo as PyMediaInfo
        except Exception:
            return {}
        try:
            media_info = PyMediaInfo.parse(path)
        except Exception:
            return {}
        summary = {"video_format": "", "width": "", "height": "", "audio_format": ""}
        for track in media_info.tracks:
            if track.track_type == "Video":
                summary["video_format"] = track.format or ""
                summary["width"] = track.width or ""
                summary["height"] = track.height or ""
            if track.track_type == "Audio":
                summary["audio_format"] = track.format or ""
                break
        return summary

    @staticmethod
    # Извлечение через CLI mediainfo (JSON).
    def _summary_from_cli(path: str) -> dict:
        try:
            result = subprocess.run(
                ["mediainfo", "--Output=JSON", path],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return {}
        if result.returncode != 0:
            return {}
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}
        tracks = data.get("media", {}).get("track", [])
        summary = {"video_format": "", "width": "", "height": "", "audio_format": ""}
        for track in tracks:
            track_type = track.get("@type")
            if track_type == "Video":
                summary["video_format"] = track.get("Format", "") or ""
                summary["width"] = track.get("Width", "") or ""
                summary["height"] = track.get("Height", "") or ""
            if track_type == "Audio":
                summary["audio_format"] = track.get("Format", "") or ""
                break
        return summary

    @staticmethod
    def _format_summary(summary: dict, size_bytes: int | None) -> str:
        parts = []
        width = summary.get("width")
        height = summary.get("height")
        if width and height:
            parts.append(f"{width}x{height}")
        video_format = summary.get("video_format")
        if video_format:
            parts.append(f"Видео: {video_format}")
        audio_format = summary.get("audio_format")
        if audio_format:
            parts.append(f"Аудио: {audio_format}")
        size_value = MediaInfo._format_size(size_bytes)
        if size_value:
            parts.append(f"Размер: {size_value}")
        return " | ".join(parts)

    @staticmethod
    def _format_size(size_bytes: int | None) -> str:
        if not size_bytes:
            return ""
        size = float(size_bytes)
        for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
            if size < 1024 or unit == "ТБ":
                if unit == "Б":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024
        return ""

    # Получаем подробную структуру из mediainfo в виде словаря.
    @staticmethod
    def get_details(path: str) -> dict:
        """Подробные данные по дорожкам (для диалога видео)."""
        data = MediaInfo._details_from_pymediainfo(path)
        if data:
            return data
        return MediaInfo._details_from_cli(path)

    @staticmethod
    def _details_from_pymediainfo(path: str) -> dict:
        try:
            from pymediainfo import MediaInfo as PyMediaInfo
        except Exception:
            return {}
        try:
            media_info = PyMediaInfo.parse(path)
        except Exception:
            return {}
        tracks = []
        for idx, track in enumerate(media_info.tracks):
            tracks.append(
                {
                    "index": idx,
                    "type": track.track_type,
                    "format": track.format or "",
                    "width": track.width or "",
                    "height": track.height or "",
                    "bit_rate": track.bit_rate or "",
                    "language": track.language or "",
                    "title": getattr(track, "title", "") or "",
                    "codec_id": getattr(track, "codec_id", "") or "",
                    "encoding": getattr(track, "encoding_settings", "") or "",
                }
            )
        return {"tracks": tracks}

    @staticmethod
    def _details_from_cli(path: str) -> dict:
        try:
            result = subprocess.run(
                ["mediainfo", "--Output=JSON", path],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return {}
        if result.returncode != 0:
            return {}
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}
        tracks = []
        for idx, track in enumerate(data.get("media", {}).get("track", [])):
            tracks.append(
                {
                    "index": idx,
                    "type": track.get("@type", ""),
                    "format": track.get("Format", ""),
                    "width": track.get("Width", ""),
                    "height": track.get("Height", ""),
                    "bit_rate": track.get("BitRate", ""),
                    "language": track.get("Language", ""),
                    "title": track.get("Title", ""),
                    "codec_id": track.get("CodecID", ""),
                    "encoding": track.get("Encoded_Library_Settings", ""),
                }
            )
        return {"tracks": tracks}


# Главное окно приложения с тремя панелями.
class HSorterWindow(Gtk.ApplicationWindow):
    # Инициализация окна и построение интерфейса.
    def __init__(self, app: Gtk.Application, db: Database) -> None:
        super().__init__(application=app, title="HSorter")
        self.set_default_size(1400, 900)
        self.db = db
        self.current_title_id = None
        self.cover_path = ""
        self.new_title_mode = False
        self.status_colors = self._build_status_colors()
        self.is_dirty = False
        self.created_at_value = ""
        self.updated_at_value = ""

        self._build_ui()
        self._load_window_settings()
        self.connect("destroy", self._save_window_settings)
        self.connect("delete-event", self._on_delete_event)
        self.connect("configure-event", self._on_configure_event)
        self.connect("window-state-event", self._on_window_state_event)
        self.refresh_titles()

    # Общая разметка: три колонки.
    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        self.main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        root.pack_start(self.main_paned, True, True, 0)

        self.library_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.media_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.library_box.set_size_request(100, -1)
        self.details_box.set_size_request(100, -1)
        self.media_box.set_size_request(100, -1)

        self.main_paned.add1(self.library_box)
        self.right_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.right_paned.add1(self.details_box)
        self.right_paned.add2(self.media_box)
        self.main_paned.add2(self.right_paned)
        self.main_paned.set_wide_handle(True)
        self.right_paned.set_wide_handle(True)

        self._build_library()
        self._build_details()
        self._build_media()

    # Левая панель: библиотека тайтлов и фильтры.
    def _build_library(self) -> None:
        system_menu = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        system_menu.set_margin_bottom(4)
        self.settings_button = Gtk.Button(label="⚙")
        self.settings_button.set_size_request(36, 36)
        self.settings_button.set_tooltip_text("Настройки")
        self.settings_button.connect("clicked", lambda _b: self.open_settings_dialog())
        system_menu.pack_start(self.settings_button, False, False, 0)
        system_menu.pack_start(Gtk.Box(), True, True, 0)
        self.library_box.pack_start(system_menu, False, False, 0)

        filter_frame = Gtk.Frame(label="Фильтр")
        filter_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        filter_box.set_margin_top(6)
        filter_box.set_margin_bottom(6)
        filter_box.set_margin_start(6)
        filter_box.set_margin_end(6)
        filter_frame.add(filter_box)

        self.filter_name = Gtk.Entry()
        self.filter_tags = Gtk.Entry()
        self.filter_status = Gtk.Entry()
        self.filter_sort = Gtk.ComboBoxText()
        self.filter_sort.append("title", "По названию")
        self.filter_sort.append("created_at", "По дате добавления")
        self.filter_sort.set_active_id("title")
        filter_box.pack_start(self._row("Название", self.filter_name), False, False, 0)
        filter_box.pack_start(self._row("Теги", self.filter_tags), False, False, 0)
        filter_box.pack_start(self._row("Статус", self.filter_status), False, False, 0)
        filter_box.pack_start(self._row("Сортировка", self.filter_sort), False, False, 0)
        apply_button = Gtk.Button(label="Применить")
        apply_button.connect("clicked", lambda _b: self.refresh_titles())
        filter_box.pack_start(apply_button, False, False, 0)
        self.library_box.pack_start(filter_frame, False, False, 0)

        self.title_list = Gtk.ListBox()
        self.title_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.title_list.connect("row-selected", self.on_title_selected)
        list_scroller = Gtk.ScrolledWindow()
        list_scroller.set_vexpand(True)
        list_scroller.add(self.title_list)
        self.library_box.pack_start(list_scroller, True, True, 0)

        self.show_status = Gtk.CheckButton(label="Показать статусы")
        self.show_status.set_active(True)
        self.show_status.connect("toggled", lambda _b: self.refresh_titles())
        self.library_box.pack_start(self.show_status, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add_button = Gtk.Button(label="Добавить")
        add_button.connect("clicked", lambda _b: self.add_title())
        import_button = Gtk.Button(label="Импорт")
        import_button.connect("clicked", lambda _b: self.import_title())
        delete_button = Gtk.Button(label="Удалить")
        delete_button.connect("clicked", lambda _b: self.delete_title())
        buttons.pack_start(add_button, False, False, 0)
        buttons.pack_start(import_button, False, False, 0)
        buttons.pack_start(delete_button, False, False, 0)
        self.library_box.pack_start(buttons, False, False, 0)

    # Центральная панель: карточка выбранного тайтла.
    def _build_details(self) -> None:
        header = Gtk.Label(label="Карточка тайтла")
        header.set_xalign(0)
        self.details_box.pack_start(header, False, False, 0)

        cover_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.cover_image = Gtk.Image()
        self.cover_image.set_size_request(240, 240)
        self.cover_event = Gtk.EventBox()
        self.cover_event.add(self.cover_image)
        self.cover_event.connect("button-press-event", self.on_cover_double_click)
        self.cover_button = Gtk.Button(label="Загрузить изображение")
        self.cover_button.connect("clicked", lambda _b: self.pick_cover())
        cover_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        cover_column.pack_start(self.cover_event, False, False, 0)
        cover_column.pack_start(self.cover_button, False, False, 0)
        cover_row.pack_start(cover_column, False, False, 0)

        title_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.main_title = Gtk.Entry()
        self.main_title.connect("changed", self.on_main_title_changed)
        self.alt_titles = Gtk.Entry()
        self.alt_titles.connect("changed", lambda _e: self._mark_dirty())
        title_column.pack_start(self._row("Основное название", self.main_title), False, False, 0)
        self.name_warning_label = Gtk.Label(label="")
        self.name_warning_label.set_xalign(0)
        title_column.pack_start(self.name_warning_label, False, False, 0)
        title_column.pack_start(
            self._row("Дополнительные названия", self.alt_titles), False, False, 0
        )
        cover_row.pack_start(title_column, True, True, 0)

        self.details_box.pack_start(cover_row, False, False, 0)

        # Выпадающий чеклист статусов.
        self.status_checks = {}
        self.status_button = Gtk.MenuButton(label="Статусы")
        status_popover = Gtk.Popover.new(self.status_button)
        status_popover.set_position(Gtk.PositionType.BOTTOM)
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        status_box.set_margin_top(6)
        status_box.set_margin_bottom(6)
        status_box.set_margin_start(6)
        status_box.set_margin_end(6)
        status_scroller = Gtk.ScrolledWindow()
        status_scroller.set_size_request(260, 180)
        status_scroller.add(status_box)
        status_popover.add(status_scroller)
        for status in STATUS_OPTIONS:
            check = self._build_status_check(status)
            check.connect("toggled", lambda _e: self._mark_dirty())
            self.status_checks[status] = check
            status_box.pack_start(check, False, False, 0)
        status_popover.show_all()
        status_popover.hide()
        self.status_button.set_popover(status_popover)
        self.details_box.pack_start(self._row("Статус", self.status_button), False, False, 0)

        rating_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.rating_entry = Gtk.Entry()
        self.personal_rating_entry = Gtk.Entry()
        self.censored_check = Gtk.CheckButton(label="Цензура")
        self.rating_entry.connect("changed", lambda _e: self._mark_dirty())
        self.personal_rating_entry.connect("changed", lambda _e: self._mark_dirty())
        self.censored_check.connect("toggled", lambda _e: self._mark_dirty())
        rating_row.pack_start(self._row("Рейтинг", self.rating_entry), True, True, 0)
        rating_row.pack_start(
            self._row("Личный рейтинг", self.personal_rating_entry), True, True, 0
        )
        rating_row.pack_start(self.censored_check, False, False, 0)
        self.details_box.pack_start(rating_row, False, False, 0)

        year_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        current_year = datetime.date.today().year
        self.year_start = Gtk.SpinButton.new_with_range(1900, current_year + 5, 1)
        self.year_end = Gtk.Entry()
        self.year_start.set_value(current_year)
        self.year_end.set_text("")
        self.year_start.connect("value-changed", self.on_year_start_changed)
        self.year_end.connect("changed", self.on_year_end_changed)
        self.episodes_spin = Gtk.SpinButton.new_with_range(0, 10000, 1)
        self.duration_entry = Gtk.Entry()
        self.episodes_spin.connect("value-changed", lambda _e: self._mark_dirty())
        self.duration_entry.connect("changed", lambda _e: self._mark_dirty())
        year_row.pack_start(self._row("Год начала", self.year_start), True, True, 0)
        year_row.pack_start(self._row("Год окончания", self.year_end), True, True, 0)
        year_row.pack_start(self._row("Эпизоды", self.episodes_spin), True, True, 0)
        year_row.pack_start(self._row("Длительность", self.duration_entry), True, True, 0)
        self.details_box.pack_start(year_row, False, False, 0)

        url_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.url_entry = Gtk.Entry()
        open_url_button = Gtk.Button(label="Открыть ссылку")
        open_url_button.connect("clicked", lambda _b: self.open_title_url())
        self.sync_anidb_button = Gtk.Button(label="Синхр.")
        self.sync_anidb_button.set_sensitive(False)
        self.sync_anidb_button.connect("clicked", lambda _b: self.sync_title_with_anidb())
        self.url_entry.connect("changed", lambda _e: self._on_url_changed())
        url_row.pack_start(self._row("URL", self.url_entry), True, True, 0)
        url_row.pack_start(open_url_button, False, False, 0)
        url_row.pack_start(self.sync_anidb_button, False, False, 0)
        self.details_box.pack_start(url_row, False, False, 0)

        date_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.created_at_label = Gtk.Label(label="")
        self.created_at_label.set_xalign(0)
        self.updated_at_label = Gtk.Label(label="")
        self.updated_at_label.set_xalign(0)
        date_row.pack_start(self._row("Дата добавления", self.created_at_label), True, True, 0)
        date_row.pack_start(self._row("Дата изменения", self.updated_at_label), True, True, 0)
        self.details_box.pack_start(date_row, False, False, 0)

        self.description_buffer = Gtk.TextBuffer()
        description_view = Gtk.TextView(buffer=self.description_buffer)
        description_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.description_buffer.connect("changed", lambda _b: self._mark_dirty())
        description_scroller = Gtk.ScrolledWindow()
        description_scroller.set_vexpand(True)
        description_scroller.add(description_view)
        self.details_box.pack_start(
            self._section("Краткое описание", description_scroller), True, True, 0
        )

        info_frame = Gtk.Frame(label="Сведения")
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        info_box.set_margin_top(6)
        info_box.set_margin_bottom(6)
        info_box.set_margin_start(6)
        info_box.set_margin_end(6)
        info_frame.add(info_box)
        info_grid = Gtk.Grid(column_spacing=6, row_spacing=4)
        self.info_entries = {}
        labels = [
            "Страна",
            "Производство",
            "Режиссёр",
            "Дизайнер персонажей",
            "Автор сценария/оригинала",
            "Композитор",
            "Автор субтитров",
            "Автор озвучки",
        ]
        for idx, label in enumerate(labels):
            entry = Gtk.Entry()
            entry.connect("changed", lambda _e: self._mark_dirty())
            self.info_entries[label] = entry
            info_grid.attach(Gtk.Label(label=label, xalign=0), 0, idx, 1, 1)
            info_grid.attach(entry, 1, idx, 1, 1)
        info_box.pack_start(info_grid, True, True, 0)
        self.title_comment_buffer = Gtk.TextBuffer()
        comment_view = Gtk.TextView(buffer=self.title_comment_buffer)
        comment_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.title_comment_buffer.connect("changed", lambda _b: self._mark_dirty())
        comment_scroller = Gtk.ScrolledWindow()
        comment_scroller.set_size_request(240, 160)
        comment_scroller.set_vexpand(True)
        comment_scroller.add(comment_view)
        comment_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        comment_label = Gtk.Label(label="Комментарий")
        comment_label.set_xalign(0)
        comment_box.pack_start(comment_label, False, False, 0)
        comment_box.pack_start(comment_scroller, True, True, 0)
        info_box.pack_start(comment_box, True, True, 0)
        self.details_box.pack_start(info_frame, False, False, 0)

        tags_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.tags_entry = Gtk.Entry()
        self.tags_entry.connect("changed", lambda _e: self._mark_dirty())
        add_tag = Gtk.Button(label="Добавить тег")
        add_tag.connect("clicked", lambda _b: self.add_tag())
        tags_row.pack_start(self.tags_entry, True, True, 0)
        tags_row.pack_start(add_tag, False, False, 0)
        self.details_box.pack_start(self._section("Теги", tags_row), False, False, 0)

        self.save_button = Gtk.Button(label="Сохранить изменения")
        self.save_button.connect("clicked", lambda _b: self.save_title())
        self.save_button.set_sensitive(False)
        self.details_box.pack_start(self.save_button, False, False, 0)

        self._enable_drop(self.cover_image, self.on_cover_drop)

    # Правая панель: изображения и видео.
    def _build_media(self) -> None:
        images_frame = Gtk.Frame(label="Изображения")
        images_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        images_box.set_margin_top(6)
        images_box.set_margin_bottom(6)
        images_box.set_margin_start(6)
        images_box.set_margin_end(6)
        images_frame.add(images_box)
        # Список изображений в виде миниатюр (thumbnails).
        self.images_store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, int, str, int)
        self.images_view = Gtk.IconView.new()
        self.images_view.set_model(self.images_store)
        self.images_view.set_pixbuf_column(0)
        self.images_view.set_margin(6)
        self.images_view.set_item_padding(6)
        self.images_view.set_columns(1)
        self.images_view.set_reorderable(True)
        self.images_view.connect("item-activated", self.on_image_activated)
        self.images_view.connect("button-press-event", self.on_images_menu)
        self.images_store.connect("rows-reordered", self.on_images_reordered)
        self.images_view.connect("drag-end", self.on_images_drag_end)
        self.images_scroller = Gtk.ScrolledWindow()
        self.images_scroller.set_vexpand(True)
        self.images_scroller.add(self.images_view)
        images_box.pack_start(self.images_scroller, True, True, 0)
        add_image = Gtk.Button(label="Добавить файл")
        add_image.connect("clicked", lambda _b: self.add_image())
        images_box.pack_start(add_image, False, False, 0)
        self.media_box.pack_start(images_frame, True, True, 0)

        videos_frame = Gtk.Frame(label="Видео")
        videos_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        videos_box.set_margin_top(6)
        videos_box.set_margin_bottom(6)
        videos_box.set_margin_start(6)
        videos_box.set_margin_end(6)
        videos_frame.add(videos_box)
        # Список видеофайлов с поддержкой сортировки и контекстного меню.
        self.videos_store = Gtk.ListStore(str, str, int)
        self.videos_view = Gtk.TreeView(model=self.videos_store)
        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("Видео", renderer, markup=0)
        self.videos_view.append_column(column)
        self.videos_view.set_reorderable(True)
        self.videos_view.connect("button-press-event", self.on_videos_menu)
        self.videos_view.connect("row-activated", self.on_video_activated)
        self.videos_store.connect("rows-reordered", self.on_videos_reordered)
        self.videos_view.connect("drag-end", self.on_videos_drag_end)
        self.videos_scroller = Gtk.ScrolledWindow()
        self.videos_scroller.set_vexpand(True)
        self.videos_scroller.add(self.videos_view)
        videos_box.pack_start(self.videos_scroller, True, True, 0)
        add_video = Gtk.Button(label="Добавить файл")
        add_video.connect("clicked", lambda _b: self.add_video())
        videos_box.pack_start(add_video, False, False, 0)
        self.media_box.pack_start(videos_frame, True, True, 0)

        self._enable_drop(self.images_scroller, self.on_images_drop)
        self._enable_drop(self.videos_scroller, self.on_videos_drop)

    # Утилита для строки "метка + виджет".
    def _row(self, label: str, widget: Gtk.Widget) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        row.pack_start(widget, True, True, 0)
        return row

    # Утилита для секций с заголовком.
    def _section(self, title: str, widget: Gtk.Widget) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        label = Gtk.Label(label=title)
        label.set_xalign(0)
        box.pack_start(label, False, False, 0)
        box.pack_start(widget, True, True, 0)
        return box

    # Включаем drag-and-drop для виджета.
    def _enable_drop(self, widget: Gtk.Widget, handler) -> None:
        target = Gtk.TargetEntry.new("text/uri-list", 0, 0)
        widget.drag_dest_set(Gtk.DestDefaults.ALL, [target], Gdk.DragAction.COPY)
        widget.connect("drag-data-received", handler)

    # Обновление списка тайтлов слева с учётом фильтров.
    def refresh_titles(self) -> None:
        for row in self.title_list.get_children():
            self.title_list.remove(row)
        titles = self.db.list_titles(
            query=self.filter_name.get_text().strip(),
            tags=self.filter_tags.get_text().strip(),
            status_filter=self.filter_status.get_text().strip(),
            sort_by=self.filter_sort.get_active_id() or "title",
        )
        self.title_rows = []
        for title in titles:
            display = html.escape(self._truncate_title(title["main_title"]))
            if self.show_status.get_active():
                status_data = json.loads(title["status_json"] or "{}")
                enabled = [s for s, v in status_data.items() if v]
                if enabled:
                    status_markup = "; ".join(
                        [self._format_status_span(status) for status in enabled]
                    )
                    display += f"\n  <span size='small'>{status_markup}</span>"
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=display, xalign=0)
            label.set_use_markup(True)
            row.add(label)
            self.title_list.add(row)
            self.title_rows.append(title["id"])
        self.title_list.show_all()

    # Обработчик выбора тайтла в списке.
    def on_title_selected(self, _listbox, row: Gtk.ListBoxRow) -> None:
        if not row:
            return
        index = row.get_index()
        title_id = self.title_rows[index]
        if self.is_dirty:
            choice = self._prompt_unsaved("перейти к другому тайтлу")
            if choice == "save":
                if not self.save_title():
                    self._select_current_title_in_list()
                    return
            elif choice == "cancel":
                self._select_current_title_in_list()
                return
        self.load_title(title_id)

    # Загрузка данных тайтла в форму.
    def load_title(self, title_id: int) -> None:
        title = self.db.get_title(title_id)
        if not title:
            return
        self.current_title_id = title_id
        self.new_title_mode = False
        self.cover_path = title["cover_path"] or ""
        cached_cover = self._cache_image(self.cover_path)
        if cached_cover and cached_cover != self.cover_path:
            self.cover_path = cached_cover
            self.db.update_title_cover(title_id, cached_cover)
        self.main_title.set_text(title["main_title"])
        self.alt_titles.set_text(title["alt_titles"])
        self.rating_entry.set_text("" if title["rating"] is None else str(title["rating"]))
        self.personal_rating_entry.set_text(
            "" if title["personal_rating"] is None else str(title["personal_rating"])
        )
        self.censored_check.set_active(bool(title["censored"]))
        self.year_start.set_value(title["year_start"] or datetime.date.today().year)
        self.year_end.set_text(str(title["year_end"] or ""))
        self.episodes_spin.set_value(title["episodes"] or 0)
        self.duration_entry.set_text(title["total_duration"])
        self.url_entry.set_text(title["url"] or "")
        self._update_sync_button()
        self.created_at_value = title["created_at"] or ""
        self.updated_at_value = title["updated_at"] or ""
        self.created_at_label.set_text(self._format_date(self.created_at_value))
        self.updated_at_label.set_text(self._format_date(self.updated_at_value))
        self.description_buffer.set_text(title["description"] or "")
        info_map = {
            "Страна": title["country"],
            "Производство": title["production"],
            "Режиссёр": title["director"],
            "Дизайнер персонажей": title["character_designer"],
            "Автор сценария/оригинала": title["author"],
            "Композитор": title["composer"],
            "Автор субтитров": title["subtitles_author"],
            "Автор озвучки": title["voice_author"],
        }
        for key, entry in self.info_entries.items():
            entry.set_text(info_map.get(key, ""))
        self.title_comment_buffer.set_text(title["title_comment"] or "")
        self.tags_entry.set_text(title["tags"])
        status_data = json.loads(title["status_json"] or "{}")
        for status, check in self.status_checks.items():
            check.set_active(bool(status_data.get(status)))
        self._set_cover(self.cover_path)
        self.refresh_media_lists()
        self._update_save_state()
        self._clear_dirty()

    # Установка изображения обложки.
    def _set_cover(self, path: str) -> None:
        if path and os.path.exists(path):
            pixbuf = self._load_pixbuf(path)
            if pixbuf:
                self.cover_image.set_from_pixbuf(pixbuf)
                return
        self.cover_image.clear()

    # Читаем файл в Pixbuf и масштабируем до 240px по большей стороне.
    def _load_pixbuf(self, path: str):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        except Exception:
            return None
        width, height = pixbuf.get_width(), pixbuf.get_height()
        scale = min(240 / width, 240 / height, 1)
        if scale < 1:
            pixbuf = pixbuf.scale_simple(
                int(width * scale), int(height * scale), GdkPixbuf.InterpType.BILINEAR
            )
        return pixbuf

    # Создаём миниатюру для списка изображений (обрезаем по большей стороне до 160px).
    def _load_thumbnail(self, path: str):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        except Exception:
            return None
        width, height = pixbuf.get_width(), pixbuf.get_height()
        scale = min(160 / width, 160 / height, 1)
        if scale < 1:
            pixbuf = pixbuf.scale_simple(
                int(width * scale), int(height * scale), GdkPixbuf.InterpType.BILINEAR
            )
        return pixbuf

    # Добавить новый тайтл.
    def add_title(self) -> None:
        if self.is_dirty:
            choice = self._prompt_unsaved("создать новый тайтл")
            if choice == "save":
                if not self.save_title():
                    return
            elif choice == "cancel":
                return
        self.new_title_mode = True
        self.current_title_id = None
        self.clear_form()
        self.main_title.grab_focus()
        self._update_save_state()

    def import_title(self) -> None:
        url = self._prompt_text("Импорт AniDB", "Введите ссылку на AniDB")
        if not url:
            return
        anime_id = self._extract_anidb_id(url)
        if not anime_id:
            self._message("AniDB", "Не удалось определить ID тайтла из ссылки.")
            return
        anidb_data = self._fetch_anidb_data(anime_id)
        if not anidb_data:
            return
        empty_local = {key: "" for key in anidb_data}
        accepted, data = self._open_sync_wizard(empty_local, anidb_data, is_import=True)
        if not accepted:
            return
        self.new_title_mode = True
        self.current_title_id = None
        self.clear_form()
        self._apply_sync_data_to_form(data)
        self.save_title()

    # Сохранить изменения.
    def save_title(self) -> bool:
        if not self._update_save_state():
            return False
        data = self.collect_form_data()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        if self.new_title_mode:
            self.created_at_value = now
            self.updated_at_value = now
            data["created_at"] = self.created_at_value
            data["updated_at"] = self.updated_at_value
            title_id = self.db.add_title(data)
            self.new_title_mode = False
            self.refresh_titles()
            self.load_title(title_id)
        else:
            if not self.current_title_id:
                self._message("Нет тайтла", "Выберите тайтл в списке.")
                return False
            if not self.created_at_value:
                self.created_at_value = now
            self.updated_at_value = now
            data["created_at"] = self.created_at_value
            data["updated_at"] = self.updated_at_value
            self.db.update_title(self.current_title_id, data)
            self.refresh_titles()
            self.created_at_label.set_text(self._format_date(self.created_at_value))
            self.updated_at_label.set_text(self._format_date(self.updated_at_value))
        self._clear_dirty()
        return True

    # Удалить выбранный тайтл.
    def delete_title(self) -> None:
        if not self.current_title_id:
            return
        if not self._confirm("Удалить", "Удалить выбранный тайтл?"):
            return
        self.db.delete_title(self.current_title_id)
        self.current_title_id = None
        self.refresh_titles()
        self.clear_form()

    # Считать значения формы в словарь для сохранения.
    def collect_form_data(self) -> dict:
        status_data = {status: check.get_active() for status, check in self.status_checks.items()}
        return {
            "main_title": self.main_title.get_text().strip(),
            "alt_titles": self.alt_titles.get_text().strip(),
            "rating": self._parse_optional_int(self.rating_entry.get_text().strip()),
            "personal_rating": self._parse_optional_int(
                self.personal_rating_entry.get_text().strip()
            ),
            "censored": self.censored_check.get_active(),
            "year_start": int(self.year_start.get_value()),
            "year_end": self._parse_optional_int(self.year_end.get_text().strip()),
            "episodes": int(self.episodes_spin.get_value()),
            "total_duration": self.duration_entry.get_text().strip(),
            "description": self.description_buffer.get_text(
                self.description_buffer.get_start_iter(),
                self.description_buffer.get_end_iter(),
                True,
            ).strip(),
            "country": self.info_entries["Страна"].get_text().strip(),
            "production": self.info_entries["Производство"].get_text().strip(),
            "director": self.info_entries["Режиссёр"].get_text().strip(),
            "character_designer": self.info_entries["Дизайнер персонажей"].get_text().strip(),
            "author": self.info_entries["Автор сценария/оригинала"].get_text().strip(),
            "composer": self.info_entries["Композитор"].get_text().strip(),
            "subtitles_author": self.info_entries["Автор субтитров"].get_text().strip(),
            "voice_author": self.info_entries["Автор озвучки"].get_text().strip(),
            "title_comment": self.title_comment_buffer.get_text(
                self.title_comment_buffer.get_start_iter(),
                self.title_comment_buffer.get_end_iter(),
                True,
            ).strip(),
            "url": self.url_entry.get_text().strip(),
            "status": status_data,
            "tags": self.tags_entry.get_text().strip(),
            "cover_path": self.cover_path,
            "created_at": self.created_at_value,
            "updated_at": self.updated_at_value,
        }

    # Очистить форму, если тайтл удалён или не выбран.
    def clear_form(self) -> None:
        self.main_title.set_text("")
        self.alt_titles.set_text("")
        self.rating_entry.set_text("")
        self.personal_rating_entry.set_text("")
        self.censored_check.set_active(False)
        year = datetime.date.today().year
        self.year_start.set_value(year)
        self.year_end.set_text("")
        self.episodes_spin.set_value(0)
        self.duration_entry.set_text("")
        self.description_buffer.set_text("")
        for entry in self.info_entries.values():
            entry.set_text("")
        self.title_comment_buffer.set_text("")
        self.url_entry.set_text("")
        self._update_sync_button()
        self.created_at_label.set_text("")
        self.updated_at_label.set_text("")
        self.created_at_value = ""
        self.updated_at_value = ""
        for check in self.status_checks.values():
            check.set_active(False)
        self.tags_entry.set_text("")
        self.cover_path = ""
        self.cover_image.clear()
        self.images_store.clear()
        self.videos_store.clear()
        self.name_warning_label.set_text("")
        self.save_button.set_sensitive(False)
        self._clear_dirty()

    # Добавление тега через диалог.
    def add_tag(self) -> None:
        dialog = Gtk.Dialog(title="Тег", transient_for=self, modal=True)
        dialog.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dialog.add_button("Добавить", Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        content = dialog.get_content_area()
        content.add(Gtk.Label(label="Введите новый тег"))
        content.add(entry)
        dialog.show_all()
        response = dialog.run()
        new_tag = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not new_tag:
            return
        current = self.tags_entry.get_text().strip()
        if current:
            tags = [t.strip() for t in current.split(",") if t.strip()]
            tags.append(new_tag)
            self.tags_entry.set_text(", ".join(sorted(set(tags))))
        else:
            self.tags_entry.set_text(new_tag)
        self._mark_dirty()

    # Выбор обложки через файловый диалог.
    def pick_cover(self) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Выберите изображение",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Выбрать", Gtk.ResponseType.OK)
        filter_images = Gtk.FileFilter()
        filter_images.add_mime_type("image/png")
        filter_images.add_mime_type("image/jpeg")
        filter_images.add_mime_type("image/bmp")
        filter_images.add_mime_type("image/gif")
        dialog.add_filter(filter_images)
        if dialog.run() == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            if filename:
                self.cover_path = self._cache_image(filename)
                self._set_cover(self.cover_path)
                self._mark_dirty()
        dialog.destroy()

    # Обработка drop для обложки.
    def on_cover_drop(self, _widget, _context, _x, _y, data, _info, _time) -> None:
        path = self._first_path_from_drop(data)
        if path:
            self.cover_path = self._cache_image(path)
            self._set_cover(self.cover_path)
            self._mark_dirty()

    def on_cover_double_click(self, _widget, event) -> None:
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self._open_default_if_exists(self.cover_path)

    # Возвращает первый путь из drag-and-drop.
    def _first_path_from_drop(self, data: Gtk.SelectionData) -> str:
        paths = self._extract_paths(data)
        return paths[0] if paths else ""

    # Преобразование URI из drop в локальные пути.
    def _extract_paths(self, data: Gtk.SelectionData) -> list:
        uris = data.get_uris()
        paths = []
        for uri in uris:
            file = Gio.File.new_for_uri(uri)
            path = file.get_path()
            if path:
                paths.append(path)
        return paths

    # Перерисовка списков изображений и видео.
    def refresh_media_lists(self) -> None:
        self.images_store.clear()
        self.videos_store.clear()
        if not self.current_title_id:
            return
        for item in self.db.list_media(self.current_title_id, "image"):
            cached_path = self._cache_image(item["path"])
            if cached_path and cached_path != item["path"]:
                self.db.update_media_path(item["id"], cached_path)
            pixbuf = self._load_thumbnail(cached_path)
            if pixbuf:
                self.images_store.append(
                    [pixbuf, cached_path, item["id"], "image", item["id"]]
                )
        for item in self.db.list_media(self.current_title_id, "video"):
            filename = os.path.basename(item["path"])
            info_value = item["info"] or ""
            if item["path"] and (not info_value or "Размер:" not in info_value):
                info_value = MediaInfo.describe_video(item["path"])
                if info_value:
                    self.db.update_media_info(item["id"], info_value)
            if info_value:
                info_markup = f"\n<span size='smaller'>{html.escape(info_value)}</span>"
            else:
                info_markup = ""
            text = f"{html.escape(filename)}{info_markup}"
            self.videos_store.append([text, item["path"], item["id"]])
            if item["thumbnail_path"]:
                thumb_path = self._cache_image(item["thumbnail_path"])
                if thumb_path and thumb_path != item["thumbnail_path"]:
                    self.db.update_media_thumbnail(item["id"], thumb_path)
                if thumb_path and os.path.exists(thumb_path):
                    pixbuf = self._load_thumbnail(thumb_path)
                    if pixbuf:
                        self.images_store.append(
                            [pixbuf, thumb_path, None, "video_thumbnail", item["id"]]
                        )
        self.images_view.show_all()
        self.videos_view.show_all()

    # Добавление изображений к тайтлу.
    def add_image(self) -> None:
        if not self.current_title_id:
            self._message("Нет тайтла", "Сначала выберите тайтл.")
            return
        paths = self._pick_files("Выберите изображения", ["image/png", "image/jpeg", "image/bmp"])
        for path in paths:
            cached_path = self._cache_image(path)
            self.db.add_media(self.current_title_id, "image", cached_path, "")
        self.refresh_media_lists()

    # Добавление видео с автоматическим чтением MediaInfo.
    def add_video(self) -> None:
        if not self.current_title_id:
            self._message("Нет тайтла", "Сначала выберите тайтл.")
            return
        paths = self._pick_files("Выберите видео", ["video/x-matroska", "video/mp4", "video/quicktime"])
        for path in paths:
            info = MediaInfo.describe_video(path)
            self.db.add_media(self.current_title_id, "video", path, info)
        self.refresh_media_lists()

    # Обработка drop для списка изображений.
    def on_images_drop(self, _widget, _context, _x, _y, data, _info, _time) -> None:
        if not self.current_title_id:
            return
        for path in self._extract_paths(data):
            cached_path = self._cache_image(path)
            self.db.add_media(self.current_title_id, "image", cached_path, "")
        self.refresh_media_lists()

    # Открыть изображение по клику на миниатюре.
    def on_image_activated(self, _view, tree_path) -> None:
        model = self.images_store
        tree_iter = model.get_iter(tree_path)
        path = model.get_value(tree_iter, 1)
        if path:
            Gio.AppInfo.launch_default_for_uri(f"file://{path}", None)

    # Контекстное меню для удаления изображения.
    def on_images_menu(self, _view, event) -> bool:
        if event.button != 3:
            return False
        path = self.images_view.get_path_at_pos(int(event.x), int(event.y))
        if not path:
            return False
        self.images_view.select_path(path)
        menu = Gtk.Menu()
        delete_item = Gtk.MenuItem(label="Удалить")
        delete_item.connect("activate", lambda _i: self._delete_selected_image())
        menu.append(delete_item)
        menu.show_all()
        menu.popup(None, None, None, None, event.button, event.time)
        return True

    # Удаление выбранного изображения из БД и списка.
    def _delete_selected_image(self) -> None:
        paths = self.images_view.get_selected_items()
        if not paths:
            return
        tree_iter = self.images_store.get_iter(paths[0])
        source_type = self.images_store.get_value(tree_iter, 3)
        source_id = self.images_store.get_value(tree_iter, 4)
        if source_type == "image" and source_id:
            self.db.delete_media(source_id)
            self.refresh_media_lists()
        elif source_type == "video_thumbnail" and source_id:
            self.db.update_media_thumbnail(source_id, "")
            self.refresh_media_lists()

    # Сохранение порядка изображений после drag-and-drop.
    def on_images_reordered(self, _model, _path, _iter, _new_order) -> None:
        self._persist_media_order(self.images_store)

    # Дублируем сохранение порядка после drag-and-drop для Gtk.IconView.
    def on_images_drag_end(self, *_args) -> None:
        self._persist_media_order(self.images_store)

    # Обработка drop для списка видео.
    def on_videos_drop(self, _widget, _context, _x, _y, data, _info, _time) -> None:
        if not self.current_title_id:
            return
        for path in self._extract_paths(data):
            info = MediaInfo.describe_video(path)
            self.db.add_media(self.current_title_id, "video", path, info)
        self.refresh_media_lists()

    # Открытие модального окна с подробностями видео.
    def on_video_activated(self, _view, path, _column) -> None:
        tree_iter = self.videos_store.get_iter(path)
        media_path = self.videos_store.get_value(tree_iter, 1)
        media_id = self.videos_store.get_value(tree_iter, 2)
        if not media_id:
            return
        self._open_video_details_dialog(media_id, media_path)

    # Контекстное меню для удаления видео.
    def on_videos_menu(self, view, event) -> bool:
        if event.button != 3:
            return False
        path_info = view.get_path_at_pos(int(event.x), int(event.y))
        if not path_info:
            return False
        path, _column, _cell_x, _cell_y = path_info
        view.get_selection().select_path(path)
        menu = Gtk.Menu()
        delete_item = Gtk.MenuItem(label="Удалить")
        delete_item.connect("activate", lambda _i: self._delete_selected_video())
        menu.append(delete_item)
        menu.show_all()
        menu.popup(None, None, None, None, event.button, event.time)
        return True

    # Удаление выбранного видео из БД и списка.
    def _delete_selected_video(self) -> None:
        selection = self.videos_view.get_selection()
        model, tree_iter = selection.get_selected()
        if not tree_iter:
            return
        media_id = model.get_value(tree_iter, 2)
        if media_id:
            self.db.delete_media(media_id)
            self.refresh_media_lists()

    # Сохранение порядка видео после drag-and-drop.
    def on_videos_reordered(self, _model, _path, _iter, _new_order) -> None:
        self._persist_media_order(self.videos_store)

    # Дублируем сохранение порядка после drag-and-drop для Gtk.TreeView.
    def on_videos_drag_end(self, *_args) -> None:
        self._persist_media_order(self.videos_store)

    # Универсальный способ сохранить порядок по текущей модели.
    def _persist_media_order(self, model: Gtk.ListStore) -> None:
        media_ids = [row[2] for row in model if row[2] is not None]
        if media_ids:
            self.db.update_media_order(media_ids)

    def _truncate_title(self, title: str) -> str:
        if len(title) <= 32:
            return title
        return f"{title[:29]}..."

    def _build_status_colors(self) -> dict:
        colors = {}
        for status in STATUS_OPTIONS:
            digest = hashlib.md5(status.encode("utf-8")).hexdigest()
            hue = int(digest[:8], 16) / 0xFFFFFFFF
            saturation = 0.55
            lightness = 0.5
            r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
            colors[status] = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
        return colors

    def _format_status_span(self, status: str) -> str:
        color = self.status_colors.get(status, "#000000")
        return f"<span foreground='{color}'>{html.escape(status)}</span>"

    def _format_status_label(self, status: str) -> str:
        return self._format_status_span(status)

    def _build_status_check(self, status: str) -> Gtk.CheckButton:
        check = Gtk.CheckButton()
        label = Gtk.Label()
        label.set_xalign(0)
        label.set_use_markup(True)
        label.set_markup(self._format_status_label(status))
        check.add(label)
        label.show()
        return check

    def _parse_optional_int(self, value: str) -> int | None:
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def on_year_start_changed(self, _spin) -> None:
        start_year = int(self.year_start.get_value())
        end_year_text = self.year_end.get_text().strip()
        end_year = self._parse_optional_int(end_year_text)
        if end_year is None or start_year > end_year:
            self.year_end.set_text(str(start_year))
        self._mark_dirty()

    def on_year_end_changed(self, _entry) -> None:
        start_year = int(self.year_start.get_value())
        end_year = self._parse_optional_int(self.year_end.get_text().strip())
        if end_year is None or start_year > end_year:
            self.year_end.set_text(str(start_year))
        self._mark_dirty()

    def open_title_url(self) -> None:
        url = self.url_entry.get_text().strip()
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        Gio.AppInfo.launch_default_for_uri(url, None)

    def _on_url_changed(self) -> None:
        self._mark_dirty()
        self._update_sync_button()

    def _extract_anidb_id(self, url: str) -> str | None:
        if not url:
            return None
        match = re.search(r"anidb\.net/anime/(\d+)", url)
        return match.group(1) if match else None

    def _update_sync_button(self) -> None:
        if not hasattr(self, "sync_anidb_button"):
            return
        url = self.url_entry.get_text().strip()
        self.sync_anidb_button.set_sensitive(bool(self._extract_anidb_id(url)))

    def sync_title_with_anidb(self) -> None:
        url = self.url_entry.get_text().strip()
        anime_id = self._extract_anidb_id(url)
        if not anime_id:
            self._message("AniDB", "Ссылка на AniDB не найдена.")
            return
        anidb_data = self._fetch_anidb_data(anime_id)
        if not anidb_data:
            return
        local_data = self._title_data_for_sync()
        accepted, data = self._open_sync_wizard(local_data, anidb_data, is_import=False)
        if not accepted:
            return
        self._apply_sync_data_to_form(data)
        self.save_title()

    def _prompt_text(self, title: str, label: str) -> str | None:
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dialog.add_button("ОК", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_spacing(6)
        content.add(Gtk.Label(label=label))
        entry = Gtk.Entry()
        content.add(entry)
        dialog.show_all()
        response = dialog.run()
        text_value = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return None
        return text_value

    def _get_anidb_settings(self) -> dict:
        return {
            "username": self.db.get_setting("anidb_username") or "",
            "password": self.db.get_setting("anidb_password") or "",
        }

    def open_settings_dialog(self) -> None:
        dialog = Gtk.Dialog(title="Настройки", transient_for=self, modal=True)
        dialog.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dialog.add_button("Сохранить", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_spacing(8)

        anidb_frame = Gtk.Frame(label="AniDB.net")
        anidb_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        anidb_box.set_margin_top(6)
        anidb_box.set_margin_bottom(6)
        anidb_box.set_margin_start(6)
        anidb_box.set_margin_end(6)
        anidb_frame.add(anidb_box)

        grid = Gtk.Grid(column_spacing=6, row_spacing=4)
        username_entry = Gtk.Entry()
        password_entry = Gtk.Entry()
        password_entry.set_visibility(False)
        settings = self._get_anidb_settings()
        username_entry.set_text(settings["username"])
        password_entry.set_text(settings["password"])

        labels = [
            ("Логин", username_entry),
            ("Пароль", password_entry),
        ]
        for idx, (label, entry) in enumerate(labels):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, idx, 1, 1)
            grid.attach(entry, 1, idx, 1, 1)
        anidb_box.pack_start(grid, False, False, 0)
        content.add(anidb_frame)

        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.db.set_setting("anidb_username", username_entry.get_text().strip())
            self.db.set_setting("anidb_password", password_entry.get_text())
        dialog.destroy()

    def _fetch_anidb_data(self, anime_id: str) -> dict | None:
        settings = self._get_anidb_settings()
        missing = [key for key, value in settings.items() if not value]
        if missing:
            self._message("AniDB", "Заполните настройки AniDB в настройках приложения.")
            return None
        try:
            response_xml = self._anidb_http_request(
                "anime",
                {
                    "aid": anime_id,
                    "user": settings["username"],
                    "pass": settings["password"],
                },
            )
        except Exception as exc:
            self._message("AniDB", f"Ошибка запроса AniDB: {exc}")
            return None
        return self._anidb_xml_to_title_data(response_xml, anime_id)

    def _anidb_http_request(self, request_name: str, params: dict) -> str:
        base_url = "http://api.anidb.net:9001/httpapi"
        query = {
            "request": request_name,
            "client": "hsorter",
            "clientver": "1",
            "protover": "1",
        }
        query.update(params)
        url = f"{base_url}?{urllib.parse.urlencode(query)}"
        with urllib.request.urlopen(url, timeout=20) as response:
            content = response.read()
        return content.decode("utf-8", errors="replace")

    def _anidb_xml_to_title_data(self, xml_text: str, anime_id: str) -> dict:
        root = ET.fromstring(xml_text)
        anime_node = root.find("anime")
        if anime_node is None:
            raise ValueError("Ответ AniDB не содержит данных anime.")
        titles = anime_node.findall("title")
        main_title = ""
        alt_titles = []
        for title in titles:
            title_type = title.attrib.get("type", "")
            value = (title.text or "").strip()
            if not value:
                continue
            if title_type in ("main", "official") and not main_title:
                main_title = value
            else:
                alt_titles.append(value)
        description = (anime_node.findtext("description") or "").strip()
        start_date = (anime_node.findtext("startdate") or "").strip()
        year_value = ""
        if start_date:
            year_value = start_date.split("-")[0]
        episodes = (anime_node.findtext("episodecount") or "").strip()
        tags = []
        for genre in anime_node.findall("genres/genre"):
            name = (genre.text or "").strip()
            if name:
                tags.append(name)
        for category in anime_node.findall("categories/category"):
            name = (category.findtext("name") or "").strip()
            if name:
                tags.append(name)
        data = {
            "main_title": main_title,
            "alt_titles": ", ".join(sorted(set(alt_titles))),
            "year_start": year_value,
            "year_end": "",
            "episodes": episodes,
            "total_duration": "",
            "description": description,
            "country": "",
            "production": "",
            "director": "",
            "character_designer": "",
            "author": "",
            "composer": "",
            "subtitles_author": "",
            "voice_author": "",
            "title_comment": "",
            "tags": ", ".join(sorted(set(tags))),
            "url": f"https://anidb.net/anime/{anime_id}",
        }
        return data

    def _title_data_for_sync(self) -> dict:
        info_map = {
            "country": self.info_entries["Страна"].get_text().strip(),
            "production": self.info_entries["Производство"].get_text().strip(),
            "director": self.info_entries["Режиссёр"].get_text().strip(),
            "character_designer": self.info_entries["Дизайнер персонажей"].get_text().strip(),
            "author": self.info_entries["Автор сценария/оригинала"].get_text().strip(),
            "composer": self.info_entries["Композитор"].get_text().strip(),
            "subtitles_author": self.info_entries["Автор субтитров"].get_text().strip(),
            "voice_author": self.info_entries["Автор озвучки"].get_text().strip(),
        }
        return {
            "main_title": self.main_title.get_text().strip(),
            "alt_titles": self.alt_titles.get_text().strip(),
            "year_start": str(int(self.year_start.get_value())) if self.year_start else "",
            "year_end": self.year_end.get_text().strip(),
            "episodes": str(int(self.episodes_spin.get_value())) if self.episodes_spin else "",
            "total_duration": self.duration_entry.get_text().strip(),
            "description": self.description_buffer.get_text(
                self.description_buffer.get_start_iter(),
                self.description_buffer.get_end_iter(),
                True,
            ).strip(),
            "country": info_map["country"],
            "production": info_map["production"],
            "director": info_map["director"],
            "character_designer": info_map["character_designer"],
            "author": info_map["author"],
            "composer": info_map["composer"],
            "subtitles_author": info_map["subtitles_author"],
            "voice_author": info_map["voice_author"],
            "title_comment": self.title_comment_buffer.get_text(
                self.title_comment_buffer.get_start_iter(),
                self.title_comment_buffer.get_end_iter(),
                True,
            ).strip(),
            "tags": self.tags_entry.get_text().strip(),
            "url": self.url_entry.get_text().strip(),
        }

    def _apply_sync_data_to_form(self, data: dict) -> None:
        self.main_title.set_text(data.get("main_title", ""))
        self.alt_titles.set_text(data.get("alt_titles", ""))
        year_start = data.get("year_start", "")
        if year_start.isdigit():
            self.year_start.set_value(int(year_start))
        else:
            self.year_start.set_value(datetime.date.today().year)
        self.year_end.set_text(data.get("year_end", ""))
        episodes_value = data.get("episodes", "")
        if episodes_value.isdigit():
            self.episodes_spin.set_value(int(episodes_value))
        else:
            self.episodes_spin.set_value(0)
        self.duration_entry.set_text(data.get("total_duration", ""))
        self.description_buffer.set_text(data.get("description", ""))
        self.info_entries["Страна"].set_text(data.get("country", ""))
        self.info_entries["Производство"].set_text(data.get("production", ""))
        self.info_entries["Режиссёр"].set_text(data.get("director", ""))
        self.info_entries["Дизайнер персонажей"].set_text(
            data.get("character_designer", "")
        )
        self.info_entries["Автор сценария/оригинала"].set_text(data.get("author", ""))
        self.info_entries["Композитор"].set_text(data.get("composer", ""))
        self.info_entries["Автор субтитров"].set_text(data.get("subtitles_author", ""))
        self.info_entries["Автор озвучки"].set_text(data.get("voice_author", ""))
        self.title_comment_buffer.set_text(data.get("title_comment", ""))
        self.tags_entry.set_text(data.get("tags", ""))
        self.url_entry.set_text(data.get("url", ""))
        self._update_sync_button()
        self._mark_dirty()

    def _open_sync_wizard(
        self, local_data: dict, anidb_data: dict, is_import: bool
    ) -> tuple[bool, dict]:
        fields = [
            ("main_title", "Основное название", False),
            ("alt_titles", "Дополнительные названия", False),
            ("year_start", "Год начала", False),
            ("year_end", "Год окончания", False),
            ("episodes", "Эпизоды", False),
            ("total_duration", "Длительность", False),
            ("description", "Краткое описание", True),
            ("country", "Страна", False),
            ("production", "Производство", False),
            ("director", "Режиссёр", False),
            ("character_designer", "Дизайнер персонажей", False),
            ("author", "Автор сценария/оригинала", False),
            ("composer", "Композитор", False),
            ("subtitles_author", "Автор субтитров", False),
            ("voice_author", "Автор озвучки", False),
            ("title_comment", "Комментарий", True),
            ("tags", "Теги", False),
            ("url", "URL", False),
        ]
        data = {key: local_data.get(key, "") for key, _, _ in fields}
        dialog = Gtk.Dialog(title="Синхронизация AniDB", transient_for=self, modal=True)
        dialog.set_default_size(900, 600)
        dialog.add_button("Отмена", Gtk.ResponseType.CANCEL)
        content = dialog.get_content_area()
        content.set_spacing(8)

        header = Gtk.Label(label="")
        header.set_xalign(0)
        content.add(header)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        local_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        remote_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row.pack_start(local_box, True, True, 0)
        row.pack_start(remote_box, True, True, 0)
        content.add(row)

        local_label = Gtk.Label(label="Локальные данные")
        local_label.set_xalign(0)
        local_box.pack_start(local_label, False, False, 0)
        local_buffer = Gtk.TextBuffer()
        local_view = Gtk.TextView(buffer=local_buffer)
        local_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        local_scroller = Gtk.ScrolledWindow()
        local_scroller.set_vexpand(True)
        local_scroller.add(local_view)
        local_box.pack_start(local_scroller, True, True, 0)

        remote_label = Gtk.Label(label="AniDB")
        remote_label.set_xalign(0)
        remote_box.pack_start(remote_label, False, False, 0)
        remote_buffer = Gtk.TextBuffer()
        remote_view = Gtk.TextView(buffer=remote_buffer)
        remote_view.set_editable(False)
        remote_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        remote_scroller = Gtk.ScrolledWindow()
        remote_scroller.set_vexpand(True)
        remote_scroller.add(remote_view)
        remote_box.pack_start(remote_scroller, True, True, 0)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        copy_button = Gtk.Button(label="Копировать")
        copy_all_button = Gtk.Button(label="Копировать всё")
        prev_button = Gtk.Button(label="Назад")
        next_button = Gtk.Button(label="Далее")
        action_row.pack_start(copy_button, False, False, 0)
        action_row.pack_start(copy_all_button, False, False, 0)
        action_row.pack_end(next_button, False, False, 0)
        action_row.pack_end(prev_button, False, False, 0)
        content.add(action_row)

        index = 0

        def read_local() -> None:
            key, _, _ = fields[index]
            data[key] = local_buffer.get_text(
                local_buffer.get_start_iter(),
                local_buffer.get_end_iter(),
                True,
            ).strip()

        def show_field() -> None:
            key, label, multiline = fields[index]
            header.set_text(f"{label} ({index + 1}/{len(fields)})")
            local_value = data.get(key, "")
            remote_value = anidb_data.get(key, "")
            local_buffer.set_text(local_value)
            remote_buffer.set_text(remote_value)
            if multiline:
                local_view.set_size_request(-1, 180)
                remote_view.set_size_request(-1, 180)
            else:
                local_view.set_size_request(-1, 80)
                remote_view.set_size_request(-1, 80)
            prev_button.set_sensitive(index > 0)
            next_button.set_label("Завершить" if index == len(fields) - 1 else "Далее")

        def copy_current() -> None:
            key, _, _ = fields[index]
            data[key] = anidb_data.get(key, "")
            local_buffer.set_text(data[key])

        def copy_all() -> None:
            if not self._confirm(
                "Скопировать всё", "Данные тайтла будут перезаписаны целиком! Продолжить?"
            ):
                return
            for key, _, _ in fields:
                data[key] = anidb_data.get(key, "")
            show_field()

        def go_prev() -> None:
            nonlocal index
            read_local()
            if index > 0:
                index -= 1
                show_field()

        def go_next() -> None:
            nonlocal index
            read_local()
            if index < len(fields) - 1:
                index += 1
                show_field()
            else:
                dialog.response(Gtk.ResponseType.OK)

        copy_button.connect("clicked", lambda _b: copy_current())
        copy_all_button.connect("clicked", lambda _b: copy_all())
        prev_button.connect("clicked", lambda _b: go_prev())
        next_button.connect("clicked", lambda _b: go_next())

        show_field()
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return False, data
        action = "импортировать" if is_import else "сохранить изменения"
        if not self._confirm("Сохранение", f"Хотите {action}?"):
            return False, data
        return True, data

    def _format_date(self, value: str | None) -> str:
        if not value:
            return ""
        return value.split(" ")[0]

    # Реакция на изменение основного названия тайтла.
    def on_main_title_changed(self, _entry) -> None:
        self._mark_dirty()
        self._update_save_state()

    # Обновляем доступность кнопки сохранения и сообщение об ошибке.
    def _update_save_state(self) -> bool:
        name = self.main_title.get_text().strip()
        if not name:
            self.save_button.set_sensitive(False)
            self.name_warning_label.set_text("")
            return False
        duplicate = self.db.title_exists(name, exclude_id=self.current_title_id)
        if duplicate:
            self.save_button.set_sensitive(False)
            self.name_warning_label.set_markup(
                "<span foreground='red'>Название уже существует.</span>"
            )
            return False
        self.name_warning_label.set_text("")
        self.save_button.set_sensitive(True)
        return True

    def _mark_dirty(self) -> None:
        if not self.is_dirty:
            self.is_dirty = True

    def _clear_dirty(self) -> None:
        self.is_dirty = False

    def _prompt_unsaved(self, action: str) -> str:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Есть несохраненные изменения.",
        )
        dialog.format_secondary_text(
            f"Сохранить изменения перед тем как {action}?"
        )
        dialog.add_button("Сохранить", Gtk.ResponseType.YES)
        dialog.add_button("Не сохранять", Gtk.ResponseType.NO)
        dialog.add_button("Отмена", Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            return "save"
        if response == Gtk.ResponseType.NO:
            return "discard"
        return "cancel"

    def _select_current_title_in_list(self) -> None:
        if not self.current_title_id:
            return
        if not hasattr(self, "title_rows"):
            return
        try:
            index = self.title_rows.index(self.current_title_id)
        except ValueError:
            return
        row = self.title_list.get_row_at_index(index)
        if row:
            self.title_list.select_row(row)

    def _on_delete_event(self, *_args) -> bool:
        if self.is_dirty:
            choice = self._prompt_unsaved("закрыть приложение")
            if choice == "save":
                if not self.save_title():
                    return True
            elif choice == "cancel":
                return True
        return False

    # Диалог выбора нескольких файлов.
    def _pick_files(self, title: str, mime_types: list) -> list:
        dialog = Gtk.FileChooserDialog(
            title=title, transient_for=self, action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Выбрать", Gtk.ResponseType.OK)
        dialog.set_select_multiple(True)
        filter_media = Gtk.FileFilter()
        for mime in mime_types:
            filter_media.add_mime_type(mime)
        dialog.add_filter(filter_media)
        paths = []
        if dialog.run() == Gtk.ResponseType.OK:
            for filename in dialog.get_filenames():
                if filename:
                    paths.append(filename)
        dialog.destroy()
        return paths

    # Открыть диалог подробной информации о видеофайле.
    def _open_video_details_dialog(self, media_id: int, media_path: str) -> None:
        media_row = self._get_media_row(media_id)
        if not media_row:
            return
        dialog = Gtk.Dialog(
            title=f"Детали {os.path.basename(media_path)}", transient_for=self, modal=True
        )
        dialog.add_button("Закрыть", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(900, 700)
        content = dialog.get_content_area()
        content.set_spacing(8)

        # Верхняя зона: миниатюра слева, путь и свойства по центру, изображения справа.
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        left_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        center_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top_row.pack_start(left_column, False, False, 0)
        top_row.pack_start(center_column, True, True, 0)
        top_row.pack_start(right_column, True, True, 0)
        content.pack_start(top_row, True, True, 0)

        # Миниатюра видео и кнопки управления.
        thumb_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        thumb_label = Gtk.Label(label="Миниатюра")
        thumb_label.set_xalign(0)
        thumb_image = Gtk.Image()
        thumb_image.set_size_request(350, 260)
        thumb_event = Gtk.EventBox()
        thumb_event.add(thumb_image)
        thumb_path = media_row["thumbnail_path"] or ""
        if thumb_path and os.path.exists(thumb_path):
            thumb_image.set_from_pixbuf(self._load_pixbuf(thumb_path))
        thumb_event.connect(
            "button-press-event",
            lambda _w, _e: self._open_default_if_exists(thumb_path),
        )
        thumb_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        load_thumb = Gtk.Button(label="Загрузить миниатюру")
        gen_thumb = Gtk.Button(label="Сгенерировать")
        thumb_buttons.pack_start(load_thumb, False, False, 0)
        thumb_buttons.pack_start(gen_thumb, False, False, 0)
        thumb_box.pack_start(thumb_label, False, False, 0)
        thumb_box.pack_start(thumb_event, False, False, 0)
        thumb_box.pack_start(thumb_buttons, False, False, 0)
        left_column.pack_start(thumb_box, False, False, 0)

        # Полный путь и кнопка открытия каталога.
        path_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        path_entry = Gtk.Entry()
        path_entry.set_text(media_path)
        path_entry.set_editable(False)
        open_dir = Gtk.Button(label="Открыть каталог")
        open_dir.connect("clicked", lambda _b: self._open_folder(media_path))
        path_row.pack_start(Gtk.Label(label="Путь", xalign=0), False, False, 0)
        path_row.pack_start(path_entry, True, True, 0)
        path_row.pack_start(open_dir, False, False, 0)
        center_column.pack_start(path_row, False, False, 0)

        def set_thumbnail(path_value: str) -> None:
            nonlocal thumb_path
            thumb_path = self._cache_image(path_value)
            if thumb_path and os.path.exists(thumb_path):
                thumb_image.set_from_pixbuf(self._load_pixbuf(thumb_path))

        load_thumb.connect(
            "clicked",
            lambda _b: self._pick_thumbnail_for_video(set_thumbnail),
        )
        gen_thumb.connect(
            "clicked",
            lambda _b: self._generate_video_thumbnail(media_path, set_thumbnail),
        )

        # Детальная информация по дорожкам.
        tracks_frame = Gtk.Frame(label="Дорожки и свойства")
        tracks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tracks_box.set_margin_top(6)
        tracks_box.set_margin_bottom(6)
        tracks_box.set_margin_start(6)
        tracks_box.set_margin_end(6)
        tracks_frame.add(tracks_box)
        tracks_store = Gtk.ListStore(str, str, str, str, str, str, int, str, str, bool, str)
        tracks_view = Gtk.TreeView(model=tracks_store)
        columns = [
            ("Тип", 0),
            ("Название", 1),
            ("Язык", 2),
            ("Формат", 3),
            ("Разрешение", 4),
            ("Битрейт", 5),
            ("CodecID", 7),
            ("Кодировка", 8),
        ]
        for title, idx in columns:
            renderer = Gtk.CellRendererText()
            if idx == 2:
                renderer.set_property("editable", True)
                renderer.connect(
                    "edited",
                    lambda _r, path_str, new_text: self._on_track_language_edit(
                        tracks_store, path_str, new_text, media_id
                    ),
                )
            column = Gtk.TreeViewColumn(title, renderer, text=idx)
            tracks_view.append_column(column)
        hardsub_renderer = Gtk.CellRendererToggle()
        hardsub_renderer.connect(
            "toggled",
            lambda _r, path_str: self._on_track_hardsub_toggle(
                tracks_store, path_str, media_id
            ),
        )
        tracks_view.append_column(Gtk.TreeViewColumn("Хардсаб", hardsub_renderer, active=9))
        hs_lang_renderer = Gtk.CellRendererText()
        hs_lang_renderer.set_property("editable", True)
        hs_lang_renderer.connect(
            "edited",
            lambda _r, path_str, new_text: self._on_track_hardsub_lang_edit(
                tracks_store, path_str, new_text, media_id
            ),
        )
        tracks_view.append_column(Gtk.TreeViewColumn("Язык хардсаба", hs_lang_renderer, text=10))

        tracks_scroller = Gtk.ScrolledWindow()
        tracks_scroller.set_vexpand(True)
        tracks_scroller.set_size_request(-1, 260)
        tracks_scroller.add(tracks_view)
        tracks_box.pack_start(tracks_scroller, True, True, 0)
        center_column.pack_start(tracks_frame, True, True, 0)

        self._populate_tracks_store(tracks_store, media_id, media_path)

        # Список изображений видеофайла.
        video_images_frame = Gtk.Frame(label="Изображения видеофайла")
        video_images_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        video_images_box.set_margin_top(6)
        video_images_box.set_margin_bottom(6)
        video_images_box.set_margin_start(6)
        video_images_box.set_margin_end(6)
        video_images_frame.add(video_images_box)
        video_images_store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, int)
        video_images_view = Gtk.IconView.new()
        video_images_view.set_model(video_images_store)
        video_images_view.set_pixbuf_column(0)
        video_images_view.set_columns(1)
        video_images_view.set_reorderable(True)
        video_images_view.connect(
            "item-activated",
            lambda _v, tree_path: self._open_default_for_store_path(
                video_images_store, tree_path
            ),
        )
        video_images_view.connect(
            "button-press-event",
            lambda _v, event: self._video_images_menu(
                event, video_images_view, video_images_store, media_id
            ),
        )
        video_images_store.connect(
            "rows-reordered",
            lambda _m, _p, _i, _n: self._persist_video_image_order(
                video_images_store, media_id
            ),
        )
        images_scroller = Gtk.ScrolledWindow()
        images_scroller.set_vexpand(True)
        images_scroller.add(video_images_view)
        video_images_box.pack_start(images_scroller, True, True, 0)
        add_video_image = Gtk.Button(label="Добавить изображение")
        add_video_image.connect(
            "clicked",
            lambda _b: self._add_video_image(media_id, video_images_store),
        )
        video_images_box.pack_start(add_video_image, False, False, 0)
        right_column.pack_start(video_images_frame, True, True, 0)

        self._refresh_video_images(media_id, video_images_store)

        # Пользовательский комментарий.
        comment_frame = Gtk.Frame(label="Комментарий")
        comment_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        comment_box.set_margin_top(6)
        comment_box.set_margin_bottom(6)
        comment_box.set_margin_start(6)
        comment_box.set_margin_end(6)
        comment_frame.add(comment_box)
        comment_buffer = Gtk.TextBuffer()
        comment_buffer.set_text(media_row["comment"] or "")
        comment_view = Gtk.TextView(buffer=comment_buffer)
        comment_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        comment_scroller = Gtk.ScrolledWindow()
        comment_scroller.set_vexpand(True)
        comment_scroller.set_size_request(-1, 140)
        comment_scroller.add(comment_view)
        comment_box.pack_start(comment_scroller, True, True, 0)
        content.pack_start(comment_frame, False, False, 0)

        dialog.show_all()
        dialog.run()
        self.db.update_media_details(
            media_id,
            thumb_path,
            comment_buffer.get_text(
                comment_buffer.get_start_iter(),
                comment_buffer.get_end_iter(),
                True,
            ).strip(),
        )
        dialog.destroy()
        self.refresh_media_lists()

    def _get_media_row(self, media_id: int):
        cur = self.db.conn.cursor()
        return cur.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()

    def _open_folder(self, path_value: str) -> None:
        folder = os.path.dirname(path_value)
        Gio.AppInfo.launch_default_for_uri(f"file://{folder}", None)

    def _open_default_if_exists(self, path_value: str) -> None:
        if path_value and os.path.exists(path_value):
            Gio.AppInfo.launch_default_for_uri(f"file://{path_value}", None)

    def _open_default_for_store_path(self, store: Gtk.ListStore, tree_path) -> None:
        tree_iter = store.get_iter(tree_path)
        path_value = store.get_value(tree_iter, 1)
        self._open_default_if_exists(path_value)

    def _cache_dir(self) -> str:
        cache_dir = os.path.join(os.path.dirname(__file__), ".hsorter_cache")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    def _cache_image(self, path_value: str) -> str:
        if not path_value or not os.path.exists(path_value):
            return path_value
        cache_dir = self._cache_dir()
        abs_path = os.path.abspath(path_value)
        try:
            if os.path.commonpath([abs_path, cache_dir]) == cache_dir:
                return abs_path
        except ValueError:
            pass
        ext = os.path.splitext(abs_path)[1].lower() or ".img"
        stat_info = os.stat(abs_path)
        digest = hashlib.sha256(
            f"{abs_path}|{stat_info.st_mtime_ns}|{stat_info.st_size}".encode("utf-8")
        ).hexdigest()[:16]
        cached_path = os.path.join(cache_dir, f"image_{digest}{ext}")
        if not os.path.exists(cached_path):
            shutil.copy2(abs_path, cached_path)
        return cached_path

    def _pick_thumbnail_for_video(self, callback) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Выберите изображение миниатюры",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Выбрать", Gtk.ResponseType.OK)
        filter_images = Gtk.FileFilter()
        for mime in ("image/png", "image/jpeg", "image/bmp", "image/gif"):
            filter_images.add_mime_type(mime)
        dialog.add_filter(filter_images)
        if dialog.run() == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            if filename:
                callback(self._cache_image(filename))
        dialog.destroy()

    def _generate_video_thumbnail(self, video_path: str, callback) -> None:
        cache_dir = self._cache_dir()
        output_path = os.path.join(
            cache_dir, f"thumb_{abs(hash(video_path)) % 100000}.jpg"
        )
        duration = self._get_video_duration(video_path)
        step = max(duration / 20, 1.0) if duration else None
        if step:
            select_expr = f"isnan(prev_selected_t)+gte(t,prev_selected_t+{step})"
            vf = f"select='{select_expr}',scale=360:-1,tile=4x5:padding=4:margin=4"
        else:
            vf = "fps=1/10,scale=360:-1,tile=4x5:padding=4:margin=4"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-vf",
            vf,
            "-frames:v",
            "1",
            output_path,
        ]
        subprocess.run(cmd, check=False)
        if os.path.exists(output_path):
            callback(output_path)

    def _get_video_duration(self, video_path: str) -> float | None:
        """Возвращает длительность видео в секундах через ffprobe."""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    video_path,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return None
        if result.returncode != 0:
            return None
        try:
            return float(result.stdout.strip())
        except ValueError:
            return None

    def _populate_tracks_store(self, store: Gtk.ListStore, media_id: int, path: str) -> None:
        store.clear()
        details = MediaInfo.get_details(path)
        overrides = self._track_overrides_map(media_id)
        for track in details.get("tracks", []):
            track_type = track.get("type", "")
            track_index = track.get("index", 0)
            override = overrides.get((track_type, track_index), {})
            language = override.get("language") or track.get("language", "")
            hardsub = bool(override.get("hardsub", False))
            hardsub_lang = override.get("hardsub_language", "")
            resolution = ""
            if track.get("width") and track.get("height"):
                resolution = f"{track.get('width')}x{track.get('height')}"
            encoding_value = track.get("encoding", "") if track_type == "Text" else ""
            store.append(
                [
                    track_type,
                    track.get("title", ""),
                    language,
                    track.get("format", ""),
                    resolution,
                    str(track.get("bit_rate", "")),
                    int(track_index),
                    track.get("codec_id", ""),
                    encoding_value,
                    hardsub,
                    hardsub_lang,
                ]
            )

    def _track_overrides_map(self, media_id: int) -> dict:
        overrides = {}
        for row in self.db.list_track_overrides(media_id):
            overrides[(row["track_type"], row["track_index"])] = {
                "language": row["language"],
                "hardsub": bool(row["hardsub"]),
                "hardsub_language": row["hardsub_language"],
            }
        return overrides

    def _on_track_language_edit(
        self, store: Gtk.ListStore, path_str: str, new_text: str, media_id: int
    ) -> None:
        tree_iter = store.get_iter(path_str)
        store.set_value(tree_iter, 2, new_text)
        track_type = store.get_value(tree_iter, 0)
        track_index = store.get_value(tree_iter, 6)
        hardsub = store.get_value(tree_iter, 9)
        hardsub_lang = store.get_value(tree_iter, 10)
        self.db.upsert_track_override(
            media_id, track_type, track_index, new_text, hardsub, hardsub_lang
        )

    def _on_track_hardsub_toggle(
        self, store: Gtk.ListStore, path_str: str, media_id: int
    ) -> None:
        tree_iter = store.get_iter(path_str)
        current = store.get_value(tree_iter, 9)
        store.set_value(tree_iter, 9, not current)
        track_type = store.get_value(tree_iter, 0)
        track_index = store.get_value(tree_iter, 6)
        language = store.get_value(tree_iter, 2)
        hardsub_lang = store.get_value(tree_iter, 10)
        self.db.upsert_track_override(
            media_id, track_type, track_index, language, not current, hardsub_lang
        )

    def _on_track_hardsub_lang_edit(
        self, store: Gtk.ListStore, path_str: str, new_text: str, media_id: int
    ) -> None:
        tree_iter = store.get_iter(path_str)
        store.set_value(tree_iter, 10, new_text)
        track_type = store.get_value(tree_iter, 0)
        track_index = store.get_value(tree_iter, 6)
        language = store.get_value(tree_iter, 2)
        hardsub = store.get_value(tree_iter, 9)
        self.db.upsert_track_override(
            media_id, track_type, track_index, language, hardsub, new_text
        )

    def _refresh_video_images(self, media_id: int, store: Gtk.ListStore) -> None:
        store.clear()
        for item in self.db.list_video_images(media_id):
            cached_path = self._cache_image(item["path"])
            if cached_path and cached_path != item["path"]:
                self.db.update_video_image_path(item["id"], cached_path)
            pixbuf = self._load_thumbnail(cached_path)
            if pixbuf:
                store.append([pixbuf, cached_path, item["id"]])

    def _add_video_image(self, media_id: int, store: Gtk.ListStore) -> None:
        paths = self._pick_files(
            "Выберите изображения", ["image/png", "image/jpeg", "image/bmp"]
        )
        for path in paths:
            cached_path = self._cache_image(path)
            self.db.add_video_image(media_id, cached_path)
        self._refresh_video_images(media_id, store)

    def _video_images_menu(
        self,
        event,
        view: Gtk.IconView,
        store: Gtk.ListStore,
        media_id: int,
    ) -> bool:
        if event.button != 3:
            return False
        path = view.get_path_at_pos(int(event.x), int(event.y))
        if not path:
            return False
        view.select_path(path)
        menu = Gtk.Menu()
        delete_item = Gtk.MenuItem(label="Удалить")
        delete_item.connect(
            "activate", lambda _i: self._delete_video_image(view, store, media_id)
        )
        menu.append(delete_item)
        menu.show_all()
        menu.popup(None, None, None, None, event.button, event.time)
        return True

    def _delete_video_image(
        self, view: Gtk.IconView, store: Gtk.ListStore, media_id: int
    ) -> None:
        paths = view.get_selected_items()
        if not paths:
            return
        tree_iter = store.get_iter(paths[0])
        image_id = store.get_value(tree_iter, 2)
        if image_id:
            self.db.delete_video_image(image_id)
            self._refresh_video_images(media_id, store)

    def _persist_video_image_order(self, store: Gtk.ListStore, media_id: int) -> None:
        image_ids = [row[2] for row in store if row[2] is not None]
        if image_ids:
            self.db.update_video_image_order(image_ids)

    # Универсальное инфо-сообщение.
    def _message(self, title: str, body: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(body)
        dialog.run()
        dialog.destroy()

    # Диалог подтверждения.
    def _confirm(self, title: str, body: str) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=title,
        )
        dialog.format_secondary_text(body)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.YES

    # Загрузка размеров окна и позиций разделителей.
    def _load_window_settings(self) -> None:
        size_value = self.db.get_setting("window_size")
        if size_value:
            try:
                width, height = json.loads(size_value)
                self.resize(int(width), int(height))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        self._pending_window_state = self.db.get_setting("window_state")
        show_status = self.db.get_setting("list_show_status")
        if show_status is not None:
            self.show_status.set_active(show_status == "1")
        sort_value = self.db.get_setting("list_sort")
        if sort_value:
            self.filter_sort.set_active_id(sort_value)
        pos_value = self.db.get_setting("window_position")
        if pos_value:
            try:
                x_pos, y_pos = json.loads(pos_value)
                self.move(int(x_pos), int(y_pos))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        GLib.idle_add(self._apply_pane_ratio)

    # Сохранение размеров окна и позиций разделителей.
    def _save_window_settings(self, *_args) -> None:
        width, height = self.get_size()
        self.db.set_setting("window_size", json.dumps([width, height]))
        allocation = self.get_allocation()
        self.db.set_setting("window_position", json.dumps([allocation.x, allocation.y]))
        self.db.set_setting("list_show_status", "1" if self.show_status.get_active() else "0")
        self.db.set_setting("list_sort", self.filter_sort.get_active_id() or "title")
        state = "normal"
        if self.is_maximized():
            state = "maximized"
        else:
            window = self.get_window()
            if window and window.get_state() & Gdk.WindowState.ICONIFIED:
                state = "minimized"
        self.db.set_setting("window_state", state)

    # Ограничиваем позиции разделителей, чтобы элементы не сжимались слишком сильно.
    def _clamp_panes(self) -> bool:
        self._apply_pane_ratio()
        return False

    # Дополнительная защита: клемпим после изменения размеров окна (с задержкой).
    def _on_configure_event(self, *_args) -> None:
        if getattr(self, "_clamp_timeout_id", None):
            return
        self._clamp_timeout_id = GLib.timeout_add(120, self._finish_clamp)

    def _finish_clamp(self) -> bool:
        self._clamp_timeout_id = None
        self._apply_pane_ratio()
        return False

    def _apply_pane_ratio(self) -> bool:
        allocation = self.get_allocation()
        total_width = allocation.width if allocation.width else self.get_size()[0]
        if total_width <= 0:
            return False
        main_pos = int(total_width * 0.2)
        right_total = total_width - main_pos
        right_pos = int(right_total * 0.75)
        self.main_paned.set_position(main_pos)
        self.right_paned.set_position(right_pos)
        return False

    # Применяем состояние окна после того, как окно создано.
    def _on_window_state_event(self, *_args) -> None:
        state = getattr(self, "_pending_window_state", None)
        if not state:
            return
        self._pending_window_state = None
        if state == "maximized":
            self.maximize()
        elif state == "minimized":
            self.iconify()
        GLib.idle_add(self._apply_pane_ratio)


# Приложение GTK.
class HSorterApp(Gtk.Application):
    # Создание приложения и БД.
    def __init__(self) -> None:
        super().__init__(application_id="com.example.hsorter")
        self.db = Database(os.path.join(os.path.dirname(__file__), "hsorter.sqlite"))

    # Показываем главное окно.
    def do_activate(self) -> None:
        win = HSorterWindow(self, self.db)
        win.show_all()


# Создание и запуск GTK приложения.
def main() -> None:
    app = HSorterApp()
    app.run(None)


if __name__ == "__main__":
    main()
