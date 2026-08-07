# -*- coding: utf-8 -*-
"""
main_integration_snippet.py
------------------------------
main.py에 아래 내용을 추가하세요. 기존 엔드포인트는 하나도 수정하지 않습니다.

1) 상단 import 구역에 추가:
   from personalization import (
       classify_and_store, store_explicit_feedback,
       get_profile, get_top_interests,
   )
   from pydantic import BaseModel  # 이미 있음 (기존 import 재사용)

2) 기존 "아티클 목록 조회 API" 근처, 파일 끝쪽에 아래 엔드포인트들을 추가:
"""

# ============================================
# 개인화 프로필 API (신규)
# ============================================

class ExplicitFeedbackRequest(BaseModel):
    article_id: int
    positive: bool  # True=👍, False=👎


@app.post("/personalization/feedback")
def submit_feedback(request: ExplicitFeedbackRequest, session: Session = Depends(get_session)):
    """
    기사 카드에 👍/👎 버튼을 추가하고 여기로 연결하세요.
    프론트에서는 ArticleCard.jsx 하단에 버튼 두 개만 추가하면 됩니다.
    """
    signal = store_explicit_feedback(session, article_id=request.article_id, positive=request.positive)
    if signal is None:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없거나 분류할 수 없습니다.")
    return {"status": "success", "subcategory": signal.subcategory, "weight": signal.weight}


@app.get("/personalization/profile")
def get_personalization_profile(session: Session = Depends(get_session)):
    """
    현재까지 쌓인 개인화 프로필 전체를 반환한다 (시간 가중 감쇠 적용된 상태).
    설정 화면 등에 "당신의 관심사" 시각화로 바로 붙일 수 있는 형태.
    """
    return {"profile": get_profile(session)}


@app.get("/personalization/top-interests")
def get_personalization_top_interests(n: int = Query(5), session: Session = Depends(get_session)):
    """
    챗봇/보고서 생성 프롬프트에 주입할 상위 관심사.
    사용 예 (증권 브리핑 프롬프트 조립 시):

        top = get_top_interests(session, n=5)
        interest_text = ", ".join(f"{s}({round(d['score'],2)})" for s, d in top)
        system_prompt = f"사용자의 최근 관심 분야: {interest_text}. ..."
    """
    top = get_top_interests(session, n=n)
    return {"top_interests": [{"subcategory": s, **d} for s, d in top]}


# ============================================
# 기존 기사 저장 시점에 자동으로 신호를 남기는 훅
# (RSSCollector/GoogleNewsSearchCollector가 Article을 session.add() 하는
#  지점 — collectors.py 또는 scheduler.py에서 새 기사가 저장된 직후) 예시:
#
#   from personalization import classify_and_store
#   ...
#   session.add(article)
#   session.commit()
#   session.refresh(article)
#   classify_and_store(
#       session, article.title, article.content or "",
#       source="extension", signal_type="implicit", weight=0.3,
#       article_id=article.id,
#   )
#
# weight=0.3으로 낮게 잡은 이유: 단순 "수집됨"은 약한 암묵적 신호이기 때문.
# 사용자가 실제로 클릭/열람하면 더 높은 weight(예: 0.6)로 별도 신호를 추가하세요.
# ============================================
