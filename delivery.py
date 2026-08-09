"""
delivery.py
개인화 대화 응답을 외부 채널로 보내는 배송 계층 (실험 단계, 무자격증명).

- ntfy.sh: 가입/API키 전혀 불필요. topic이라는 임의의 문자열 하나로 즉시 푸시 발송.
  (공개 브로커이므로 topic은 추측하기 어려운 문자열을 쓰는 걸 권장 - 같은 topic을
  아는 사람은 누구나 구독 가능함)
- 이메일: 서버가 직접 SMTP로 발송하지 않는다. mailto: 링크를 만들어 반환하면
  프론트가 그 링크를 열어서(window.location.href) 사용자의 기본 메일 앱에
  제목/본문이 채워진 채로 뜨고, 사용자가 마지막 "보내기"만 직접 누른다.
  → SMTP 앱 비밀번호 등 자격증명이 전혀 필요 없다.
"""
import logging
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

NTFY_BASE_URL = "https://ntfy.sh"


def send_ntfy(topic: str, title: str, message: str) -> tuple[bool, str | None]:
    """ntfy.sh로 푸시 발송. 반환: (성공 여부, 실패 시 에러 메시지)"""
    try:
        resp = requests.post(
            f"{NTFY_BASE_URL}/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": title.encode("utf-8")},
            timeout=5,
        )
        if resp.status_code == 200:
            return True, None
        return False, f"ntfy 응답 코드 {resp.status_code}"
    except Exception as e:
        logger.warning(f"[delivery] ntfy 발송 실패: {e}")
        return False, str(e)


def build_mailto_link(to_hint: str | None, subject: str, body: str) -> str:
    """실제 발송은 안 하고, 프론트가 열 mailto: 링크만 만들어 반환한다."""
    to_part = quote(to_hint) if to_hint else ""
    return f"mailto:{to_part}?subject={quote(subject)}&body={quote(body)}"