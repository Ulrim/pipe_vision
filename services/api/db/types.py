"""포터블 컬럼 타입 (Postgres 우선, sqlite 폴백).

- 운영 DB는 Postgres(§3): defect_codes=TEXT[], payload/capture_recipe=JSONB.
- 개발/테스트는 sqlite: 동일 모델을 JSON 직렬화로 저장한다.
SQLAlchemy variant 로 방언별 실제 타입을 분기한다.
"""
from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import Text


# Postgres 에선 TEXT[], 그 외(sqlite)에선 JSON 으로 list[str] 저장.
StringArray = ARRAY(Text()).with_variant(JSON(), "sqlite")

# Postgres 에선 JSONB, 그 외(sqlite)에선 JSON.
JsonB = JSONB().with_variant(JSON(), "sqlite")
