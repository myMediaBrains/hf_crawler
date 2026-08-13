# 프로젝트 아키텍처 규칙

## 백엔드 (FastAPI)
- Layered Architecture 준수 (Routers -> Services -> Repositories)
- 모든 API 응답 및 요청은 Pydantic Schema를 사용
- 비동기(async/await) 핸들러 기본 적용

## 프론트엔드 (Vite React)
- TypeScript strict 모드 적용
- 서버 상태 관리는 React Query (TanStack Query) 사용
- UI 스타일링은 Tailwind CSS 및 shadcn/ui 사용

## hf_coder 테스트
- 테스트 커버리지: 80% 이상
- 단위 테스트는 Jest 사용
- E2E 테스트는 Cypress 사용
