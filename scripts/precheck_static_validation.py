#!/usr/bin/env python3
"""dev 사전 점검(노드 정적 검증 spec §5-2): 저장된 워크플로우 최신 버전을 배포된 /v1/workflows/validate에 넣어
code별 건수를 낸다. **읽기 전용** — Mongo는 find만, 결과는 표준출력으로만 낸다.

입력(stdin): 한 줄에 `<workflow_id>|<mongo_definition_id>` (PG workflow_versions 최신 버전, psql -At 출력).
agent 컨테이너 안에서 돌린다(MONGODB_URL·MONGODB_DB_NAME·PORT는 컨테이너 환경변수 — 값을 출력하지 않는다).

  echo "<SQL>" | docker exec -i ieum-postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' \\
    | docker exec -i ieum-agent python scripts/precheck_static_validation.py
"""
import collections
import json
import os
import sys
import urllib.request

from bson import ObjectId
from pymongo import MongoClient

URL = f"http://localhost:{os.environ.get('PORT', '8000')}/v1/workflows/validate"


def _validate(nodes, edges) -> dict:
    req = urllib.request.Request(URL, data=json.dumps({"nodes": nodes, "edges": edges}, default=str).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def main() -> None:
    coll = MongoClient(os.environ["MONGODB_URL"])[os.environ.get("MONGODB_DB_NAME", "ieum")]["workflow_definitions"]
    by_code, broken, missing, failed, total = collections.Counter(), 0, [], [], 0
    for line in sys.stdin:
        if "|" not in line:
            continue
        wf_id, def_id = (s.strip() for s in line.split("|", 1))
        total += 1
        ids = [def_id] + ([ObjectId(def_id)] if ObjectId.is_valid(def_id) else [])
        doc = coll.find_one({"_id": {"$in": ids}}, {"nodes": 1, "edges": 1})
        if doc is None:
            missing.append(wf_id)
            continue
        try:
            result = _validate(doc.get("nodes") or [], doc.get("edges") or [])
        except Exception as e:  # 한 건 때문에 요약까지 잃지 않게 — 실패 목록에 남기고 계속
            failed.append(wf_id)
            print(f"{wf_id}\tCALL_FAILED\t\t\t{type(e).__name__}", file=sys.stderr)
            continue
        if result["valid"]:
            continue
        broken += 1
        for e in result["errors"]:
            by_code[e["code"]] += 1
            print(f"{wf_id}\t{e['code']}\t{e['nodeId']}\t{e['field']}\t{e['message']}")
    print(f"\n# 워크플로우 {total}개 / 에러 있는 워크플로우 {broken}개 / 정의 없음 {len(missing)}개 {missing} / 호출 실패 {len(failed)}개 {failed}")
    for code, n in by_code.most_common():
        print(f"# {code}\t{n}")


if __name__ == "__main__":
    main()
