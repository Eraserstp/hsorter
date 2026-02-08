import datetime
import json
import os
import sqlite3
import subprocess

import gi

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
]


# Обёртка над SQLite для хранения карточек тайтлов и медиафайлов.
class Database:
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
                rating INTEGER DEFAULT 1,
                personal_rating INTEGER DEFAULT 1,
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
        self.conn.commit()

    # Проверяем наличие колонки в таблице.
    def _column_exists(self, table: str, column: str) -> bool:
        cur = self.conn.cursor()
        columns = cur.execute(f"PRAGMA table_info({table})").fetchall()
        return any(col["name"] == column for col in columns)

    # Получение списка тайтлов с фильтрами по названию, тегам и статусам.
    def list_titles(self, query: str = "", tags: str = "", status_filter: str = ""):
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
                composer, subtitles_author, voice_author, status_json, tags, cover_path
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data.get("main_title", ""),
                data.get("alt_titles", ""),
                data.get("rating", 1),
                data.get("personal_rating", 1),
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
                status_json=?,
                tags=?,
                cover_path=?
            WHERE id=?
            """,
            (
                data.get("main_title", ""),
                data.get("alt_titles", ""),
                data.get("rating", 1),
                data.get("personal_rating", 1),
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
                json.dumps(data.get("status", {}), ensure_ascii=False),
                data.get("tags", ""),
                data.get("cover_path", ""),
                title_id,
            ),
        )
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
        cur = self.conn.cursor()
        row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row:
            return row["value"]
        return default

    # Сохранить значение настройки по ключу.
    def set_setting(self, key: str, value: str) -> None:
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
    @staticmethod
    # Главная точка входа для описания видео.
    def describe_video(path: str) -> str:
        if os.path.isdir(path):
            return ""
        info = MediaInfo._from_pymediainfo(path)
        if info:
            return info
        return MediaInfo._from_cli(path)

    @staticmethod
    # Извлечение через библиотеку pymediainfo, если она установлена.
    def _from_pymediainfo(path: str) -> str:
        try:
            from pymediainfo import MediaInfo as PyMediaInfo
        except Exception:
            return ""
        try:
            media_info = PyMediaInfo.parse(path)
        except Exception:
            return ""
        parts = []
        for track in media_info.tracks:
            if track.track_type == "Video":
                parts.append(
                    f"Видео: {track.format or ''} {track.width or ''}x{track.height or ''}"
                )
            if track.track_type == "Audio":
                lang = track.language or ""
                parts.append(f"Аудио: {track.format or ''} {lang}")
            if track.track_type == "Text":
                lang = track.language or ""
                parts.append(f"Субтитры: {track.format or ''} {lang}")
        return " | ".join([p.strip() for p in parts if p.strip()])

    @staticmethod
    # Извлечение через CLI mediainfo (JSON).
    def _from_cli(path: str) -> str:
        try:
            result = subprocess.run(
                ["mediainfo", "--Output=JSON", path],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return ""
        if result.returncode != 0:
            return ""
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ""
        tracks = data.get("media", {}).get("track", [])
        parts = []
        for track in tracks:
            track_type = track.get("@type")
            if track_type == "Video":
                parts.append(
                    f"Видео: {track.get('Format', '')} {track.get('Width', '')}x{track.get('Height', '')}"
                )
            if track_type == "Audio":
                parts.append(
                    f"Аудио: {track.get('Format', '')} {track.get('Language', '')}"
                )
            if track_type == "Text":
                parts.append(
                    f"Субтитры: {track.get('Format', '')} {track.get('Language', '')}"
                )
        return " | ".join([p.strip() for p in parts if p.strip()])

    # Получаем подробную структуру из mediainfo в виде словаря.
    @staticmethod
    def get_details(path: str) -> dict:
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

        self._build_ui()
        self._load_window_settings()
        self.connect("destroy", self._save_window_settings)
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

        self._build_library()
        self._build_details()
        self._build_media()

    # Левая панель: библиотека тайтлов и фильтры.
    def _build_library(self) -> None:
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
        filter_box.pack_start(self._row("Название", self.filter_name), False, False, 0)
        filter_box.pack_start(self._row("Теги", self.filter_tags), False, False, 0)
        filter_box.pack_start(self._row("Статус", self.filter_status), False, False, 0)
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
        delete_button = Gtk.Button(label="Удалить")
        delete_button.connect("clicked", lambda _b: self.delete_title())
        buttons.pack_start(add_button, False, False, 0)
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
        self.cover_button = Gtk.Button(label="Загрузить изображение")
        self.cover_button.connect("clicked", lambda _b: self.pick_cover())
        cover_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        cover_column.pack_start(self.cover_image, False, False, 0)
        cover_column.pack_start(self.cover_button, False, False, 0)
        cover_row.pack_start(cover_column, False, False, 0)

        title_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.main_title = Gtk.Entry()
        self.main_title.connect("changed", self.on_main_title_changed)
        self.alt_titles = Gtk.Entry()
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
            check = Gtk.CheckButton(label=status)
            self.status_checks[status] = check
            status_box.pack_start(check, False, False, 0)
        status_popover.show_all()
        status_popover.hide()
        self.status_button.set_popover(status_popover)
        self.details_box.pack_start(self._row("Статус", self.status_button), False, False, 0)

        rating_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.rating_spin = Gtk.SpinButton.new_with_range(1, 10, 1)
        self.personal_rating_spin = Gtk.SpinButton.new_with_range(1, 10, 1)
        self.censored_check = Gtk.CheckButton(label="Цензура")
        rating_row.pack_start(self._row("Рейтинг", self.rating_spin), True, True, 0)
        rating_row.pack_start(
            self._row("Личный рейтинг", self.personal_rating_spin), True, True, 0
        )
        rating_row.pack_start(self.censored_check, False, False, 0)
        self.details_box.pack_start(rating_row, False, False, 0)

        year_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        current_year = datetime.date.today().year
        self.year_start = Gtk.SpinButton.new_with_range(1900, current_year + 5, 1)
        self.year_end = Gtk.SpinButton.new_with_range(1900, current_year + 5, 1)
        self.year_start.set_value(current_year)
        self.year_end.set_value(current_year)
        self.episodes_spin = Gtk.SpinButton.new_with_range(0, 10000, 1)
        self.duration_entry = Gtk.Entry()
        year_row.pack_start(self._row("Год начала", self.year_start), True, True, 0)
        year_row.pack_start(self._row("Год окончания", self.year_end), True, True, 0)
        year_row.pack_start(self._row("Эпизоды", self.episodes_spin), True, True, 0)
        year_row.pack_start(self._row("Длительность", self.duration_entry), True, True, 0)
        self.details_box.pack_start(year_row, False, False, 0)

        self.description_buffer = Gtk.TextBuffer()
        description_view = Gtk.TextView(buffer=self.description_buffer)
        description_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        description_scroller = Gtk.ScrolledWindow()
        description_scroller.set_vexpand(True)
        description_scroller.add(description_view)
        self.details_box.pack_start(
            self._section("Краткое описание", description_scroller), True, True, 0
        )

        info_grid = Gtk.Grid(column_spacing=6, row_spacing=4)
        info_grid.set_margin_top(6)
        info_grid.set_margin_bottom(6)
        info_grid.set_margin_start(6)
        info_grid.set_margin_end(6)
        info_frame = Gtk.Frame(label="Сведения")
        info_frame.add(info_grid)
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
            self.info_entries[label] = entry
            info_grid.attach(Gtk.Label(label=label, xalign=0), 0, idx, 1, 1)
            info_grid.attach(entry, 1, idx, 1, 1)
        self.details_box.pack_start(info_frame, False, False, 0)

        tags_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.tags_entry = Gtk.Entry()
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
        self.images_store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, int)
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
        column = Gtk.TreeViewColumn("Видео", renderer, text=0)
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
        )
        self.title_rows = []
        for title in titles:
            display = title["main_title"]
            if self.show_status.get_active():
                status_data = json.loads(title["status_json"] or "{}")
                enabled = [s for s, v in status_data.items() if v]
                if enabled:
                    display += f"\n  {'; '.join(enabled)}"
            row = Gtk.ListBoxRow()
            row.add(Gtk.Label(label=display, xalign=0))
            self.title_list.add(row)
            self.title_rows.append(title["id"])
        self.title_list.show_all()

    # Обработчик выбора тайтла в списке.
    def on_title_selected(self, _listbox, row: Gtk.ListBoxRow) -> None:
        if not row:
            return
        index = row.get_index()
        title_id = self.title_rows[index]
        self.load_title(title_id)

    # Загрузка данных тайтла в форму.
    def load_title(self, title_id: int) -> None:
        title = self.db.get_title(title_id)
        if not title:
            return
        self.current_title_id = title_id
        self.new_title_mode = False
        self.cover_path = title["cover_path"] or ""
        self.main_title.set_text(title["main_title"])
        self.alt_titles.set_text(title["alt_titles"])
        self.rating_spin.set_value(title["rating"])
        self.personal_rating_spin.set_value(title["personal_rating"])
        self.censored_check.set_active(bool(title["censored"]))
        self.year_start.set_value(title["year_start"] or datetime.date.today().year)
        self.year_end.set_value(title["year_end"] or datetime.date.today().year)
        self.episodes_spin.set_value(title["episodes"] or 0)
        self.duration_entry.set_text(title["total_duration"])
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
        self.tags_entry.set_text(title["tags"])
        status_data = json.loads(title["status_json"] or "{}")
        for status, check in self.status_checks.items():
            check.set_active(bool(status_data.get(status)))
        self._set_cover(self.cover_path)
        self.refresh_media_lists()
        self._update_save_state()

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
        self.new_title_mode = True
        self.current_title_id = None
        self.clear_form()
        self.main_title.grab_focus()
        self._update_save_state()

    # Сохранить изменения.
    def save_title(self) -> None:
        if not self._update_save_state():
            return
        data = self.collect_form_data()
        if self.new_title_mode:
            title_id = self.db.add_title(data)
            self.new_title_mode = False
            self.refresh_titles()
            self.load_title(title_id)
        else:
            if not self.current_title_id:
                self._message("Нет тайтла", "Выберите тайтл в списке.")
                return
            self.db.update_title(self.current_title_id, data)
            self.refresh_titles()

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
            "rating": int(self.rating_spin.get_value()),
            "personal_rating": int(self.personal_rating_spin.get_value()),
            "censored": self.censored_check.get_active(),
            "year_start": int(self.year_start.get_value()),
            "year_end": int(self.year_end.get_value()),
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
            "status": status_data,
            "tags": self.tags_entry.get_text().strip(),
            "cover_path": self.cover_path,
        }

    # Очистить форму, если тайтл удалён или не выбран.
    def clear_form(self) -> None:
        self.main_title.set_text("")
        self.alt_titles.set_text("")
        self.rating_spin.set_value(1)
        self.personal_rating_spin.set_value(1)
        self.censored_check.set_active(False)
        year = datetime.date.today().year
        self.year_start.set_value(year)
        self.year_end.set_value(year)
        self.episodes_spin.set_value(0)
        self.duration_entry.set_text("")
        self.description_buffer.set_text("")
        for entry in self.info_entries.values():
            entry.set_text("")
        for check in self.status_checks.values():
            check.set_active(False)
        self.tags_entry.set_text("")
        self.cover_path = ""
        self.cover_image.clear()
        self.images_store.clear()
        self.videos_store.clear()
        self.name_warning_label.set_text("")
        self.save_button.set_sensitive(False)

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
                self.cover_path = filename
                self._set_cover(self.cover_path)
        dialog.destroy()

    # Обработка drop для обложки.
    def on_cover_drop(self, _widget, _context, _x, _y, data, _info, _time) -> None:
        path = self._first_path_from_drop(data)
        if path:
            self.cover_path = path
            self._set_cover(path)

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
            pixbuf = self._load_thumbnail(item["path"])
            if pixbuf:
                self.images_store.append([pixbuf, item["path"], item["id"]])
        for item in self.db.list_media(self.current_title_id, "video"):
            text = os.path.basename(item["path"])
            if item["info"]:
                text += f"\n  {item['info']}"
            self.videos_store.append([text, item["path"], item["id"]])
        self.images_view.show_all()
        self.videos_view.show_all()

    # Добавление изображений к тайтлу.
    def add_image(self) -> None:
        if not self.current_title_id:
            self._message("Нет тайтла", "Сначала выберите тайтл.")
            return
        paths = self._pick_files("Выберите изображения", ["image/png", "image/jpeg", "image/bmp"])
        for path in paths:
            self.db.add_media(self.current_title_id, "image", path, "")
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
            self.db.add_media(self.current_title_id, "image", path, "")
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
        media_id = self.images_store.get_value(tree_iter, 2)
        if media_id:
            self.db.delete_media(media_id)
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

    # Реакция на изменение основного названия тайтла.
    def on_main_title_changed(self, _entry) -> None:
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
            thumb_path = path_value
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
                callback(filename)
        dialog.destroy()

    def _generate_video_thumbnail(self, video_path: str, callback) -> None:
        cache_dir = os.path.join(os.path.dirname(__file__), ".hsorter_cache")
        os.makedirs(cache_dir, exist_ok=True)
        output_path = os.path.join(
            cache_dir, f"thumb_{abs(hash(video_path)) % 100000}.jpg"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-vf",
            "fps=1/10,scale=350:260,tile=4x5",
            "-frames:v",
            "1",
            output_path,
        ]
        subprocess.run(cmd, check=False)
        if os.path.exists(output_path):
            callback(output_path)

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
            pixbuf = self._load_thumbnail(item["path"])
            if pixbuf:
                store.append([pixbuf, item["path"], item["id"]])

    def _add_video_image(self, media_id: int, store: Gtk.ListStore) -> None:
        paths = self._pick_files(
            "Выберите изображения", ["image/png", "image/jpeg", "image/bmp"]
        )
        for path in paths:
            self.db.add_video_image(media_id, path)
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
        main_pos = self.db.get_setting("main_paned_pos")
        if main_pos:
            try:
                self.main_paned.set_position(int(main_pos))
            except ValueError:
                pass
        right_pos = self.db.get_setting("right_paned_pos")
        if right_pos:
            try:
                self.right_paned.set_position(int(right_pos))
            except ValueError:
                pass
        self._pending_window_state = self.db.get_setting("window_state")
        GLib.idle_add(self._clamp_panes)

    # Сохранение размеров окна и позиций разделителей.
    def _save_window_settings(self, *_args) -> None:
        width, height = self.get_size()
        self.db.set_setting("window_size", json.dumps([width, height]))
        if not self.is_maximized():
            self.db.set_setting("main_paned_pos", str(self.main_paned.get_position()))
            self.db.set_setting("right_paned_pos", str(self.right_paned.get_position()))
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
        allocation = self.get_allocation()
        total_width = allocation.width if allocation.width else self.get_size()[0]
        min_left = 100
        min_center = 100
        min_right = 100
        min_main = min_left
        min_right_pane_total = min_center + min_right
        max_main = max(min_main, total_width - min_right_pane_total)
        main_pos = self.main_paned.get_position()
        clamped_main = max(min_main, min(max_main, main_pos))
        if clamped_main != main_pos:
            self.main_paned.set_position(clamped_main)
        right_total = total_width - clamped_main
        min_right_pos = min_center
        max_right_pos = max(min_right_pos, right_total - min_right)
        right_pos = self.right_paned.get_position()
        clamped_right = max(min_right_pos, min(max_right_pos, right_pos))
        if clamped_right != right_pos:
            self.right_paned.set_position(clamped_right)
        return False

    # Дополнительная защита: клемпим после изменения размеров окна (с задержкой).
    def _on_configure_event(self, *_args) -> None:
        if getattr(self, "_clamp_timeout_id", None):
            return
        self._clamp_timeout_id = GLib.timeout_add(120, self._finish_clamp)

    def _finish_clamp(self) -> bool:
        self._clamp_timeout_id = None
        self._clamp_panes()
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
        GLib.idle_add(self._clamp_panes)


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
