import argparse
import datetime
import json
import os
import sqlite3
import sys
import xml.etree.ElementTree as ET


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".mpg", ".mpeg"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import AMC XML into hsorter SQLite")
    parser.add_argument("xml_path", help="Path to AMC XML export")
    parser.add_argument(
        "db_path",
        nargs="?",
        default=os.path.join(os.path.dirname(__file__), "hsorter.sqlite"),
        help="Path to hsorter.sqlite (default: ./hsorter.sqlite)",
    )
    parser.add_argument(
        "--dubs",
        action="store_true",
        help="Write duplicate titles to dublicates.csv",
    )
    args = parser.parse_args()
    xml_path = os.path.abspath(args.xml_path)
    db_path = os.path.abspath(args.db_path)
    if not os.path.exists(xml_path):
        print(f"XML not found: {xml_path}")
        return 1
    ensure_schema(db_path)
    tree = ET.parse(xml_path)
    root = tree.getroot()
    xml_dir = os.path.dirname(xml_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    imported = 0
    skipped = 0
    duplicates = []
    for movie in root.findall(".//Movie"):
        data = extract_movie(movie, xml_dir)
        if not data["main_title"]:
            skipped += 1
            continue
        if title_exists(conn, data["main_title"]):
            duplicates.append(data["main_title"])
            print(f"Duplicate skipped: {data['main_title']}")
            skipped += 1
            continue
        title_id = insert_title(conn, data)
        print(f"Imported: {data['main_title']}")
        for image_path in data["images"]:
            add_media(conn, title_id, "image", image_path, "")
        if data["video_files"]:
            for video_path in data["video_files"]:
                add_media(conn, title_id, "video", video_path, "")
        else:
            print(f"Missing files: {data['main_title']}")
        imported += 1
    conn.close()
    if args.dubs and duplicates:
        write_duplicates(duplicates)
    print(f"Imported: {imported}, skipped: {skipped}")
    return 0


def ensure_schema(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
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
    conn.commit()
    conn.close()


def title_exists(conn: sqlite3.Connection, title: str) -> bool:
    row = conn.execute("SELECT 1 FROM titles WHERE main_title=? LIMIT 1", (title,)).fetchone()
    return row is not None


def insert_title(conn: sqlite3.Connection, data: dict) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO titles (
            main_title, alt_titles, rating, personal_rating, censored,
            year_start, year_end, episodes, total_duration, description,
            country, production, director, character_designer, author,
            composer, subtitles_author, voice_author, title_comment,
            url, created_at, updated_at, status_json, tags, cover_path
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            data["main_title"],
            data["alt_titles"],
            data["rating"],
            data["personal_rating"],
            1 if data["censored"] else 0,
            data["year_start"],
            data["year_end"],
            data["episodes"],
            data["total_duration"],
            data["description"],
            data["country"],
            data["production"],
            data["director"],
            data["character_designer"],
            data["author"],
            data["composer"],
            data["subtitles_author"],
            data["voice_author"],
            data["title_comment"],
            data["url"],
            data["created_at"],
            data["updated_at"],
            data["status_json"],
            data["tags"],
            data["cover_path"],
        ),
    )
    conn.commit()
    return cur.lastrowid


def add_media(conn: sqlite3.Connection, title_id: int, media_type: str, path: str, info: str) -> None:
    cur = conn.cursor()
    sort_order = next_media_order(conn, title_id, media_type)
    cur.execute(
        "INSERT INTO media (title_id, media_type, path, info, sort_order) VALUES (?,?,?,?,?)",
        (title_id, media_type, path, info, sort_order),
    )
    conn.commit()


def next_media_order(conn: sqlite3.Connection, title_id: int, media_type: str) -> int:
    row = conn.execute(
        "SELECT MAX(sort_order) AS max_order FROM media WHERE title_id=? AND media_type=?",
        (title_id, media_type),
    ).fetchone()
    return (row[0] or 0) + 1


def extract_movie(movie: ET.Element, xml_dir: str) -> dict:
    original_title = movie.get("OriginalTitle") or ""
    formatted_title = movie.get("FormattedTitle") or ""
    director = movie.get("Director") or ""
    producer = movie.get("Producer") or ""
    writer = movie.get("Writer") or ""
    year = movie.get("Year") or ""
    length = movie.get("Length") or ""
    url = movie.get("URL") or ""
    date_added = movie.get("Date") or ""
    file_path = movie.get("FilePath") or ""
    picture = movie.get("Picture") or ""
    custom_fields = movie.find("CustomFields")
    tags = ""
    censored = False
    episodes = None
    fangroup = ""
    if custom_fields is not None:
        tags = (custom_fields.get("tags") or "").strip()
        censored = (custom_fields.get("censor") or "").lower() == "true"
        fangroup = (custom_fields.get("fangroup") or "").strip()
        episodes_value = custom_fields.get("episodes")
        try:
            episodes = int(episodes_value) if episodes_value else None
        except ValueError:
            episodes = None
    created_at = (
        f"{date_added} 00:00"
        if date_added
        else datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    updated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cover_path = resolve_picture(xml_dir, picture)
    video_file = resolve_windows_path(file_path)
    video_files = []
    status_json = "{}"
    if video_file and os.path.exists(video_file):
        video_files = find_video_files(os.path.dirname(video_file))
    else:
        status_json = json_status_absent()
    status_json = merge_status_imported(status_json)
    images = []
    if cover_path:
        images.append(cover_path)
    for extra in movie.findall(".//Extras/Extra"):
        extra_pic = extra.get("EPicture") or ""
        extra_path = resolve_picture(xml_dir, extra_pic)
        if extra_path:
            images.append(extra_path)
    return {
        "main_title": original_title.strip(),
        "alt_titles": formatted_title.strip() if formatted_title != original_title else "",
        "rating": None,
        "personal_rating": None,
        "censored": censored,
        "year_start": int(year) if year.isdigit() else None,
        "year_end": None,
        "episodes": episodes or 0,
        "total_duration": length.strip(),
        "description": "",
        "country": "",
        "production": producer.strip(),
        "director": director.strip(),
        "character_designer": "",
        "author": writer.strip(),
        "composer": "",
        "voice_author": "",
        "title_comment": "",
        "url": url.strip(),
        "created_at": created_at,
        "updated_at": updated_at,
        "status_json": status_json,
        "tags": tags,
        "cover_path": cover_path or "",
        "video_files": video_files,
        "images": images,
    }


def resolve_windows_path(path_value: str) -> str:
    if not path_value:
        return ""
    normalized = path_value.replace("\\", "/")
    if normalized.lower().startswith("z:/"):
        normalized = normalized[2:]
    return normalized


def resolve_picture(xml_dir: str, relative_path: str) -> str:
    if not relative_path:
        return ""
    path = relative_path.replace("\\", "/")
    full_path = os.path.abspath(os.path.join(xml_dir, path))
    return full_path if os.path.exists(full_path) else ""


def find_video_files(directory: str) -> list[str]:
    if not directory or not os.path.isdir(directory):
        return []
    videos = []
    for entry in os.listdir(directory):
        full_path = os.path.join(directory, entry)
        if not os.path.isfile(full_path):
            continue
        ext = os.path.splitext(entry)[1].lower()
        if ext in VIDEO_EXTENSIONS:
            videos.append(full_path)
    return sorted(videos)


def json_status_absent() -> str:
    return json.dumps({"отсутствует": True}, ensure_ascii=False)


def merge_status_imported(status_json: str) -> str:
    try:
        data = json.loads(status_json) if status_json else {}
    except json.JSONDecodeError:
        data = {}
    data["импортировано"] = True
    return json.dumps(data, ensure_ascii=False)


def write_duplicates(duplicates: list[str]) -> None:
    with open("dublicates.csv", "w", encoding="utf-8") as handle:
        handle.write("title\n")
        for title in sorted(set(duplicates)):
            handle.write(f"{title}\n")


if __name__ == "__main__":
    raise SystemExit(main())
