"""通过 ASGI 请求验证 5010 审核交接包；不需要 LLM、真实 Run 或运行中的服务。"""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from live.history import router


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
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    assert request("/api/review-package/escape").status_code == 404


def test_openapi_exposes_get_contract(source):
    schema = request("/openapi.json").json()
    assert set(schema["paths"]["/api/review-package/{run_id}"]) == {"get"}
    assert set(schema["components"]["schemas"]["ReviewPackage"]["properties"]) >= {
        "revision", "status", "task_candidates", "manual_triage", "snapshot", "full_context"}
