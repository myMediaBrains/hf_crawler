"""
migrate_db.py
기존 테이블에 새로 추가된 컬럼을 반영하는 1회성 마이그레이션.

SQLModel(SQLAlchemy)의 create_all()은 "존재하지 않는 테이블"만 새로 만들고,
이미 있는 테이블에 새 컬럼을 추가해주지는 않는다. 그래서 models.py의
Article에 raw_content/keyword/origin/model_used를 추가해도, 이미 만들어져
있던 articles 테이블에는 실제로 그 컬럼들이 생기지 않는다.

이 스크립트는 PRAGMA table_info로 실제 컬럼 목록을 확인하고, 없는 컬럼만
ALTER TABLE로 추가한다. 이미 컬럼이 있으면 아무 것도 하지 않으므로,
서버를 재시작할 때마다 호출해도 안전하다 (멱등성 보장).
"""

import sqlite3
import logging

logger = logging.getLogger(__name__)

ARTICLES_MIGRATIONS = [
    ("raw_content", "ALTER TABLE articles ADD COLUMN raw_content TEXT"),
    ("keyword", "ALTER TABLE articles ADD COLUMN keyword TEXT"),
    ("origin", "ALTER TABLE articles ADD COLUMN origin TEXT DEFAULT 'RAW_CRAWL'"),
    ("model_used", "ALTER TABLE articles ADD COLUMN model_used TEXT"),
]


def migrate(db_path: str):
    """앱 시작 시 한 번 호출한다. 이미 반영된 컬럼은 건너뛰므로 매번 호출해도 안전하다."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA table_info(articles)")
        existing_columns = {row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        conn.close()
        return

    added = []
    for column_name, sql in ARTICLES_MIGRATIONS:
        if column_name not in existing_columns:
            cursor.execute(sql)
            added.append(column_name)

    if added:
        if "keyword" in added:
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_articles_keyword ON articles (keyword)")
        conn.commit()
        logger.info(f"[migrate_db] articles 테이블에 컬럼 추가: {added}")
    else:
        logger.info("[migrate_db] articles 테이블 - 추가할 컬럼 없음 (이미 최신 상태)")

    conn.close()

SOURCES_MIGRATIONS = [
    ("block_reason", "ALTER TABLE sources ADD COLUMN block_reason TEXT"),
]

TRANSLATIONS_MIGRATIONS = [
    ("block_reason", "ALTER TABLE translations ADD COLUMN block_reason TEXT"),
]


def migrate_translations(db_path: str):
    """translations 테이블에 block_reason 컬럼을 추가한다. 멱등성 보장."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA table_info(translations)")
        existing_columns = {row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        conn.close()
        return

    added = []
    for column_name, sql in TRANSLATIONS_MIGRATIONS:
        if column_name not in existing_columns:
            cursor.execute(sql)
            added.append(column_name)

    if added:
        conn.commit()
        logger.info(f"[migrate_db] translations 테이블에 컬럼 추가: {added}")
    else:
        logger.info("[migrate_db] translations 테이블 - 추가할 컬럼 없음 (이미 최신 상태)")

    conn.close()

def migrate_sources(db_path: str):
    """sources 테이블에 block_reason 컬럼(블록리스트 사유)을 추가한다. 멱등성 보장."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA table_info(sources)")
        existing_columns = {row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        conn.close()
        return

    added = []
    for column_name, sql in SOURCES_MIGRATIONS:
        if column_name not in existing_columns:
            cursor.execute(sql)
            added.append(column_name)

    if added:
        conn.commit()
        logger.info(f"[migrate_db] sources 테이블에 컬럼 추가: {added}")
    else:
        logger.info("[migrate_db] sources 테이블 - 추가할 컬럼 없음 (이미 최신 상태)")

    conn.close()

def migrate_keyword_taxonomy(db_name: str) -> None:
    conn = sqlite3.connect(db_name)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(keywords)")
    existing_columns = {row[1] for row in cur.fetchall()}

    if "major_category" not in existing_columns:
        cur.execute("ALTER TABLE keywords ADD COLUMN major_category TEXT")

    if "mid_category" not in existing_columns:
        cur.execute("ALTER TABLE keywords ADD COLUMN mid_category TEXT")

    conn.commit()
    conn.close()