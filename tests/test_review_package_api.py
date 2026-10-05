"""通过 ASGI 请求验证 5010 审核交接包；不需要 LLM、真实 Run 或运行中的服务。"""
import asyncio
import json
import os
import subprocess

import httpx
import pytest
from fastapi import FastAPI

from live.history import router


def _link_dir(target, link):
    """造一个"看起来在目录里、实则指向别处"的链接,用来试路径穿越防线。

    2026-10-05(GLM 新访客测试 F1 + 本机复核):原实现直接 `symlink_to`,踩到两个坑:

    ① 没开开发者模式的 Windows 上它抛 `WinError 1314客户端没有所需的特权`
      ⇒ **新访客照README 跑自检第一条就红**,而这份红与项目质量无关
      (CI 是 ubuntu 所以绿)。
    ② 更隐蔽:开了开发者模式时它**不抛**,但 Windows 把 `os.symlink` 当 junction 处理,
      `os.path.islink()` 为 False、`os.path.realpath()` **不解析它** ——
      于是"链接指向根外"这个前提**根本没成立**,`realpath + commonpath` 那道防线
      无从触发,这条测试在 Windows 上一直是**假绿**(404 只是因为
      `escape/run.json` 不存在,不是穿越被挡住了)。

    所以顺序是:**Windows 优先用目录联接(junction)**,它 `mklink /J` 建、不需要特权、
    且 `realpath` 同样解析它(实测 realpath 指向目标目录);其他平台用符号链接。
    两者都造不出才skip 并说明原因 —— 但**不许**在 Windows 上悄悄退化成 symlink,
    那正是上面 ② 那个假绿。返回 kind 供调用方自证样本真的造出来了。
    """
    if os.name == "nt":
        try:
            p = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                               capture_output=True)
            if p.returncode == 0:
                return "junction"
        except OSError:
            pass
        return ""            # Windows 上没有junction 就老实说造不出,不退回 symlink
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError, AttributeError):
        return ""


def request(path, method="GET"):
    async def call():
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            return await client.request(method, path)
    return asyncio.run(call())


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("CASE01_RUNS_ROOT", str(tmp_path))
    record = {"run_id": "r1", "final_feedback": {"content": "反馈"},
              "reflection": {"text": "我没有充分核实来源。"},
              "router": {"status": "error", "issues": []},
              "injector": {"secret": "hidden"}, "branch": "A"}
    folder = tmp_path / "r1"
    folder.mkdir()
    file = folder / "run.json"
    file.write_text(json.dumps(record), encoding="utf-8")
    return file, record


def test_http_package_is_readonly_stable_and_safe(source):
    file, _ = source
    before = file.read_bytes()
    response = request("/api/review-package/r1")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "manual_triage"
    assert data["manual_triage"][0]["reason"] == "router_error"
    assert "branch" not in data["snapshot"] and "injector" not in data["snapshot"]
    assert data["snapshot"]["reflection"]["text"] in data["full_context"]
    assert response.headers["etag"] == '"{}"'.format(data["revision"])
    assert request("/api/review-package/r1?raw=1").json() == response.json()
    assert request("/api/review-package/r1", "POST").status_code == 405
    assert file.read_bytes() == before


def test_hidden_record_cannot_be_exported(source):
    file, record = source
    record["deprecated"] = True
    file.write_text(json.dumps(record), encoding="utf-8")
    data = request("/api/review-package/r1").json()["data"]
    assert data["status"] == "blocked"
    assert data["snapshot"] is None and not data["full_context"]
    assert not data["task_candidates"]


@pytest.mark.parametrize("bad", ["not json", "[]", '{"run_id":"another"}'])
def test_invalid_record_is_explicit(source, bad):
    source[0].write_text(bad, encoding="utf-8")
    assert request("/api/review-package/r1").status_code == 422


def test_missing_and_path_escape(source, tmp_path):
    assert request("/api/review-package/missing").status_code == 404
    assert request("/api/review-package/a%5Cb").status_code == 404
    outside = tmp_path.parent / (tmp_path.name + "_outside")
    outside.mkdir()
    (outside / "run.json").write_bytes(source[0].read_bytes())
    kind = _link_dir(str(outside), str(tmp_path / "escape"))
    if not kind:
        pytest.skip("本环境既不能建符号链接也不能建目录联接,无法造路径穿越样本")
    assert request("/api/review-package/escape").status_code == 404
    # 顺带钉住"链接确实造到了根外":否则这条断言可能因为拿不到样本而假绿。
    assert os.path.realpath(str(tmp_path / "escape")) == os.path.realpath(str(outside)), \
        "{} 没把路径解析到根外,穿越场景没被造出来".format(kind)


def test_openapi_exposes_get_contract(source):
    schema = request("/openapi.json").json()
    assert set(schema["paths"]["/api/review-package/{run_id}"]) == {"get"}
    assert set(schema["components"]["schemas"]["ReviewPackage"]["properties"]) >= {
        "revision", "status", "task_candidates", "manual_triage", "snapshot", "full_context"}


def test_relative_root_yields_reason_code_not_500(monkeypatch):
    """M2:相对 `CASE01_RUNS_ROOT` 必须返回**原因码**,不许冒成 500。

    2026-10-05 第六轮只读核查:原实现把 `_data_root(...)` 放在 `try` 之外,
    相对根触发 `AmbiguousPathError(ValueError)` → 未捕获 → HTTP 500。
    把解析挪进 try 后落入既有的 `ValueError` 分支 → 422
    `invalid_review_record_or_catalog`(语义:配置格式错)。
    """
    monkeypatch.setenv("CASE01_RUNS_ROOT", "relative/runs")
    r = request("/api/review-package/r1")
    assert r.status_code == 422, "相对根应给 422,不许是 500"
    assert r.json() == {"ok": False, "error": "invalid_review_record_or_catalog"}


def test_relative_root_on_list_api_skips_source_with_reason(monkeypatch):
    """M2(推广):`/api/runs` 在相对根下 200,并把被跳过的源写进 `source_errors`。

    "配置错 → 该源整块不可用"是可预期降级,不是服务器错误;但**不许静默**,
    否则平台看到 count=0 会以为"这个源本来就空"。
    """
    monkeypatch.setenv("CASE01_RUNS_ROOT", "relative/runs")
    r = request("/api/runs")
    assert r.status_code == 200
    body = r.json()
    errs = body.get("source_errors") or []
    assert any(e["source"] == "review" for e in errs), \
        "跳过 review 源却未在 source_errors 里说明"
    assert "review" in body.get("source_errors_note", "")
