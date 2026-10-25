"""L3：扫描件 OCR fallback 测试。

真 OCR 用例带闸门（未装 rapidocr / pypdfium2 自动 skip —— pgvector 用例同款模式）；
降级分支（未装 / 关闭）用 monkeypatch 钉死 ``ocr.available``，不需要引擎。

fixture：``tests/fixtures/ocr-scanned.pdf`` —— 纯图单页扫描件，生成脚本
``scripts/make_ocr_fixture.py``（系统 Python，字体依赖 Windows simsun；产物已提交，
CI 与其他机器只消费不生成）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingestion import ocr
from app.ingestion.parsers import parse_file
from app.main import app
from tests.ingest_helpers import upload_and_wait

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ocr-scanned.pdf"
#: 文字页样本（真实语料第一页，文字层充足）
TEXT_PDF = ROOT / "docs" / "sample-docs" / "全球优选_产品手册.pdf"

needs_engine = pytest.mark.skipif(
    not ocr.available(),
    reason="未安装 rapidocr-onnxruntime / pypdfium2（pip install -e '.[dev,api,ocr]'）",
)


@pytest.fixture(autouse=True)
def _fresh_settings():
    """本文件直接改环境变量（OCR_ENABLED）—— Settings 是读 env 重建的，
    唯一要防的是 monkeypatch.setenv 在测试结束后还原，这里没有跨用例缓存可清。
    保留空夹具作为说明位：_parse_pdf 每次调用都 get_settings() → Settings()，
    环境变量天然按用例隔离（monkeypatch 自动还原）。"""
    yield


# ---- available() 探测 ----

def test_available_false_when_not_installed(monkeypatch) -> None:
    # sys.modules[name] = None 是 import 系统约定的「该模块不可用」写法，
    # 钉住 available() 对 ImportError 的降级（闸门行为的根）。
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    assert ocr.available() is False


# ---- 真扫描件：装了 OCR 的主路径 ----

@needs_engine
def test_scanned_pdf_parsed_via_ocr() -> None:
    """纯图 PDF 经 OCR 变成可检索文本（fixture 关键句逐字命中）。"""
    text = parse_file(FIXTURE)
    assert "设备巡检制度" in text
    assert "电镀槽" in text
    assert "五微米" in text


@needs_engine
def test_scanned_pdf_ingestion_end_to_end(tmp_path, monkeypatch) -> None:
    """端到端：扫描件走完整异步摄取 → indexed，切片入库（mock 供应商 + 真 OCR）。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with TestClient(app, raise_server_exceptions=False) as client:
        client.post("/api/auth/register", json={"username": "test_l3", "password": "secret123"})
        token = client.post(
            "/api/auth/login", json={"username": "test_l3", "password": "secret123"}
        ).json()["access_token"]
        kb_id = client.post(
            "/api/kbs", json={"name": "l3_kb"}, headers={"Authorization": f"Bearer {token}"}
        ).json()["id"]

        doc = upload_and_wait(
            client, kb_id, token, "扫描巡检制度.pdf", FIXTURE.read_bytes(), "application/pdf"
        )
        assert doc["status"] == "indexed", doc
        assert doc["chunk_count"] > 0


# ---- 未装 OCR / 关闭：降级分支（monkeypatch 钉死，不依赖引擎在不在） ----

def test_scanned_pdf_clear_error_without_ocr(monkeypatch) -> None:
    """整本皆弱 + OCR 不可用 → 诚实失败，点名「疑似扫描件」与安装指引。"""
    monkeypatch.setattr(ocr, "available", lambda: False)
    with pytest.raises(ValueError) as ei:
        parse_file(FIXTURE)
    msg = str(ei.value)
    assert "疑似扫描件" in msg
    assert "rapidocr-onnxruntime" in msg


def test_scanned_pdf_error_when_ocr_disabled(monkeypatch) -> None:
    """OCR_ENABLED=false 等价于不可用，但错误信息改为提示开关而不是装组件。"""
    monkeypatch.setenv("OCR_ENABLED", "false")
    monkeypatch.setattr(ocr, "available", lambda: True)  # 装没装都轮不到它
    with pytest.raises(ValueError) as ei:
        parse_file(FIXTURE)
    msg = str(ei.value)
    assert "疑似扫描件" in msg
    assert "OCR_ENABLED" in msg


def test_text_pdf_does_not_trigger_ocr(monkeypatch) -> None:
    """文字页 ≥ 阈值 → 不进 OCR。available 放行、ocr_pages 换成必炸替身：
    解析仍成功 = 没有任何页被送去 OCR（是阈值拦的，不是组件缺失拦的）。"""
    monkeypatch.setattr(ocr, "available", lambda: True)

    def _must_not_run(*args, **kwargs):
        raise AssertionError("文字页不该触发 OCR")

    monkeypatch.setattr(ocr, "ocr_pages", _must_not_run)
    text = parse_file(TEXT_PDF)
    assert len(text) > 100  # 真解析出了内容，不是空转


def test_mixed_pdf_without_ocr_keeps_text_pages(tmp_path, monkeypatch) -> None:
    """混合 PDF（文字页 + 图片页）在未装 OCR 时行为与改动前一致：弱页跳过、摄取照常。

    决策 3 的中档分支 —— 带图片封面的文字 PDF 不能因为没装 OCR 而失败。
    """
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.add_page(PdfReader(str(TEXT_PDF)).pages[0])  # 文字页
    writer.add_page(PdfReader(str(FIXTURE)).pages[0])  # 图片页
    mixed = tmp_path / "mixed.pdf"
    with open(mixed, "wb") as f:
        writer.write(f)

    monkeypatch.setattr(ocr, "available", lambda: False)
    text = parse_file(mixed)
    assert "产品手册" in text or len(text) > 100  # 文字页内容在，图片页贡献为零
