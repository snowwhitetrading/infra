# -*- coding: utf-8 -*-
"""Gộp pace_batches/verdicts_*.json → pace_verdicts.json (để apply_pace_llm.py ghi vào tracker)."""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
merged = {}
for f in sorted(glob.glob(os.path.join(HERE, "pace_batches", "verdicts_*.json"))):
    merged.update(json.load(open(f, encoding="utf-8")))
json.dump(merged, open(os.path.join(HERE, "pace_verdicts.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
from collections import Counter
dist = Counter((v.get("pace") or "(chưa đủ căn cứ)") for v in merged.values())
print(f"Gộp {len(merged)} dự án → pace_verdicts.json · phân bố: {dict(dist)}")
