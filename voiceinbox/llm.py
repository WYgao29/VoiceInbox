"""本机 oMLX（OpenAI 兼容，默认 http://127.0.0.1:8000/v1）。只走 127.0.0.1，不连外网。

API key 不进仓库：先看环境变量 OMLX_API_KEY，再读 oMLX 自己的 ~/.omlx/settings.json
（auth.api_key）。只读那个文件，不改 ~/.omlx 里的任何东西。

服务没开时，用 oMLX 自带的 `~/.omlx/bin/omlx start` 拉起（它会打开 oMLX.app 的托管服务）。
用完时如果模型是这一轮才加载的，就请求卸载，避免和 Qwen3-ASR 一起占着内存。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from voiceinbox.config import Config
from voiceinbox.log import LOG


class LlmError(RuntimeError):
    pass


class LlmUnavailable(LlmError):
    """服务连不上、也拉不起来。不算这条录音的失败，下一轮再试。"""


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _opener():
    # 显式不走代理：本机有 clash 一类的代理时，127.0.0.1 也可能被转发出去。
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_api_key(cfg: Config) -> str:
    env = os.environ.get("OMLX_API_KEY", "").strip()
    if env:
        return env
    path = cfg.llm_settings_file
    if path is None:
        return ""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    auth = data.get("auth") or {}
    if auth.get("skip_api_key_verification"):
        return ""
    return str(auth.get("api_key") or "")


@dataclass
class ChatResult:
    text: str
    model: str
    elapsed_s: float
    usage: dict


class OmlxClient:
    def __init__(self, cfg: Config, *, opener=None):
        parsed = urllib.parse.urlsplit(cfg.llm_base_url)
        if parsed.scheme != "http" or (parsed.hostname or "") not in _LOCAL_HOSTS:
            raise LlmError("本机总结只允许连 http://127.0.0.1")
        self.cfg = cfg
        self.base = cfg.llm_base_url.rstrip("/")
        self.root = f"{parsed.scheme}://{parsed.netloc}"
        self.model = cfg.llm_model
        self._key = read_api_key(cfg)
        self._opener = opener or _opener()
        self.loaded_by_us = False

    # ---------- HTTP ----------
    def _request(self, method: str, url: str, payload: dict | None = None, *, timeout: float) -> dict:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise LlmError(f"oMLX 返回 {exc.code}：{detail}") from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise LlmUnavailable(f"连不上 oMLX：{exc}") from exc
        try:
            return json.loads(body.decode("utf-8")) if body else {}
        except ValueError as exc:
            raise LlmError("oMLX 返回的不是 JSON") from exc

    def healthy(self) -> bool:
        try:
            self._request("GET", self.root + "/health", timeout=5)
            return True
        except LlmError:
            return False

    def ensure_server(self) -> None:
        if self.healthy():
            return
        cli = self.cfg.llm_cli
        if not self.cfg.llm_auto_start or cli is None or not Path(cli).is_file():
            raise LlmUnavailable("oMLX 没有在运行。请打开 oMLX，或运行 ~/.omlx/bin/omlx start")
        LOG.info("oMLX 没在运行，调用 %s start", cli)
        try:
            proc = subprocess.run(
                [str(cli), "start", "--timeout", "120"],
                capture_output=True,
                text=True,
                timeout=150,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LlmUnavailable(f"拉起 oMLX 失败：{exc}") from exc
        for _ in range(30):
            if self.healthy():
                LOG.info("oMLX 已经起来")
                return
            time.sleep(2)
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise LlmUnavailable(f"oMLX 没有起来：{detail}")

    def model_loaded(self) -> bool | None:
        try:
            data = self._request("GET", self.base + "/models/status", timeout=10)
        except LlmError:
            return None
        for item in data.get("models") or []:
            if item.get("id") == self.model:
                return bool(item.get("loaded"))
        return None

    def prepare(self) -> None:
        self.ensure_server()
        loaded = self.model_loaded()
        self.loaded_by_us = loaded is False

    def release(self) -> None:
        """只卸掉这一轮自己加载的模型。用户自己在用的不动。"""
        if not (self.cfg.llm_unload_after and self.loaded_by_us):
            return
        quoted = urllib.parse.quote(self.model, safe="")
        try:
            self._request("POST", f"{self.base}/models/{quoted}/unload", {}, timeout=30)
            LOG.info("已请求 oMLX 卸载 %s", self.model)
        except LlmError as exc:
            LOG.warning("卸载 %s 失败：%s", self.model, exc)
        self.loaded_by_us = False

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> ChatResult:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.9,
            "stream": False,
            # Qwen3.6 默认先「思考」。这里要的是结构化结果，关掉更快也更稳。
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        data = self._request("POST", self.base + "/chat/completions", payload, timeout=self.cfg.llm_timeout_s)
        elapsed = time.perf_counter() - started
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("oMLX 返回里没有内容") from exc
        text = strip_think(text)
        return ChatResult(text=text, model=str(data.get("model") or self.model), elapsed_s=elapsed, usage=data.get("usage") or {})

    def chat_json(self, system: str, user: str, *, max_tokens: int = 4096) -> dict:
        last: Exception | None = None
        for attempt in range(2):
            result = self.chat(system, user, json_mode=True, max_tokens=max_tokens, temperature=0.2 if attempt else 0.3)
            try:
                return parse_json_object(result.text)
            except ValueError as exc:
                last = exc
                LOG.warning("模型没有给出合法 JSON，重试一次：%s", result.text[:200])
        raise LlmError(f"模型没有给出合法 JSON：{last}")


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def parse_json_object(text: str) -> dict:
    raw = strip_think(text)
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        raw = fence.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("没有 JSON 对象")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 不是对象")
    return data
