import datetime
import json
import os
import sqlite3
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, Gtk


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
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                path TEXT NOT NULL,
                info TEXT DEFAULT "",
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY(title_id) REFERENCES titles(id) ON DELETE CASCADE
            )
            """
        )
        if not self._column_exists("media", "sort_order"):
            cur.execute("ALTER TABLE media ADD COLUMN sort_order INTEGER DEFAULT 0")
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


# Главное окно приложения с тремя панелями.
class HSorterWindow(Gtk.ApplicationWindow):
    # Инициализация окна и построение интерфейса.
    def __init__(self, app: Gtk.Application, db: Database) -> None:
        super().__init__(application=app, title="HSorter")
        self.set_default_size(1400, 900)
        self.db = db
        self.current_title_id = None
        self.cover_path = ""

        self._build_ui()
        self.refresh_titles()

    # Общая разметка: три колонки.
    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        root.set_margin_top(10)
        root.set_margin_bottom(10)
        root.set_margin_start(10)
        root.set_margin_end(10)
        self.add(root)

        self.library_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.media_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        root.pack_start(self.library_box, True, True, 0)
        root.pack_start(self.details_box, True, True, 0)
        root.pack_start(self.media_box, True, True, 0)

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
        self.alt_titles = Gtk.Entry()
        title_column.pack_start(self._row("Основное название", self.main_title), False, False, 0)
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

        save_button = Gtk.Button(label="Сохранить изменения")
        save_button.connect("clicked", lambda _b: self.save_title())
        self.details_box.pack_start(save_button, False, False, 0)

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
        data = self.collect_form_data()
        if not data["main_title"]:
            self._message("Нужно название", "Введите основное название тайтла.")
            return
        title_id = self.db.add_title(data)
        self.refresh_titles()
        self.load_title(title_id)

    # Сохранить изменения.
    def save_title(self) -> None:
        if not self.current_title_id:
            self._message("Нет тайтла", "Выберите тайтл в списке.")
            return
        data = self.collect_form_data()
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
