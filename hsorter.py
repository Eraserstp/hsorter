import datetime
import json
import os
import sqlite3
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


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


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

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
                FOREIGN KEY(title_id) REFERENCES titles(id) ON DELETE CASCADE
            )
            """
        )
        self.conn.commit()

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

    def get_title(self, title_id: int):
        cur = self.conn.cursor()
        return cur.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()

    def delete_title(self, title_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM titles WHERE id=?", (title_id,))
        self.conn.commit()

    def list_media(self, title_id: int, media_type: str):
        cur = self.conn.cursor()
        return cur.execute(
            "SELECT * FROM media WHERE title_id=? AND media_type=? ORDER BY id DESC",
            (title_id, media_type),
        ).fetchall()

    def add_media(self, title_id: int, media_type: str, path: str, info: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO media (title_id, media_type, path, info) VALUES (?,?,?,?)",
            (title_id, media_type, path, info),
        )
        self.conn.commit()


class MediaInfo:
    @staticmethod
    def describe_video(path: str) -> str:
        if os.path.isdir(path):
            return ""
        info = MediaInfo._from_pymediainfo(path)
        if info:
            return info
        return MediaInfo._from_cli(path)

    @staticmethod
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


class HSorterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HSorter")
        self.geometry("1400x900")
        self.db = Database(os.path.join(os.path.dirname(__file__), "hsorter.sqlite"))
        self.current_title_id = None
        self.show_status_var = tk.BooleanVar(value=True)
        self.filter_name_var = tk.StringVar()
        self.filter_tags_var = tk.StringVar()
        self.filter_status_var = tk.StringVar()
        self.cover_image = None
        self._init_ui()
        self.refresh_titles()

    def _init_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        main_pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_pane.grid(row=0, column=0, sticky="nsew")

        self.library_frame = ttk.Frame(main_pane, padding=10)
        self.details_frame = ttk.Frame(main_pane, padding=10)
        self.media_frame = ttk.Frame(main_pane, padding=10)

        main_pane.add(self.library_frame, weight=1)
        main_pane.add(self.details_frame, weight=2)
        main_pane.add(self.media_frame, weight=1)

        self._build_library()
        self._build_details()
        self._build_media()

    def _build_library(self) -> None:
        filter_box = ttk.LabelFrame(self.library_frame, text="Фильтр")
        filter_box.pack(fill=tk.X)
        ttk.Label(filter_box, text="Название").grid(row=0, column=0, sticky="w")
        ttk.Entry(filter_box, textvariable=self.filter_name_var).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Label(filter_box, text="Теги").grid(row=1, column=0, sticky="w")
        ttk.Entry(filter_box, textvariable=self.filter_tags_var).grid(
            row=1, column=1, sticky="ew", padx=4
        )
        ttk.Label(filter_box, text="Статус").grid(row=2, column=0, sticky="w")
        ttk.Entry(filter_box, textvariable=self.filter_status_var).grid(
            row=2, column=1, sticky="ew", padx=4
        )
        ttk.Button(filter_box, text="Применить", command=self.refresh_titles).grid(
            row=3, column=0, columnspan=2, pady=4
        )
        filter_box.columnconfigure(1, weight=1)

        list_frame = ttk.Frame(self.library_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=8)
        self.title_list = tk.Listbox(list_frame, height=20)
        self.title_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.title_list.bind("<<ListboxSelect>>", self.on_title_select)
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.title_list.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.title_list.configure(yscrollcommand=scroll.set)

        self.show_status_check = ttk.Checkbutton(
            self.library_frame,
            text="Показать статусы",
            variable=self.show_status_var,
            command=self.refresh_titles,
        )
        self.show_status_check.pack(anchor="w", pady=4)

        button_row = ttk.Frame(self.library_frame)
        button_row.pack(fill=tk.X, pady=4)
        ttk.Button(button_row, text="Добавить", command=self.add_title).pack(
            side=tk.LEFT
        )
        ttk.Button(button_row, text="Удалить", command=self.delete_title).pack(
            side=tk.LEFT, padx=4
        )

    def _build_details(self) -> None:
        self.details_frame.columnconfigure(1, weight=1)
        title_label = ttk.Label(self.details_frame, text="Карточка тайтла")
        title_label.grid(row=0, column=0, columnspan=2, sticky="w")

        self.cover_label = ttk.Label(
            self.details_frame,
            text="Перетащите изображение\nили нажмите",
            relief=tk.RIDGE,
            width=30,
            padding=6,
        )
        self.cover_label.grid(row=1, column=0, rowspan=4, sticky="n")
        self.cover_label.bind("<Button-1>", lambda _e: self.pick_cover())

        self._enable_dnd(self.cover_label, self.on_cover_drop)

        self.main_title_var = tk.StringVar()
        ttk.Label(self.details_frame, text="Основное название").grid(
            row=1, column=1, sticky="w"
        )
        ttk.Entry(self.details_frame, textvariable=self.main_title_var).grid(
            row=2, column=1, sticky="ew"
        )
        self.alt_titles_var = tk.StringVar()
        ttk.Label(self.details_frame, text="Дополнительные названия").grid(
            row=3, column=1, sticky="w"
        )
        ttk.Entry(self.details_frame, textvariable=self.alt_titles_var).grid(
            row=4, column=1, sticky="ew"
        )

        status_frame = ttk.LabelFrame(self.details_frame, text="Статус")
        status_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=6)
        self.status_vars = {}
        for idx, status in enumerate(STATUS_OPTIONS):
            var = tk.BooleanVar(value=False)
            self.status_vars[status] = var
            chk = ttk.Checkbutton(status_frame, text=status, variable=var)
            chk.grid(row=idx // 3, column=idx % 3, sticky="w", padx=4, pady=2)

        rating_frame = ttk.Frame(self.details_frame)
        rating_frame.grid(row=6, column=0, columnspan=2, sticky="ew")
        rating_frame.columnconfigure(1, weight=1)
        rating_frame.columnconfigure(3, weight=1)
        ttk.Label(rating_frame, text="Рейтинг").grid(row=0, column=0, sticky="w")
        self.rating_var = tk.IntVar(value=1)
        ttk.Spinbox(rating_frame, from_=1, to=10, textvariable=self.rating_var).grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Label(rating_frame, text="Личный рейтинг").grid(
            row=0, column=2, sticky="w", padx=6
        )
        self.personal_rating_var = tk.IntVar(value=1)
        ttk.Spinbox(
            rating_frame, from_=1, to=10, textvariable=self.personal_rating_var
        ).grid(row=0, column=3, sticky="ew")
        self.censored_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(rating_frame, text="Цензура", variable=self.censored_var).grid(
            row=0, column=4, padx=6
        )

        year_frame = ttk.Frame(self.details_frame)
        year_frame.grid(row=7, column=0, columnspan=2, sticky="ew", pady=4)
        current_year = datetime.date.today().year
        ttk.Label(year_frame, text="Год начала").grid(row=0, column=0, sticky="w")
        self.year_start_var = tk.IntVar(value=current_year)
        ttk.Entry(year_frame, textvariable=self.year_start_var, width=8).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(year_frame, text="Год окончания").grid(row=0, column=2, sticky="w")
        self.year_end_var = tk.IntVar(value=current_year)
        ttk.Entry(year_frame, textvariable=self.year_end_var, width=8).grid(
            row=0, column=3, sticky="w"
        )
        ttk.Label(year_frame, text="Эпизоды").grid(row=0, column=4, sticky="w")
        self.episodes_var = tk.IntVar(value=0)
        ttk.Entry(year_frame, textvariable=self.episodes_var, width=6).grid(
            row=0, column=5, sticky="w"
        )
        ttk.Label(year_frame, text="Длительность").grid(row=0, column=6, sticky="w")
        self.duration_var = tk.StringVar()
        ttk.Entry(year_frame, textvariable=self.duration_var, width=10).grid(
            row=0, column=7, sticky="w"
        )

        ttk.Label(self.details_frame, text="Краткое описание").grid(
            row=8, column=0, columnspan=2, sticky="w"
        )
        self.description_text = tk.Text(self.details_frame, height=4)
        self.description_text.grid(row=9, column=0, columnspan=2, sticky="ew")

        info_frame = ttk.Frame(self.details_frame)
        info_frame.grid(row=10, column=0, columnspan=2, sticky="ew", pady=6)
        info_frame.columnconfigure(1, weight=1)
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
        self.info_vars = []
        for idx, label in enumerate(labels):
            row = idx
            ttk.Label(info_frame, text=label).grid(row=row, column=0, sticky="w")
            var = tk.StringVar()
            self.info_vars.append(var)
            ttk.Entry(info_frame, textvariable=var).grid(row=row, column=1, sticky="ew")

        tag_frame = ttk.Frame(self.details_frame)
        tag_frame.grid(row=11, column=0, columnspan=2, sticky="ew", pady=4)
        tag_frame.columnconfigure(0, weight=1)
        self.tags_var = tk.StringVar()
        ttk.Entry(tag_frame, textvariable=self.tags_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(tag_frame, text="Добавить тег", command=self.add_tag).grid(
            row=0, column=1, padx=4
        )

        ttk.Button(
            self.details_frame, text="Сохранить изменения", command=self.save_title
        ).grid(row=12, column=0, columnspan=2, pady=6)

    def _build_media(self) -> None:
        images_frame = ttk.LabelFrame(self.media_frame, text="Изображения")
        images_frame.pack(fill=tk.BOTH, expand=True, pady=6)
        self.images_list = tk.Listbox(images_frame, height=10)
        self.images_list.pack(fill=tk.BOTH, expand=True)
        self._enable_dnd(self.images_list, self.on_images_drop)
        ttk.Button(images_frame, text="Добавить файл", command=self.add_image).pack(
            pady=4
        )

        videos_frame = ttk.LabelFrame(self.media_frame, text="Видео")
        videos_frame.pack(fill=tk.BOTH, expand=True, pady=6)
        self.videos_list = tk.Listbox(videos_frame, height=10)
        self.videos_list.pack(fill=tk.BOTH, expand=True)
        self._enable_dnd(self.videos_list, self.on_videos_drop)
        ttk.Button(videos_frame, text="Добавить файл", command=self.add_video).pack(
            pady=4
        )

    def _enable_dnd(self, widget: tk.Widget, handler) -> None:
        try:
            import tkinterdnd2 as tkdnd
        except Exception:
            return
        try:
            widget.drop_target_register(tkdnd.DND_FILES)
            widget.dnd_bind("<<Drop>>", handler)
        except Exception:
            return

    def refresh_titles(self) -> None:
        self.title_list.delete(0, tk.END)
        titles = self.db.list_titles(
            query=self.filter_name_var.get().strip(),
            tags=self.filter_tags_var.get().strip(),
            status_filter=self.filter_status_var.get().strip(),
        )
        for title in titles:
            display = title["main_title"]
            if self.show_status_var.get():
                status_data = json.loads(title["status_json"] or "{}")
                enabled = [s for s, v in status_data.items() if v]
                if enabled:
                    display += f"\n  {'; '.join(enabled)}"
            self.title_list.insert(tk.END, display)
        self.title_list_titles = [title["id"] for title in titles]

    def on_title_select(self, _event=None) -> None:
        selection = self.title_list.curselection()
        if not selection:
            return
        index = selection[0]
        title_id = self.title_list_titles[index]
        self.load_title(title_id)

    def load_title(self, title_id: int) -> None:
        title = self.db.get_title(title_id)
        if not title:
            return
        self.current_title_id = title_id
        self.cover_path = title["cover_path"] or ""
        self.main_title_var.set(title["main_title"])
        self.alt_titles_var.set(title["alt_titles"])
        self.rating_var.set(title["rating"])
        self.personal_rating_var.set(title["personal_rating"])
        self.censored_var.set(bool(title["censored"]))
        self.year_start_var.set(title["year_start"] or datetime.date.today().year)
        self.year_end_var.set(title["year_end"] or datetime.date.today().year)
        self.episodes_var.set(title["episodes"] or 0)
        self.duration_var.set(title["total_duration"])
        self.description_text.delete("1.0", tk.END)
        self.description_text.insert("1.0", title["description"])
        info_values = [
            title["country"],
            title["production"],
            title["director"],
            title["character_designer"],
            title["author"],
            title["composer"],
            title["subtitles_author"],
            title["voice_author"],
        ]
        for var, value in zip(self.info_vars, info_values):
            var.set(value)
        self.tags_var.set(title["tags"])
        status_data = json.loads(title["status_json"] or "{}")
        for status, var in self.status_vars.items():
            var.set(bool(status_data.get(status)))
        self._set_cover(self.cover_path)
        self.refresh_media_lists()

    def _set_cover(self, path: str) -> None:
        if path and os.path.exists(path):
            try:
                from PIL import Image, ImageTk
            except Exception:
                self.cover_label.configure(text=os.path.basename(path))
                return
            image = Image.open(path)
            image.thumbnail((240, 240))
            self.cover_image = ImageTk.PhotoImage(image)
            self.cover_label.configure(image=self.cover_image, text="")
        else:
            self.cover_image = None
            self.cover_label.configure(image="", text="Перетащите изображение\nили нажмите")

    def add_title(self) -> None:
        data = self.collect_form_data()
        if not data["main_title"]:
            messagebox.showwarning("Нужно название", "Введите основное название тайтла.")
            return
        title_id = self.db.add_title(data)
        self.refresh_titles()
        self.load_title(title_id)

    def save_title(self) -> None:
        if not self.current_title_id:
            messagebox.showwarning("Нет тайтла", "Выберите тайтл в списке.")
            return
        data = self.collect_form_data()
        self.db.update_title(self.current_title_id, data)
        self.refresh_titles()

    def delete_title(self) -> None:
        if not self.current_title_id:
            return
        if not messagebox.askyesno("Удалить", "Удалить выбранный тайтл?"):
            return
        self.db.delete_title(self.current_title_id)
        self.current_title_id = None
        self.refresh_titles()
        self.clear_form()

    def collect_form_data(self) -> dict:
        status_data = {status: var.get() for status, var in self.status_vars.items()}
        info_values = [var.get() for var in self.info_vars]
        return {
            "main_title": self.main_title_var.get().strip(),
            "alt_titles": self.alt_titles_var.get().strip(),
            "rating": self.rating_var.get(),
            "personal_rating": self.personal_rating_var.get(),
            "censored": self.censored_var.get(),
            "year_start": self.year_start_var.get(),
            "year_end": self.year_end_var.get(),
            "episodes": self.episodes_var.get(),
            "total_duration": self.duration_var.get().strip(),
            "description": self.description_text.get("1.0", tk.END).strip(),
            "country": info_values[0],
            "production": info_values[1],
            "director": info_values[2],
            "character_designer": info_values[3],
            "author": info_values[4],
            "composer": info_values[5],
            "subtitles_author": info_values[6],
            "voice_author": info_values[7],
            "status": status_data,
            "tags": self.tags_var.get().strip(),
            "cover_path": getattr(self, "cover_path", ""),
        }

    def clear_form(self) -> None:
        self.main_title_var.set("")
        self.alt_titles_var.set("")
        self.rating_var.set(1)
        self.personal_rating_var.set(1)
        self.censored_var.set(False)
        year = datetime.date.today().year
        self.year_start_var.set(year)
        self.year_end_var.set(year)
        self.episodes_var.set(0)
        self.duration_var.set("")
        self.description_text.delete("1.0", tk.END)
        for var in self.info_vars:
            var.set("")
        for status_var in self.status_vars.values():
            status_var.set(False)
        self.tags_var.set("")
        self.cover_path = ""
        self._set_cover("")
        self.images_list.delete(0, tk.END)
        self.videos_list.delete(0, tk.END)

    def add_tag(self) -> None:
        current = self.tags_var.get().strip()
        new_tag = simpledialog.askstring("Тег", "Введите новый тег")
        if not new_tag:
            return
        if current:
            tags = [t.strip() for t in current.split(",") if t.strip()]
            tags.append(new_tag.strip())
            self.tags_var.set(", ".join(sorted(set(tags))))
        else:
            self.tags_var.set(new_tag.strip())

    def pick_cover(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Выберите изображение",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp *.gif"), ("Все", "*")],
        )
        if file_path:
            self.cover_path = file_path
            self._set_cover(file_path)

    def on_cover_drop(self, event) -> None:
        path = self._normalize_drop_path(event.data)
        if path:
            self.cover_path = path
            self._set_cover(path)

    def _normalize_drop_path(self, data: str) -> str:
        if not data:
            return ""
        if data.startswith("{") and data.endswith("}"):
            data = data.strip("{}").split("} {")[0]
        return data.split()[0]

    def refresh_media_lists(self) -> None:
        self.images_list.delete(0, tk.END)
        self.videos_list.delete(0, tk.END)
        if not self.current_title_id:
            return
        for item in self.db.list_media(self.current_title_id, "image"):
            self.images_list.insert(tk.END, os.path.basename(item["path"]))
        for item in self.db.list_media(self.current_title_id, "video"):
            info = f"{os.path.basename(item['path'])}"
            if item["info"]:
                info += f"\n  {item['info']}"
            self.videos_list.insert(tk.END, info)

    def add_image(self) -> None:
        if not self.current_title_id:
            messagebox.showwarning("Нет тайтла", "Сначала выберите тайтл.")
            return
        paths = filedialog.askopenfilenames(
            title="Выберите изображения",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp *.gif"), ("Все", "*")],
        )
        for path in paths:
            self.db.add_media(self.current_title_id, "image", path, "")
        self.refresh_media_lists()

    def add_video(self) -> None:
        if not self.current_title_id:
            messagebox.showwarning("Нет тайтла", "Сначала выберите тайтл.")
            return
        paths = filedialog.askopenfilenames(
            title="Выберите видео",
            filetypes=[("Видео", "*.mkv *.mp4 *.avi *.mov *.wmv"), ("Все", "*")],
        )
        for path in paths:
            info = MediaInfo.describe_video(path)
            self.db.add_media(self.current_title_id, "video", path, info)
        self.refresh_media_lists()

    def on_images_drop(self, event) -> None:
        if not self.current_title_id:
            return
        for path in self._extract_paths(event.data):
            self.db.add_media(self.current_title_id, "image", path, "")
        self.refresh_media_lists()

    def on_videos_drop(self, event) -> None:
        if not self.current_title_id:
            return
        for path in self._extract_paths(event.data):
            info = MediaInfo.describe_video(path)
            self.db.add_media(self.current_title_id, "video", path, info)
        self.refresh_media_lists()

    def _extract_paths(self, data: str) -> list:
        if not data:
            return []
        if data.startswith("{"):
            parts = []
            current = ""
            in_brace = False
            for char in data:
                if char == "{":
                    in_brace = True
                    current = ""
                    continue
                if char == "}":
                    in_brace = False
                    parts.append(current)
                    continue
                if in_brace:
                    current += char
            return parts
        return data.split()


def main() -> None:
    app = HSorterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
