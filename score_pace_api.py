# -*- coding: utf-8 -*-
"""
score_pace_api.py — Chấm nhịp độ + % hoàn thành bằng Claude API (thay cho `claude -p` CLI
đã chạy trên server Viettel). Đọc pace_bundles.json → chia lô → gọi Anthropic Messages API
với rubric pace_agent_prompt.txt → gộp verdicts → pace_verdicts.json.

Cần biến môi trường ANTHROPIC_API_KEY. Dùng trong GitHub Actions hàng tháng.

  python score_pace_api.py [--in pace_bundles.json] [--out pace_verdicts.json] [--asof YYYY-MM]
"""
import argparse
import json
import os
import re
import sys

import anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("PACE_MODEL", "claude-sonnet-5-5")   # đổi model qua env nếu cần
BATCH = int(os.environ.get("PACE_BATCH", "20"))


def _extract_json(text):
    """Lấy object JSON lớn nhất trong text (phòng khi model kèm lời dẫn)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    return json.loads(m.group(0))


def run(bundles_path, out_path, asof=None):
    with open(os.path.join(HERE, bundles_path), encoding="utf-8") as f:
        bundles = json.load(f)
    system = open(os.path.join(HERE, "pace_agent_prompt.txt"), encoding="utf-8").read()
    if asof:                                      # nhắc as-of: không dùng thông tin sau tháng mục tiêu
        system = (f"AS-OF: Dữ liệu CHỈ tới hết tháng {asof}. Chấm theo trạng thái dự án TẠI "
                  f"thời điểm đó, KHÔNG dùng thông tin sau {asof}.\n\n") + system

    client = anthropic.Anthropic()               # ANTHROPIC_API_KEY lấy từ env
    tids = list(bundles)
    verdicts, n_err = {}, 0
    for i in range(0, len(tids), BATCH):
        chunk = {k: bundles[k] for k in tids[i:i + BATCH]}
        payload = json.dumps(chunk, ensure_ascii=False)
        try:
            with client.messages.stream(        # stream → tránh timeout khi output dài
                model=MODEL, max_tokens=12000, system=system,
                messages=[{"role": "user", "content": payload}],
            ) as stream:
                msg = stream.get_final_message()
            text = "".join(b.text for b in msg.content if b.type == "text")
            verdicts.update(_extract_json(text))
        except (anthropic.APIError, json.JSONDecodeError, ValueError) as e:
            n_err += 1
            print(f"  ! lô {i // BATCH} lỗi: {e}", file=sys.stderr)

    with open(os.path.join(HERE, out_path), "w", encoding="utf-8") as f:
        json.dump(verdicts, f, ensure_ascii=False)
    print(f"Chấm API ({MODEL}): {len(verdicts)}/{len(tids)} dự án → {out_path}"
          + (f" · lô lỗi: {n_err}" if n_err else ""))
    if not verdicts:
        sys.exit("Không chấm được dự án nào (kiểm tra ANTHROPIC_API_KEY / model).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="bundles", default="pace_bundles.json")
    ap.add_argument("--out", default="pace_verdicts.json")
    ap.add_argument("--asof", default=None, help="tháng mục tiêu YYYY-MM (nhắc model không dùng tin sau đó)")
    a = ap.parse_args()
    run(a.bundles, a.out, a.asof)
