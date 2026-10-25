"""扫描件 OCR（L3）：pypdfium2 渲染 + RapidOCR 识别。

依赖在 ``[ocr]`` extra（rapidocr-onnxruntime / pypdfium2），**未安装不报错**：
``available()`` 返回 False，调用方按需求卡决策 3 降级 —— 文字 PDF 的摄取完全
不受影响，纯图扫描件以「疑似扫描件」的明确原因 failed。

- 模型内置在 wheel 里，安装后**离线可用**（测试与生产都不联网下载模型）；
- 引擎构造要加载 onnx 模型（实测 ~1s），进程内只建一次（惰性单例）；
- 推理用锁串行化：摄取跑在 K4 线程池里，OCR 吞吐本就不高，串行是保守但省心的选择；
- 渲染走 ``bitmap.to_numpy()``（numpy 由 opencv 依赖带入），不引入 Pillow。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

#: 渲染 scale ≈ 144 DPI，印刷体实测足够；刻意不做配置，旋钮越少越好。
_RENDER_SCALE = 2.0

_engine = None
_engine_lock = threading.Lock()


def available() -> bool:
    """rapidocr 与 pypdfium2 是否都已安装（只探测 import，不构造引擎）。"""
    try:
        import pypdfium2  # noqa: F401
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401

        return True
    except ImportError:
        return False


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def ocr_pages(path: str | Path, page_indices: list[int]) -> list[str]:
    """渲染并 OCR 指定页，返回与 ``page_indices`` 等长的文本列表。

    页与页之间互不连坐：某页识别抛错记 warning、该页记空串，其余页照常返回 ——
    调用方拿到空串时保留该页原（弱）文本，不让一页的失败拖垮整份文档。
    """
    import pypdfium2 as pdfium

    out: list[str] = []
    with _engine_lock:
        engine = _get_engine()
        pdf = pdfium.PdfDocument(str(path))
        try:
            for idx in page_indices:
                try:
                    arr = pdf[idx].render(scale=_RENDER_SCALE).to_numpy()  # RGB
                    # cv2 生态按 BGR 读图，RapidOCR 按该约定处理 ndarray
                    result, _elapse = engine(arr[:, :, ::-1].copy())
                    out.append("\n".join(item[1] for item in (result or [])))
                except Exception:
                    logger.warning("单页 OCR 失败（该页保留原文本）file=%s page=%s", path, idx, exc_info=True)
                    out.append("")
        finally:
            pdf.close()
    return out
