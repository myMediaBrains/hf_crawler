"""
seed_my_interests.py
---------------------
1회성 스크립트 - 사용자가 직접 정리한 관심사 목록을 /genres API로 한 번에
등록한다. 이후로는 별도 조치 없이 scheduler.py의 백그라운드 틱이 30분마다
알아서 점검하며 수집을 이어간다 (MAX_KEYWORDS_PER_TICK=5로 속도 조절되고,
사람이 다른 작업을 하면 우선권을 양보하도록 이미 구성돼 있음).

실행 전: 8000번 서버가 떠 있어야 함.
실행: python seed_my_interests.py

각 항목은 (대분류, 중분류/태그이름, 영어 검색문구) 튜플.
- 대분류: Politics/Economy는 Tag.sensitive=True로 등록해야 하지만, 지금
  /genres API는 sensitive 파라미터를 아직 안 받는다 (장르편집기 UI도 마찬가지).
  일단 전부 sensitive=False로 등록되고, 필요하면 나중에 DB에서 직접
  UPDATE tags SET sensitive=1 WHERE major_category IN ('Politics','Economy')
  로 한 번에 처리하거나, /genres에 sensitive 파라미터를 추가해서 재등록하면 됨.
"""

import time
import requests

API_BASE = "http://localhost:8000"

# (대분류, 중분류=Tag 이름, 검색문구)
INTERESTS = [
    ("AI", "AI", "artificial intelligence news"),
    ("Life", "Cooking", "cooking recipes food trends"),
    ("Life", "Travel", "travel destinations guide"),
    ("Sports", "Sports", "sports news highlights"),
    ("Culture", "History", "history articles documentary"),
    ("Politics", "Politics", "politics news"),
    ("Economy", "Economy", "economy market news"),
    ("Culture", "Books", "novel bestseller fiction books"),
    ("Culture", "Music", "music new releases billboard"),
    ("Life", "Lifestyle", "lifestyle trends"),
    ("Entertainment", "Movies", "movie reviews new releases"),
    ("Entertainment", "TV Drama", "tv drama series review"),
    ("Entertainment", "Documentary", "documentary film review"),
    ("Tech", "Quantum Computing", "quantum computing news"),
    ("Economy", "Bitcoin", "bitcoin cryptocurrency news"),
    ("Economy", "Stock Market", "stock market news"),
    ("Tech", "IT Companies", "tech company news"),
    ("Tech", "Software", "software development news"),
    ("Health", "Senior Health", "senior health elderly wellness"),
    ("Health", "Diabetes", "diabetes management news"),
    ("Health", "Fitness", "fitness health news"),
    ("Life", "Food", "food trends cuisine"),
]


def main():
    success, failed = 0, 0
    for major, mid, search_query in INTERESTS:
        try:
            resp = requests.post(
                f"{API_BASE}/genres",
                json={
                    "major_category": major,
                    "mid_category": mid,
                    "sub_category": mid,   # Tag 이름 = 중분류 이름으로 통일 (짧고 정규화된 형태 유지)
                    "search_query": search_query,
                    "months_back": 1,
                    "interval_hours": 24.0,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                print(f"✅ {major} > {mid}")
                success += 1
            else:
                print(f"❌ {major} > {mid} - HTTP {resp.status_code}: {resp.text[:150]}")
                failed += 1
        except Exception as e:
            print(f"❌ {major} > {mid} - 예외: {e}")
            failed += 1

        time.sleep(0.3)  # 서버에 부담 안 주려고 살짝 간격

    print(f"\n완료: 성공 {success}건, 실패 {failed}건")
    print("이제부터는 scheduler.py 백그라운드 틱이 30분마다 알아서 점검하며 수집합니다.")
    print("장르편집기에서 등록된 22개 항목을 바로 확인할 수 있습니다.")


if __name__ == "__main__":
    main()
