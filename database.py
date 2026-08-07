# database.py
from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import event
from typing import Generator
import os

# 환경 변수에서 DB 이름 가져오기 (없으면 기본값)
DB_NAME = os.getenv("DB_NAME", "local_deep_trend.db")
DATABASE_URL = f"sqlite:///./{DB_NAME}"

# SQLAlchemy 엔진 생성 (check_same_thread=False는 SQLite에서 필수)
# timeout=30: 다른 커넥션이 쓰기 락을 쥐고 있을 때 sqlite3 드라이버가 즉시
# "database is locked"를 던지지 않고 최대 30초까지 재시도하며 기다리게 한다.
# 이것만으로는 완전하지 않아서, 아래 PRAGMA로 WAL 모드도 함께 켠다.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    echo=False  # True로 설정하면 SQL 로그 확인 가능
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """
    커넥션이 열릴 때마다 SQLite 동시성 관련 PRAGMA를 적용한다.

    - journal_mode=WAL: 기본(rollback journal) 모드는 쓰기 중엔 읽기까지 막히는데,
      이 앱은 크롤링(쓰기)과 프론트엔드 폴링(/stats/system 등, 읽기)이 동시에
      계속 일어나는 구조라 WAL(Write-Ahead Logging)로 바꿔야 읽기가 쓰기를
      막지 않는다. 여러 프로세스/스레드가 있어도 파일 기반으로 안전하게 동작.
    - busy_timeout=30000(ms): 쓰기 락이 걸려 있을 때 즉시 실패하지 않고 최대
      30초간 재시도. 8/7 세션에서 같은 키워드를 두 작업이 동시에 수집하면서
      "database is locked" 에러가 발생한 문제의 근본 완화책.
    - synchronous=NORMAL: WAL 모드에서 권장되는 설정, FULL보다 약간 빠르면서도
      충분히 안전함 (SQLite 공식 문서 권장값).
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_db_and_tables():
    """데이터베이스 테이블 생성"""
    SQLModel.metadata.create_all(engine)
    print("✅ 데이터베이스 테이블이 생성되었습니다.")

def get_session() -> Generator[Session, None, None]:
    """FastAPI Depends용 세션 제공"""
    with Session(engine) as session:
        yield session

# 기존 데이터 마이그레이션이 필요한 경우 (선택사항)
def migrate_existing_data():
    """기존 SQLite 데이터를 SQLModel로 마이그레이션"""
    import sqlite3
    from models import Article
    
    # 기존 데이터베이스 파일이 있는지 확인
    if not os.path.exists(DB_NAME):
        print("⚠️ 기존 데이터베이스 파일이 없습니다.")
        return
    
    try:
        old_conn = sqlite3.connect(DB_NAME)
        cursor = old_conn.cursor()
        
        # 기존 테이블 확인
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='articles'")
        if not cursor.fetchone():
            print("ℹ️ 기존 articles 테이블이 없습니다.")
            old_conn.close()
            return
        
        # 데이터 마이그레이션
        cursor.execute("SELECT id, title, url, published_at, content, source FROM articles")
        rows = cursor.fetchall()
        old_conn.close()
        
        if rows:
            print(f"🔄 {len(rows)}개의 기존 데이터 마이그레이션 중...")
            with Session(engine) as session:
                for row in rows:
                    from models import Article
                    article = Article(
                        id=row[0],
                        title=row[1],
                        url=row[2],
                        published_at=row[3],
                        content=row[4] or "내용 없음",
                        source=row[5] or "Unknown"
                    )
                    session.merge(article)  # 중복 처리
                session.commit()
            print(f"✅ {len(rows)}개 데이터 마이그레이션 완료!")
    except Exception as e:
        print(f"❌ 마이그레이션 중 오류 발생: {e}")

# 이 파일이 직접 실행될 때 테스트
if __name__ == "__main__":
    create_db_and_tables()
    print("✅ 데이터베이스 설정이 완료되었습니다.")
    print(f"📁 데이터베이스 파일: {DB_NAME}")