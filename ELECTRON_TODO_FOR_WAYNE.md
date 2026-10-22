# 留给 Wayne 的授权事项

今晚没有申请或触发系统权限，没有签名，没有公证，没有改 launchd，没有动 `bin/voiceinbox-python`，没有提交 `config.toml`。

## 先看这个：App 未捆绑后端

`release/` 里的 VoiceInbox.app **没有**把 Python 后端打进去。打包排除了 `voiceinbox/`、`.venv/`、`bin/` 和 `config.toml`。包里没有解释器，也没有这份配置。

- 应用要留在仓库的 `release/` 里，才会从所在位置向上找到仓库。
- 挪走之前先设置环境变量 `VOICEINBOX_ROOT`，指向这个仓库。变量一旦设置，应用只认这个路径。
- 路径不存在，或从应用所在位置找不到仓库时，会弹出中文说明：原因是未捆绑后端，办法是留在 `release/` 里，或把 `VOICEINBOX_ROOT` 指到仓库根目录。指错时不会悄悄改用别的目录。

## 明天可以做

1. 完全磁盘访问继续只加这一个文件，不要加 `.venv/bin/python`，也不要加 Electron 或 VoiceInbox.app：

   `<仓库根目录>/bin/voiceinbox-python`

   桌面应用自拉起后端时，优先执行上面这个已经授权的 `bin/voiceinbox-python`（文件存在且可执行）。它不存在时才退回 `.venv/bin/python`。应用只调用这个文件，不会重新生成、签名或修改它，也不会跑 `scripts/build_launcher.sh` 或 `scripts/install_launchd.sh`。设置页和顶栏会显示这次用的解释器路径。真正按间隔扫语音备忘录的仍是现有的 launchd 任务。

2. 签名和公证没有做。`release/VoiceInbox-darwin-arm64/VoiceInbox.app` 未签名。从仓库里用下面的命令打开即可。若把 `.app` 拷到「应用程序」或换一台机器，Gatekeeper 可能拦截，需要你自己决定要不要签名。

   ```bash
   open "release/VoiceInbox-darwin-arm64/VoiceInbox.app"
   ```

   应用必须留在仓库的 `release/` 目录，才能向上找到仓库里的后端和 `config.toml`。打包继续排除 `config.toml`。如果要挪走，先设置 `VOICEINBOX_ROOT` 指向这个仓库。详见上面「App 未捆绑后端」。

3. `config.toml` 里有你未提交的改动。这次没有提交，也没有覆盖。需要的话你自己提交。

4. launchd 的 `com.wayne.voiceinbox` 没有被改。设置页的监控开关、间隔、「有新录音时立即处理」和「立即检查一次」会调用 `launchctl`。开关旁写着「会修改后台任务 com.wayne.voiceinbox」。每次写之前会再问一次，取消就不会发请求。今晚没有点这些按钮。

5. 通知、麦克风、屏幕录制都没有申请。截图用的是 Electron `webContents.capturePage`，不需要屏幕录制权限。

## 怎么开

8791 上如果已经有健康的 voiceinbox（现在就是你开的那个），应用会复用，退出时不关它。只有应用自己拉起的后端，退出时才会关掉。

开发和核对用另一个端口，核对完要关掉：

```bash
release/VoiceInbox-darwin-arm64/VoiceInbox.app/Contents/MacOS/VoiceInbox --backend-port=8792
```
