"""生成 L3 扫描件 fixture：tests/fixtures/ocr-scanned.pdf（纯图单页，无文字层）。

**用系统 Python 跑**（fpdf2 / Pillow 装在系统解释器，.venv 里没有 —— 与
generate_sample_docs.py 同一约定）：

    python scripts/make_ocr_fixture.py

为什么 fixture 要提交进仓：渲染中文依赖 Windows 字体（simsun.ttc），CI 与其他
机器不该重新生成；测试只消费提交的 PDF。改动内容后重新生成并提交。

做法：PIL 把文字画成位图 → fpdf2 把位图铺满 A4 页 → 文字只存在于图像像素里，
pypdf 的 extract_text() 对它抽不出任何字符（这正是「扫描件」的形态）。
"""
from __future__ import annotations

from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "ocr-scanned.pdf"
FONT = Path(r"C:\Windows\Fonts\simsun.ttc")

# 画布按 A4@150dpi 取整（1240x1754）；字体 40px 保证 OCR 稳（渲染 scale 2.0 后 ≈80px）
W, H = 1240, 1754
LINES = [
    "第七章 设备巡检制度",
    "",
    "巡检人员每日检查电镀槽温度并如实记录。",
    "镀层厚度低于五微米的产品必须返工处理。",
    "所有巡检记录保存三年备查。",
]


def main() -> None:
    font = ImageFont.truetype(str(FONT), 40)
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    y = 140
    for line in LINES:
        if line:
            draw.text((100, y), line, fill="black", font=font)
        y += 90
    png = OUT.with_suffix(".tmp.png")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(png)

    pdf = FPDF(unit="pt", format=(W, H))
    pdf.add_page()
    pdf.image(str(png), x=0, y=0, w=W, h=H)
    pdf.output(str(OUT))
    png.unlink()

    # 自检：抽不出文字层才算合格的扫描件
    from pypdf import PdfReader

    text = PdfReader(str(OUT)).pages[0].extract_text() or ""
    assert len("".join(text.split())) == 0, f"fixture 不合格：竟抽出文字层 {text!r}"
    print(f"ok: {OUT}（{OUT.stat().st_size} bytes，无文字层）")


if __name__ == "__main__":
    main()
