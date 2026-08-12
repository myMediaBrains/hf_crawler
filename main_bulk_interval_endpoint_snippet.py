# -*- coding: utf-8 -*-
"""
main_bulk_interval_endpoint_snippet.py
-----------------------------------------
main.py에 추가할 내용입니다.

1) Pydantic 요청 모델 - 기존 KeywordIntervalSetRequest 클래스 바로 아래에 추가:

       class BulkIntervalUpdateRequest(BaseModel):
           months_back: int = 1
           interval_hours: float = 24.0

2) 엔드포인트 - 기존 "@app.put("/keywords/interval")" 엔드포인트(단일 키워드용,
   IntervalSettings.jsx가 예전 팝오버였을 때 쓰던 것) 바로 아래에 아래 함수를
   통째로 추가하세요. 기존 단일 키워드용 엔드포인트는 지금 프론트에서 더 이상
   호출하지 않지만(데이터편집 탭이 이제 전체 일괄 적용만 함), 다른 데서 쓸 수도
   있으니 삭제하지 말고 그대로 남겨두세요.
"""


# 등록된 모든 키워드에 "최근 N개월 이내 / N시간마다"를 일괄 적용.
# 데이터편집 > 검색주기설정 탭의 '저장' 버튼 전용.
@app.put("/keywords/interval/bulk")
def update_all_keywords_interval(
    request: BulkIntervalUpdateRequest,
    session: Session = Depends(get_session),
):
    keywords = session.exec(select(Keyword)).all()

    count = 0
    for kw in keywords:
        kw.months_back = request.months_back
        kw.interval_hours = request.interval_hours
        session.add(kw)
        count += 1

    session.commit()

    return {
        "status": "success",
        "message": (
            f"등록된 키워드 {count}개에 "
            f"'최근 {request.months_back}개월 이내 / {request.interval_hours}시간마다'를 "
            f"일괄 적용했습니다."
        ),
    }
