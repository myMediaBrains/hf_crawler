# -*- coding: utf-8 -*-
"""
personalization_taxonomy.py
----------------------------
기존 main.py의 CATEGORY_CONFIG(대분류, 느슨한 키워드 매칭)를 건드리지 않고,
그 위에 "중분류" 레이어를 하나 추가한다.

목적: main.py의 CATEGORY_CONFIG는 "이 기사가 Tech인지 Politics인지" 정도의
      큰 분류만 하지만, 개인화 프로필을 만들려면 "Tech 중에서도 AI인지 개발인지"
      까지 더 촘촘하게 알아야 한다.

기존 프로젝트 8/7 수정 이력의 원칙을 그대로 따른다:
  - 수집 기사가 전부 영어이므로 키워드도 전부 영어
  - CATEGORY_CONFIG와 동일하게 (top_category, keywords) 튜플 구조 사용
  - _score_categories_for_article과 동일한 "점수제" 매칭을 재사용 (건수/목록 불일치 방지 원칙 계승)

⚠️ 코드(키) 이름은 이후 절대 바꾸지 않는다 (기존 InteractionSignal 데이터와의
   연속성이 끊어짐). 세분화가 더 필요하면 새 키를 추가만 한다.
"""

# {서브카테고리코드: (기존 CATEGORY_CONFIG의 top-level 키, 한국어 표시라벨, [영어 키워드 목록])}
SUBCATEGORY_CONFIG = {
    # --- 기존 CATEGORY_CONFIG["AI"] 하위 세분화 ---
    "AI.RESEARCH": ("AI", "AI 연구/모델", ["research", "paper", "benchmark", "training", "model release"]),
    "AI.PRODUCT": ("AI", "AI 제품/서비스", ["openai", "anthropic", "claude", "chatgpt", "gemini", "copilot"]),
    "AI.POLICY": ("AI", "AI 정책/규제", ["ai regulation", "ai policy", "ai safety", "ai act"]),

    # --- 기존 CATEGORY_CONFIG["Tech"] 하위 세분화 ---
    "TECH.DEV": ("Tech", "개발/프로그래밍", ["programming", "developer", "open source", "github", "framework"]),
    "TECH.HARDWARE": ("Tech", "하드웨어/기기", ["chip", "hardware", "device", "laptop", "smartphone"]),
    "TECH.STARTUP": ("Tech", "스타트업/산업", ["startup", "funding", "venture capital", "ipo"]),

    # --- 기존 CATEGORY_CONFIG["Politics"] 하위 세분화 (민감 카테고리) ---
    "POL.ECONOMY_POLICY": ("Politics", "경제정책 이슈", ["tax policy", "trade policy", "economic policy"]),
    "POL.FOREIGN": ("Politics", "외교/안보 이슈", ["foreign policy", "diplomacy", "national security"]),
    "POL.DOMESTIC": ("Politics", "국내정치/선거", ["election", "congress", "senate", "parliament", "vote"]),

    # --- 기존 CATEGORY_CONFIG["Economy"](CNBC Finance 등) 세분화 ---
    "ECON.MACRO": ("Economy", "거시경제", ["inflation", "interest rate", "fed", "gdp", "recession"]),
    "ECON.STOCK": ("Economy", "증권/투자", ["stock", "nasdaq", "s&p", "shares", "market rally", "sell-off"]),
    "ECON.INDUSTRY": ("Economy", "산업/기업동향", ["earnings", "revenue", "merger", "acquisition"]),

    # --- 기존 CATEGORY_CONFIG["Health"] 세분화 ---
    "HEALTH.GENERAL": ("Health", "건강 일반", ["wellness", "fitness", "nutrition"]),
    "HEALTH.MEDICAL": ("Health", "의료/질병", ["disease", "treatment", "diagnosis", "clinical"]),

    # --- 기존 CATEGORY_CONFIG["Diabetes"] (이미 세분화되어 있어 그대로 매핑만) ---
    "DIABETES.CARE": ("Diabetes", "당뇨 관리", ["blood sugar", "insulin", "glucose", "diabetes"]),

    # --- 기존 CATEGORY_CONFIG["Travel"] 세분화 ---
    "TRAVEL.DESTINATION": ("Travel", "여행지/관광", ["destination", "tourism", "travel guide"]),

    # --- 기존 CATEGORY_CONFIG["Golf"] (그대로 매핑) ---
    "GOLF.GENERAL": ("Golf", "골프", ["golf", "pga", "lpga", "masters"]),

    # --- CATEGORY_CONFIG에 없던 신규 개인화 관심 영역 (문화/독서/철학 — 별도 소스 필요할 수 있음) ---
    "CULTURE.MEDIA": ("Culture", "영화/음악/엔터테인먼트", ["billboard", "pitchfork", "film review", "album"]),
    "CULTURE.FOOD": ("Culture", "음식/요리", ["recipe", "bon appetit", "cooking", "cuisine"]),
    "READING.BOOKS": ("Reading", "독서/도서", ["book review", "author", "novel", "publishing"]),
}

# 민감 카테고리: 프로필을 "답변 결론 유도"가 아니라 "정보 필터링"에만 써야 하는 영역
SENSITIVE_TOP_CATEGORIES = {"Politics", "Economy"}


def get_top_category(subcategory_code: str) -> str:
    return SUBCATEGORY_CONFIG[subcategory_code][0]


def is_sensitive(subcategory_code: str) -> bool:
    return get_top_category(subcategory_code) in SENSITIVE_TOP_CATEGORIES


def score_subcategories_for_text(title: str, content: str) -> dict:
    """
    기존 main.py의 _score_categories_for_article과 동일한 방식(제목 가중치 3배,
    본문 가중치 1배)으로 서브카테고리 점수를 계산한다.
    반환: {subcategory_code: score}
    """
    import re

    title_lower = (title or "").lower()
    content_lower = (content or "").lower()

    scores = {}
    for code, (_top, _label, keywords) in SUBCATEGORY_CONFIG.items():
        score = 0
        for term in keywords:
            pattern = r'(?:^|\b|[^\w])' + re.escape(term) + r'(?:$|\b|[^\w])'
            score += len(re.findall(pattern, title_lower, re.IGNORECASE)) * 3
            score += len(re.findall(pattern, content_lower, re.IGNORECASE)) * 1
        if score > 0:
            scores[code] = score
    return scores


def best_subcategory_for_text(title: str, content: str):
    """가장 점수가 높은 서브카테고리 1개 (없으면 None)."""
    scores = score_subcategories_for_text(title, content)
    if not scores:
        return None
    return max(scores, key=scores.get)
