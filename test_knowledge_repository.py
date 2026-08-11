from knowledge_repository import search_encyclopedia

print("=== 영어판 테스트 ===")
for r in search_encyclopedia("machine learning"):
    print(f"- {r['title']} ({len(r['content'])}자)")

print("\n=== 한국어판 테스트 ===")
for r in search_encyclopedia("세종대왕", prefer_korean=True):
    print(f"- {r['title']} ({len(r['content'])}자)")

