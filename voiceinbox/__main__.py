"""命令行：run-once / process / status / retry / doctor / web，以及 v3 的 enrich / summarize / vocab / digest / search / reindex / backfill。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from voiceinbox.config import ensure_tool_path, load_config
from voiceinbox.doctor import doctor
from voiceinbox.pipeline import process_path, retry, run_once, status_text
from voiceinbox.web import serve_forever


def main(argv: list[str] | None = None) -> int:
    ensure_tool_path()
    parser = argparse.ArgumentParser(prog="voiceinbox", description="语音备忘录 → Qwen3-ASR 纯文本")
    parser.add_argument("--config", default=None, help="config.toml 路径")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run-once", help="扫描一次并转写，然后退出")
    process = sub.add_parser("process", help="立刻转写一个音频文件")
    process.add_argument("file")
    sub.add_parser("status", help="查看各状态数量和最近记录")
    retry_cmd = sub.add_parser("retry", help="重试一条，或 all-dead")
    retry_cmd.add_argument("target", help="记录 id，或 all-dead")
    sub.add_parser("doctor", help="检查 ffmpeg、模型缓存和完全磁盘访问")
    web_cmd = sub.add_parser("web", help="本机 API，只绑定 127.0.0.1。界面是桌面应用")
    web_cmd.add_argument("--host", default=None, help="只能是 127.0.0.1")
    web_cmd.add_argument("--port", default=None, help="默认 8791；不能用 8000 或 8765")
    enrich_cmd = sub.add_parser("enrich", help="对已转写的录音做纠错、总结、写 Obsidian（只处理需要的）")
    enrich_cmd.add_argument("ids", nargs="*", help="记录 id；不填就处理所有需要处理的")
    summ = sub.add_parser("summarize", help="换模板重新总结一条（不动原始转写）")
    summ.add_argument("id")
    summ.add_argument("--template", default="auto", help="会议 / 访谈 / 课程 / 通话 / 随想 / 其他 / auto")
    voc = sub.add_parser("vocab", help="热词和纠错表")
    voc_sub = voc.add_subparsers(dest="vocab_cmd", required=True)
    voc_sub.add_parser("show", help="列出热词和纠错")
    add = voc_sub.add_parser("add-fix", help="加一条纠错：错 对")
    add.add_argument("wrong")
    add.add_argument("right")
    rm = voc_sub.add_parser("rm-fix", help="删一条纠错")
    rm.add_argument("wrong")
    hw = voc_sub.add_parser("add-hotword", help="加热词")
    hw.add_argument("words", nargs="+")
    voc_sub.add_parser("apply", help="把纠错表重新套到所有转写和总结上（不调用 LLM）")
    dig = sub.add_parser("digest", help="立刻生成某天的当日摘要")
    dig.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天")
    mon = sub.add_parser("monitor", help="后台监控：show / on / off / interval 分钟 / watch on|off / render 路径")
    mon.add_argument("action", choices=["show", "on", "off", "interval", "watch", "render"])
    mon.add_argument("value", nargs="?")
    srch = sub.add_parser("search", help="全文搜索转写和总结")
    srch.add_argument("query")
    srch.add_argument("--from", dest="date_from", default=None)
    srch.add_argument("--to", dest="date_to", default=None)
    sub.add_parser("reindex", help="重建搜索索引")
    sub.add_parser("backfill", help="把所有已转写的录音走一遍新步骤，并补齐当日摘要")
    args = parser.parse_args(argv)

    if args.cmd == "doctor":
        try:
            cfg = load_config(args.config)
        except Exception as exc:
            print(f"配置错误：{exc}", file=sys.stderr)
            return 1
        code, text = doctor(cfg)
        print(text, end="")
        return code

    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1

    if args.cmd == "run-once":
        stats = run_once(cfg)
        if stats.fda_blocked:
            print(
                "读不到语音备忘录目录（没有完全磁盘访问）。运行 python -m voiceinbox doctor 查看要授权的程序。",
                file=sys.stderr,
            )
            return 2
        return 0
    if args.cmd == "status":
        print(status_text(cfg), end="")
        return 0
    if args.cmd == "process":
        try:
            stats, rec = process_path(cfg, Path(args.file))
        except Exception as exc:
            print(f"失败：{exc}", file=sys.stderr)
            return 1
        if stats.locked or rec is None:
            print("另一实例正在运行，请稍后再试")
            return 3
        if rec.status == "transcribed":
            print(f"已转写 {rec.id}")
            print(f"文本：{rec.text_path}")
            return 0
        if rec.status == "skipped":
            print(f"已跳过 {rec.id}：{rec.last_error or ''}")
            return 0
        print(f"未完成 {rec.id} 状态 {rec.status}：{rec.last_error or ''}")
        return 1
    if args.cmd == "monitor":
        return _monitor(args)
    if args.cmd == "web":
        return serve_forever(args.config, args.host, args.port)
    if args.cmd == "retry":
        try:
            stats = retry(cfg, args.target)
        except KeyError:
            print(f"没有这条记录：{args.target}", file=sys.stderr)
            return 1
        if stats.locked:
            print("另一实例正在运行，请稍后再试")
            return 3
        print(status_text(cfg), end="")
        return 0 if stats.failed == 0 else 1
    if args.cmd in {"enrich", "summarize", "vocab", "digest", "search", "reindex", "backfill"}:
        return _v3(cfg, args)
    print(f"未知命令：{args.cmd}", file=sys.stderr)
    return 2


def _v3(cfg, args) -> int:
    from datetime import datetime

    from voiceinbox import enrich, search, vocab
    from voiceinbox.lock import FileLock
    from voiceinbox.log import setup_logging
    from voiceinbox.pipeline import ensure_dirs
    from voiceinbox.state import State
    from voiceinbox.timeutil import now as now_in

    ensure_dirs(cfg)
    setup_logging(cfg.logs_dir)
    try:
        if args.cmd == "search":
            for hit in search.search(cfg.search_db_path, args.query, date_from=args.date_from, date_to=args.date_to):
                at = search.approx_seconds(hit)
                pos = f" 约 {int(at // 60)}:{int(at % 60):02d}" if at is not None else ""
                print(f"{hit['id']}  {hit['recorded_at'][:16]}  {hit['title']}  [{hit['field']}{pos}]  {hit['snippet']}")
            return 0
        if args.cmd == "vocab":
            if args.vocab_cmd == "show":
                print("热词：" + "、".join(vocab.read_hotwords(cfg)))
                for wrong, right in vocab.read_replacements(cfg).items():
                    print(f"纠错：{wrong} → {right}")
                print(f"文件：{cfg.hotwords_file}  {cfg.replacements_file}")
                return 0
            if args.vocab_cmd == "add-fix":
                vocab.add_replacement(cfg, args.wrong, args.right)
                print(f"已加：{args.wrong} → {args.right}。运行 vocab apply 套到已有转写上。")
                return 0
            if args.vocab_cmd == "rm-fix":
                vocab.remove_replacement(cfg, args.wrong)
                print(f"已删：{args.wrong}")
                return 0
            if args.vocab_cmd == "add-hotword":
                words = vocab.read_hotwords(cfg) + list(args.words)
                print("热词：" + "、".join(vocab.write_hotwords(cfg, words)))
                return 0
            if args.vocab_cmd == "apply":
                print(f"已重新套用纠错 {enrich.reapply_corrections(cfg)} 条")
                return 0
        if args.cmd == "summarize":
            result = enrich.regenerate(cfg, args.id, template=args.template)
            print(f"已重新总结 {args.id}：{result.get('type')} · {result.get('title')} · 模板 {result.get('template')}")
            if result.get("obsidian_path"):
                print(f"Obsidian：{result['obsidian_path']}")
            return 0
        if args.cmd == "digest":
            day = args.date or now_in(cfg.timezone).strftime("%Y-%m-%d")
            datetime.strptime(day, "%Y-%m-%d")
            path = enrich.digest_now(cfg, day)
            print(f"当日摘要：{path}" if path else f"{day} 没有可以汇总的录音")
            return 0
        with FileLock(cfg.lock_path) as got:
            if not got:
                print("另一实例正在运行，请稍后再试")
                return 3
            state = State(cfg.db_path)
            if args.cmd == "reindex":
                print(f"已重建索引 {enrich.reindex(cfg, state)} 条")
                return 0
            if args.cmd == "enrich":
                stats = enrich.enrich_pending(cfg, state, ids=args.ids or None)
            else:  # backfill
                ids = [rec.id for rec in state.list_by_status("transcribed")]
                stats = enrich.enrich_pending(cfg, state, ids=ids)
                box = enrich.ClientBox(cfg)
                try:
                    days = sorted({d for d in (enrich._day_of(r) for r in state.list_by_status("transcribed")) if d})
                    for day in days:
                        if cfg.llm_enabled and cfg.obsidian_enabled and enrich.run_digest(cfg, state, box, day):
                            stats.digests += 1
                finally:
                    box.close()
            print(
                f"整理 {stats.enriched} 条：总结 {stats.llm_done}，写入 Obsidian {stats.exported}，"
                f"等 LLM {stats.waiting_llm}，失败 {stats.failed}，当日摘要 {stats.digests}"
            )
            for err in stats.errors:
                print("  " + err, file=sys.stderr)
            return 0 if stats.failed == 0 else 1
    except enrich.Locked:
        print("另一实例正在运行，请稍后再试")
        return 3
    except KeyError as exc:
        print(f"没有这条：{exc}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    return 2



def _monitor(args) -> int:
    from pathlib import Path

    from voiceinbox import monitor
    from voiceinbox.config import default_config_path, load_config

    config_path = Path(args.config) if args.config else default_config_path()
    desired = monitor.read_desired(config_path)
    if args.action == "render":
        # install_launchd.sh 用：按 config.toml 的 [monitor] 生成要安装的 plist。
        if not args.value:
            print("要给出 plist 路径", file=sys.stderr)
            return 2
        Path(args.value).write_bytes(monitor.render_plist(desired))
        print("enabled" if desired["enabled"] else "disabled")
        return 0
    if args.action != "show":
        changes = dict(desired)
        if args.action in {"on", "off"}:
            changes["enabled"] = args.action == "on"
        elif args.action == "interval":
            changes["interval_min"] = args.value
        elif args.action == "watch":
            changes["watch"] = args.value == "on"
        try:
            monitor.write_desired(config_path, **changes)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        cfg = load_config(config_path)
        print(monitor.Monitor(config_path, cfg.lock_path, cfg.logs_dir, cfg.backup_dir / "launchd").apply(background=False))
    cfg = load_config(config_path)
    st = monitor.Monitor(config_path, cfg.lock_path, cfg.logs_dir, cfg.backup_dir / "launchd").status()
    print(f"后台监控：{st['state_label']}，每 {st['interval_min']} 分钟，立即处理新录音：{'开' if st['watching'] else '关'}")
    print(f"上次：{st['last_run'] or '—'}  下次约：{st['next_run'] or '—'}  与配置一致：{'是' if st['in_sync'] else '否'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
