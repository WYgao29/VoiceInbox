"""热词表和纠错表。"""

from __future__ import annotations

from pathlib import Path

import pytest

from voiceinbox import vocab
from tests.conftest import make_cfg


def test_seed_and_migrate_legacy_hotwords(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    cfg.state_dir.mkdir(parents=True)
    (cfg.state_dir / "hotwords.txt").write_text("张三\nSol\n", encoding="utf-8")
    vocab.ensure_vocab(cfg)
    assert vocab.read_hotwords(cfg) == ["张三", "Sol"]
    assert not (cfg.state_dir / "hotwords.txt").exists()
    assert (cfg.state_dir / "hotwords.txt.migrated").exists()
    # 再跑一次不覆盖用户改过的词表
    cfg.hotwords_file.write_text("李四\n", encoding="utf-8")
    vocab.ensure_vocab(cfg)
    assert vocab.read_hotwords(cfg) == ["李四"]
    assert vocab.hotwords_file_for_asr(cfg) == cfg.hotwords_file
    vocab.write_hotwords(cfg, "")
    assert vocab.hotwords_file_for_asr(cfg) is None


def test_replacements_roundtrip_backup_and_apply(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    vocab.ensure_vocab(cfg)
    assert vocab.read_replacements(cfg) == {}
    vocab.add_replacement(cfg, "索尔", "Sol")
    rules = vocab.add_replacement(cfg, '引"号', "引号\\")
    assert rules == {"索尔": "Sol", '引"号': "引号\\"}
    assert vocab.read_replacements(cfg) == rules
    assert list((cfg.backup_dir / "vocab").iterdir())
    assert vocab.apply_replacements("我们做索尔，索尔项目", {"索尔": "Sol", "索尔项目": "Sol 项目"}) == "我们做Sol，Sol 项目"
    with pytest.raises(ValueError):
        vocab.add_replacement(cfg, "", "x")
    with pytest.raises(ValueError):
        vocab.add_replacement(cfg, "a\nb", "x")
    with pytest.raises(ValueError):
        vocab.add_replacement(cfg, "同", "同")
    vocab.remove_replacement(cfg, "索尔")
    assert "索尔" not in vocab.read_replacements(cfg)
