# database.py
from sqlmodel import SQLModel, create_engine, Session
from typing import Generator
import os

# 환경 변수에서 DB 이름 가져오기 (없으면 기본값)
DB_NAME = os.getenv("DB_NAME", "local_deep_trend.db")
DATABASE_URL = f"sqlite:///./{DB_NAME}"

# SQLAlchemy 엔진 생성 (check_same_thread=False는 SQLite에서 필수)
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False  # True로 설정하면 SQL 로그 확인 가능
)

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