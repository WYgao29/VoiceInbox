"""调用 ~/asr/qwen3-asr/transcribe.sh。不修改那个目录里的任何文件。

必须 HF_HUB_OFFLINE=1，只用已经缓存的 mlx-community/Qwen3-ASR-1.7B-bf16。
"""

from __future__ import annotations

import os
from pathlib import Path

from voiceinbox.config import Config
from voiceinbox.log import LOG
from voiceinbox.proc import run_cmd
from voiceinbox.vocab import hotwords_file_for_asr

ENGINE = "qwen3-asr"


class AsrError(RuntimeError):
    pass


def _apply_hint(env: dict[str, str], key: str, value: str | None) -> None:
    """显式传入才覆盖。None 表示沿用进程环境；空字符串表示这一轮不传。"""
    if value is None:
        return
    if "\x00" in value:
        raise AsrError(f"{key} 含有非法字符")
    text = value.strip()
    if text:
        env[key] = text
    else:
        env.pop(key, None)


def transcribe(
    wav: Path,
    out_txt: Path,
    cfg: Config,
    *,
    timeout_s: float,
    context: str | None = None,
    hotwords: str | None = None,
) -> str:
    """转写一段 wav。

    transcribe.sh 没有背景和热词的命令行参数。它读环境变量：
    ASR_CONTEXT 作为 system_prompt，ASR_HOTWORDS 按逗号、顿号、分号或换行拆开。
    常用热词（~/VoiceInbox/vocab/hotwords.txt）每次都通过 ASR_HOTWORDS_FILE 交给脚本；
    脚本会把它和 ASR_HOTWORDS 合在一起。热词表为空时不设置这个变量。
    context / hotwords 为 None 时不改进程里原有的同名变量。
    """
    if not cfg.asr_script.is_file():
        raise AsrError(f"找不到转写脚本：{cfg.asr_script}")
    env = os.environ.copy()
    env["HF_HUB_OFFLINE"] = "1"
    env["QWEN3_ASR_MODEL"] = cfg.asr_model
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    if cfg.asr_language:
        env["ASR_LANG"] = cfg.asr_language
    else:
        env.pop("ASR_LANG", None)
    _apply_hint(env, "ASR_CONTEXT", context)
    _apply_hint(env, "ASR_HOTWORDS", hotwords)
    words_file = hotwords_file_for_asr(cfg)
    if words_file is not None:
        env["ASR_HOTWORDS_FILE"] = str(words_file)
    else:
        env.pop("ASR_HOTWORDS_FILE", None)
    try:
        code, out, err = run_cmd([str(cfg.asr_script), str(wav)], timeout_s=timeout_s, env=env)
    except TimeoutError as exc:
        raise AsrError(str(exc)) from exc
    for line in (err or "").splitlines():
        if line.startswith("["):
            LOG.info("asr | %s", line)
    if code != 0:
        detail = (err or out or "").strip()
        raise AsrError(f"transcribe.sh 退出码 {code}：{detail[-2000:]}")
    # transcribe.py 用 splitext 写旁路 txt：a.16k.wav → a.16k.txt。
    # 标准输出只是日志，不能当成正文。没写出文件就失败。
    sidecar = wav.with_suffix(".txt")
    if not sidecar.is_file() or sidecar.stat().st_size == 0:
        raise AsrError("transcribe.sh 没有生成文本文件")
    text = sidecar.read_text(encoding="utf-8").strip()
    if not text:
        raise AsrError("转写结果为空")
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    partial = out_txt.with_name(out_txt.name + ".partial")
    partial.write_text(text + "\n", encoding="utf-8")
    os.replace(partial, out_txt)
    if sidecar.exists() and sidecar.resolve() != out_txt.resolve():
        sidecar.unlink()
    return text + "\n"
