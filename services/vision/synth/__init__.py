"""학습·평가 데이터 직접 만들기 (2026-10-24).

실물 정답 사진이 모이기 전까지 쓸 데이터를 프로그램으로 만든다.

- `tube`    : 튜브 한 개의 겉모습(원통 음영·반사 띠·결·끝) — 축 정렬 패치 + 실루엣.
- `defects` : 튜브 좌표(축 u, 둘레 v)에서 스크래치·유분기·변색을 그린다(부록 A.5 정의).
- `scene`   : 패치들을 다발로 배치(붙음·벌어짐·끝 엇갈림·회전·원근·조명·배경·기준자)하고
              정답(개수·튜브 네 모서리·길이 mm·불량 코드)을 함께 낸다.
- `real`    : 현장 사진에서 튜브 질감을 잘라 패치로 쓴다(사진은 저장소에 두지 않는다).
- `dataset` : 부록 A.4 형식(raw/<코드>/ + 사이드카 json)과 장면 정답 json 으로 저장.

**인수 증빙이 아니다.** 알고리즘이 조건 변화에 버티는지, 학습 파이프라인이 끝까지 도는지를
확인하는 데이터다. 실제 불량 판정 정확도는 실물 정답셋으로만 확정한다(CLAUDE.md §1.2 정의 고정).
모두 시드 고정으로 결정적이다.
"""
from .defects import DEFECT_CODES, paint_defects
from .scene import SceneSpec, make_scene
from .tube import TubeLook, TubePatch, render_tube

__all__ = ["DEFECT_CODES", "SceneSpec", "TubeLook", "TubePatch", "make_scene", "paint_defects", "render_tube"]
