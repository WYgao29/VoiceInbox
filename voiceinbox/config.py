"""读取 config.toml。环境变量可以覆盖路径和语言。"""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from voiceinbox.timeutil import zone

_DEFAULT_MODEL = "mlx-community/Qwen3-ASR-1.7B-bf16"


def default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config.toml"


def _as_path(value: object, base: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if not path.is_absolute():
        path = base / path
    return path


def _as_since(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if text.lower() in {"", "none", "null"}:
        return None
    return date.fromisoformat(text)


@dataclass(frozen=True)
class Config:
    voice_memos_dir: Path
    inbox_dir: Path
    root_dir: Path
    asr_script: Path
    asr_model: str = _DEFAULT_MODEL
    asr_language: str = "Chinese"
    stable_wait_s: float = 60
    stable_recheck_s: float = 3
    max_attempts: int = 3
    retry_base_s: int = 300
    min_duration_s: float = 3
    since: date | None = None
    keep_audio_copy: bool = True
    timezone: str = "Asia/Shanghai"
    notify_on_fda_block: bool = False
    max_per_run: int = 10
    undated_policy: str = "mtime"
    # 闪念：短于这个秒数的录音只润色成一小段，追加到当天的每日笔记。
    flash_max_s: float = 120
    # 本机 oMLX（OpenAI 兼容）。默认关，config.toml 里打开。
    llm_enabled: bool = False
    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_model: str = "Qwen3.6-35B-A3B-4bit"
    llm_settings_file: Path | None = None
    llm_cli: Path | None = None
    llm_auto_start: bool = True
    llm_timeout_s: float = 900
    llm_unload_after: bool = True
    llm_chunk_chars: int = 20000
    obsidian_enabled: bool = False
    obsidian_vault: Path | None = None
    obsidian_folder: str = "语音备忘录"
    digest_enabled: bool = False
    digest_time: str = "22:30"

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts 至少为 1")
        if self.max_per_run < 1:
            raise ValueError("max_per_run 至少为 1")
        if self.min_duration_s < 0 or self.stable_wait_s < 0 or self.stable_recheck_s < 0:
            raise ValueError("时长和等待时间不能为负数")
        if self.retry_base_s < 0:
            raise ValueError("retry_base_s 不能为负数")
        if self.undated_policy not in {"mtime", "skip"}:
            raise ValueError("undated_policy 只能是 mtime 或 skip")
        zone(self.timezone)
        if self.flash_max_s < 0:
            raise ValueError("flash_max_s 不能为负数")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", self.digest_time):
            raise ValueError("digest_time 必须是 HH:MM")
        if self.llm_chunk_chars < 2000:
            raise ValueError("llm_chunk_chars 至少 2000")
        folder = self.obsidian_folder.strip().strip("/")
        if not folder or ".." in folder.split("/"):
            raise ValueError("obsidian_folder 不能为空，也不能含 ..")

    @property
    def staging_dir(self) -> Path:
        return self.root_dir / "staging"

    @property
    def work_dir(self) -> Path:
        return self.root_dir / "work"

    @property
    def transcripts_dir(self) -> Path:
        return self.root_dir / "transcripts"

    @property
    def failed_dir(self) -> Path:
        return self.root_dir / "failed"

    @property
    def state_dir(self) -> Path:
        return self.root_dir / "state"

    @property
    def logs_dir(self) -> Path:
        return self.root_dir / "logs"

    @property
    def archive_dir(self) -> Path:
        return self.root_dir / "archive" / "audio"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.sqlite"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / ".lock"

    @property
    def vocab_dir(self) -> Path:
        return self.root_dir / "vocab"

    @property
    def hotwords_file(self) -> Path:
        """常用热词唯一的来源。每次转写都通过 ASR_HOTWORDS_FILE 交给 Qwen。"""
        return self.vocab_dir / "hotwords.txt"

    @property
    def replacements_file(self) -> Path:
        return self.vocab_dir / "replacements.toml"

    @property
    def search_db_path(self) -> Path:
        return self.state_dir / "search.sqlite"

    @property
    def backup_dir(self) -> Path:
        return self.root_dir / "backups"

    @property
    def obsidian_root(self) -> Path | None:
        if self.obsidian_vault is None:
            return None
        return self.obsidian_vault / self.obsidian_folder.strip().strip("/")


def config_from_mapping(data: dict, *, base: Path) -> Config:
    paths = data.get("paths") or {}
    asr = data.get("asr") or {}
    pipe = data.get("pipeline") or {}
    llm = data.get("llm") or {}
    obs = data.get("obsidian") or {}
    digest = data.get("digest") or {}
    vault = str(obs.get("vault", "") or "").strip()
    llm_settings = str(llm.get("settings_file", "~/.omlx/settings.json") or "").strip()
    llm_cli = str(llm.get("cli", "~/.omlx/bin/omlx") or "").strip()
    return Config(
        voice_memos_dir=_as_path(
            paths.get(
                "voice_memos_dir",
                "~/Library/Group Containers/group.com.apple.VoiceMemos.shared",
            ),
            base,
        ),
        inbox_dir=_as_path(paths.get("inbox_dir", "~/VoiceInbox/inbox"), base),
        root_dir=_as_path(paths.get("root_dir", "~/VoiceInbox"), base),
        asr_script=_as_path(asr.get("script", "~/asr/qwen3-asr/transcribe.sh"), base),
        asr_model=str(asr.get("model", _DEFAULT_MODEL)),
        asr_language=str(asr.get("language", "Chinese")),
        stable_wait_s=float(pipe.get("stable_wait_s", 60)),
        stable_recheck_s=float(pipe.get("stable_recheck_s", 3)),
        max_attempts=int(pipe.get("max_attempts", 3)),
        retry_base_s=int(pipe.get("retry_base_s", 300)),
        min_duration_s=float(pipe.get("min_duration_s", 3)),
        since=_as_since(pipe.get("since", "2026-10-01")),
        keep_audio_copy=bool(pipe.get("keep_audio_copy", True)),
        timezone=str(pipe.get("timezone", "Asia/Shanghai")),
        notify_on_fda_block=bool(pipe.get("notify_on_fda_block", False)),
        max_per_run=int(pipe.get("max_per_run", 10)),
        undated_policy=str(pipe.get("undated_policy", "mtime")),
        flash_max_s=float(pipe.get("flash_max_s", 120)),
        llm_enabled=bool(llm.get("enabled", False)),
        llm_base_url=str(llm.get("base_url", "http://127.0.0.1:8000/v1")).rstrip("/"),
        llm_model=str(llm.get("model", "Qwen3.6-35B-A3B-4bit")),
        llm_settings_file=_as_path(llm_settings, base) if llm_settings else None,
        llm_cli=_as_path(llm_cli, base) if llm_cli else None,
        llm_auto_start=bool(llm.get("auto_start", True)),
        llm_timeout_s=float(llm.get("timeout_s", 900)),
        llm_unload_after=bool(llm.get("unload_after", True)),
        llm_chunk_chars=int(llm.get("chunk_chars", 20000)),
        obsidian_enabled=bool(obs.get("enabled", False)),
        obsidian_vault=_as_path(vault, base) if vault else None,
        obsidian_folder=str(obs.get("folder", "语音备忘录")),
        digest_enabled=bool(digest.get("enabled", False)),
        digest_time=str(digest.get("time", "22:30")),
    )


def load_config(path: str | Path | None = None) -> Config:
    if path is None or path == "":
        env = os.environ.get("VOICEINBOX_CONFIG")
        path = Path(env) if env else default_config_path()
    path = Path(os.path.expanduser(str(path)))
    if not path.is_file():
        raise FileNotFoundError(f"找不到配置文件：{path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return _apply_env(config_from_mapping(data, base=path.parent))


def _apply_env(cfg: Config) -> Config:
    changes: dict[str, object] = {}
    if os.environ.get("VOICEINBOX_ROOT"):
        changes["root_dir"] = Path(os.path.expanduser(os.environ["VOICEINBOX_ROOT"]))
    if os.environ.get("VOICEINBOX_INBOX"):
        changes["inbox_dir"] = Path(os.path.expanduser(os.environ["VOICEINBOX_INBOX"]))
    if os.environ.get("VOICEINBOX_VOICE_MEMOS"):
        changes["voice_memos_dir"] = Path(os.path.expanduser(os.environ["VOICEINBOX_VOICE_MEMOS"]))
    if os.environ.get("VOICEINBOX_ASR_SCRIPT"):
        changes["asr_script"] = Path(os.path.expanduser(os.environ["VOICEINBOX_ASR_SCRIPT"]))
    if "VOICEINBOX_ASR_LANG" in os.environ:
        changes["asr_language"] = os.environ["VOICEINBOX_ASR_LANG"]
    if os.environ.get("VOICEINBOX_SINCE"):
        changes["since"] = _as_since(os.environ["VOICEINBOX_SINCE"])
    if not changes:
        return cfg
    return replace(cfg, **changes)


_LANG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_SINCE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_since_input(value: object) -> date | None:
    """界面上的起始日期。空字符串表示不按日期过滤。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("起始日期必须是 YYYY-MM-DD，或留空")
    text = value.strip()
    if text == "":
        return None
    if not _SINCE_RE.fullmatch(text):
        raise ValueError("起始日期必须是 YYYY-MM-DD")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("起始日期不是有效日期") from exc


def parse_language(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("语言必须是字符串")
    text = value.strip()
    if text == "":
        return ""
    if not _LANG_RE.fullmatch(text):
        raise ValueError("语言留空表示自动判断，或填写 Chinese、English 这类英文标识")
    return text


def parse_max_per_run(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("每轮最多条数必须是整数")
    if not 1 <= value <= 10000:
        raise ValueError("每轮最多条数必须在 1 到 10000 之间")
    return value


def read_common_settings(path: str | Path) -> dict[str, object]:
    """读文件里的三项，不看环境变量。"""
    path = Path(path)
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    pipe = data.get("pipeline") or {}
    asr = data.get("asr") or {}
    if "since" in pipe:
        since = _as_since(pipe.get("since"))
    else:
        since = date.fromisoformat("2026-10-01")
    if "max_per_run" in pipe:
        max_per_run = int(pipe["max_per_run"])
    else:
        max_per_run = 10
    language = str(asr["language"]) if "language" in asr else "Chinese"
    return {
        "since": "" if since is None else since.isoformat(),
        "max_per_run": max_per_run,
        "language": language,
    }


def _toml_basic(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _section_name(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("[") or stripped.startswith("[[") or stripped.startswith("#"):
        return None
    match = re.match(r"\[([A-Za-z0-9_.\-]+)\]", stripped)
    if not match:
        return None
    return match.group(1)


def _is_assignment(line: str, key: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    return re.match(rf"{re.escape(key)}[ \t]*=", stripped) is not None


def _split_trailing_comment(text: str) -> tuple[str, str]:
    in_string = False
    quote = ""
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
            continue
        if char == "#":
            return text[:index].rstrip(), text[index:].strip()
    return text.rstrip(), ""


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return "\n"


def set_toml_key(text: str, section: str, key: str, rendered: str) -> str:
    """替换某个表里的赋值。保留缩进和行尾注释。缺了这个键就补到该表末尾。"""
    lines = text.splitlines(keepends=True)
    current: str | None = None
    spans: list[tuple[str, int, int]] = []
    start = None
    for index, line in enumerate(lines):
        name = _section_name(line)
        if name is None:
            continue
        if current is not None and start is not None:
            spans.append((current, start, index))
        current = name
        start = index
    if current is not None and start is not None:
        spans.append((current, start, len(lines)))
    target = next((item for item in spans if item[0] == section), None)
    if target is None:
        raise ValueError(f"配置缺少 [{section}]")
    _, section_start, section_end = target
    found = None
    for index in range(section_start + 1, section_end):
        if _is_assignment(lines[index], key):
            found = index
    if found is not None:
        old = lines[found]
        ending = _line_ending(old) if old.endswith(("\n", "\r\n")) else "\n"
        _code, comment = _split_trailing_comment(old.rstrip("\r\n"))
        indent = re.match(r"[ \t]*", old).group(0)
        suffix = f" {comment}" if comment else ""
        lines[found] = f"{indent}{key} = {rendered}{suffix}{ending}"
    else:
        if section_end > 0 and lines and not lines[section_end - 1].endswith(("\n", "\r\n")):
            lines[section_end - 1] = lines[section_end - 1] + "\n"
        lines.insert(section_end, f"{key} = {rendered}\n")
    return "".join(lines)


def _atomic_replace(path: Path, text: str) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=directory)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        mode = path.stat().st_mode & 0o777
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def update_common_settings(
    path: str | Path,
    *,
    since: object,
    max_per_run: object,
    language: object,
) -> dict[str, object]:
    """校验后原子替换 since、max_per_run、language。失败时原文件不变。"""
    path = Path(path)
    since_date = parse_since_input(since)
    limit = parse_max_per_run(max_per_run)
    lang = parse_language(language)
    original = path.read_text(encoding="utf-8")
    updated = set_toml_key(original, "pipeline", "since", _toml_basic("" if since_date is None else since_date.isoformat()))
    updated = set_toml_key(updated, "pipeline", "max_per_run", str(limit))
    updated = set_toml_key(updated, "asr", "language", _toml_basic(lang))
    try:
        config_from_mapping(tomllib.loads(updated), base=path.parent)
    except (tomllib.TOMLDecodeError, ValueError, OSError, TypeError) as exc:
        raise ValueError(f"新配置无法解析，没有写入：{exc}") from exc
    parsed = read_common_settings_text(updated)
    expected = {
        "since": "" if since_date is None else since_date.isoformat(),
        "max_per_run": limit,
        "language": lang,
    }
    if parsed != expected:
        raise ValueError("写入前校验没有对上，没有修改配置文件")
    if updated != original:
        _atomic_replace(path, updated)
    return expected


def read_common_settings_text(text: str) -> dict[str, object]:
    data = tomllib.loads(text)
    pipe = data.get("pipeline") or {}
    asr = data.get("asr") or {}
    since = _as_since(pipe.get("since")) if "since" in pipe else date.fromisoformat("2026-10-01")
    max_per_run = int(pipe["max_per_run"]) if "max_per_run" in pipe else 10
    language = str(asr["language"]) if "language" in asr else "Chinese"
    return {
        "since": "" if since is None else since.isoformat(),
        "max_per_run": max_per_run,
        "language": language,
    }


def ensure_tool_path() -> None:
    """launchd 的 PATH 很短。这里把 Homebrew 补上，方便找到 ffmpeg。"""
    extra = ["/opt/homebrew/bin", "/usr/local/bin"]
    current = os.environ.get("PATH", "")
    parts = current.split(":") if current else []
    missing = [item for item in extra if item not in parts and Path(item).is_dir()]
    if missing:
        os.environ["PATH"] = ":".join(missing + parts)


def _ensure_section(text: str, section: str) -> str:
    for line in text.splitlines():
        if _section_name(line) == section:
            return text
    tail = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{tail}\n[{section}]\n"


def parse_flash_max_s(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("闪念阈值必须是数字（秒）")
    number = float(value)
    if not 0 <= number <= 3600:
        raise ValueError("闪念阈值必须在 0 到 3600 秒之间")
    return number


def _parse_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label}必须是开或关")
    return value


def read_feature_settings_text(text: str) -> dict[str, object]:
    data = tomllib.loads(text)
    pipe = data.get("pipeline") or {}
    llm = data.get("llm") or {}
    obs = data.get("obsidian") or {}
    digest = data.get("digest") or {}
    return {
        "llm_enabled": bool(llm.get("enabled", False)),
        "obsidian_enabled": bool(obs.get("enabled", False)),
        "digest_enabled": bool(digest.get("enabled", False)),
        "flash_max_s": float(pipe.get("flash_max_s", 120)),
    }


def read_feature_settings(path: str | Path) -> dict[str, object]:
    return read_feature_settings_text(Path(path).read_text(encoding="utf-8"))


def update_feature_settings(
    path: str | Path,
    *,
    llm_enabled: object,
    obsidian_enabled: object,
    flash_max_s: object,
    digest_enabled: object | None = None,
) -> dict[str, object]:
    """总结开关、Obsidian 开关、闪念阈值（以及当日摘要开关）。校验后原子替换。"""
    path = Path(path)
    llm_on = _parse_bool(llm_enabled, "本机总结")
    obs_on = _parse_bool(obsidian_enabled, "写入 Obsidian")
    flash = parse_flash_max_s(flash_max_s)
    original = path.read_text(encoding="utf-8")
    updated = original
    for section in ("llm", "obsidian"):
        updated = _ensure_section(updated, section)
    updated = set_toml_key(updated, "pipeline", "flash_max_s", repr(flash) if flash != int(flash) else str(int(flash)))
    updated = set_toml_key(updated, "llm", "enabled", "true" if llm_on else "false")
    updated = set_toml_key(updated, "obsidian", "enabled", "true" if obs_on else "false")
    if digest_enabled is not None:
        digest_on = _parse_bool(digest_enabled, "当日摘要")
        updated = _ensure_section(updated, "digest")
        updated = set_toml_key(updated, "digest", "enabled", "true" if digest_on else "false")
    try:
        config_from_mapping(tomllib.loads(updated), base=path.parent)
    except (tomllib.TOMLDecodeError, ValueError, OSError, TypeError) as exc:
        raise ValueError(f"新配置无法解析，没有写入：{exc}") from exc
    parsed = read_feature_settings_text(updated)
    if parsed["llm_enabled"] != llm_on or parsed["obsidian_enabled"] != obs_on or parsed["flash_max_s"] != flash:
        raise ValueError("写入前校验没有对上，没有修改配置文件")
    if updated != original:
        _atomic_replace(path, updated)
    return parsed
