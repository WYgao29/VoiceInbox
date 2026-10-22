# Web 界面 v2 进度

这里记的是已经删除的静态网页（`voiceinbox/webstatic`）。现在的界面是 VoiceInbox 桌面应用，启动方式见 README 的「界面」。

## 已完成

### 阶段 1｜布局与列表

提交 `3ad5d5f`。

- 首页改成宽屏双栏：左边卡片列表，右边详情。只按电脑宽度排，没有手机断点。
- 卡片显示标题（有苹果录音标题就用，没有就用转写第一句）、录音时间、时长、最多三行正文预览。
- 按今天 / 昨天 / 更早分组。没有录音时间的归到更早。
- 打开页面默认只请求已转写（状态 `done`）。待处理、转写中、失败、跳过仍在筛选项里。
- 顶部全文搜索。命中正文时，预览改成关键词附近的片段，列表里用 `<mark>` 高亮，不把原文插进 HTML。
- 设置从侧栏挪到单独一页，内容还是原来的三项。系统状态页还在。导入仍在列表上方。
- 测试：`.venv/bin/python -m pytest -m "not e2e" -q` → 51 passed, 1 deselected。

### 阶段 2｜详情

提交 `bf9ba65`。

- 播放器放到详情顶部，旁边可以选 0.75× 到 2×。换录音后倍速保留。
- 全文用 21px、1.8 行高的宽版面，不再关在小预览框里。
- 可以复制，可以导出 txt（纯正文）和 md（标题、时间、时长、正文）。
- 正文里的搜索词用与列表相同的方式高亮。
- 测试：`.venv/bin/python -m pytest -m "not e2e" -q` → 52 passed, 1 deselected。

### 阶段 3｜状态与重转

提交 `89ac76e`。

- 状态改成：已转写、排队中、转写中、失败、太短已跳过。多次失败并进「失败」，界面和样式里不再出现内部状态名。
- 失败时显示一句人话原因，详情里有「重试这条」。批量重试放在顶部「更多」里，一次重试所有失败项。
- 背景说明和热词收进详情折叠区「重新转写」。去掉环境变量名的说明。
- 常用热词可编辑，保存到数据目录的 `state/hotwords.txt`（不写配置文件，也不动 `~/asr`）。「带上常用热词」会填进这一次的热词框。
- 测试：`.venv/bin/python -m pytest -m "not e2e" -q` → 54 passed, 1 deselected。

### 阶段 4｜设置页与系统状态

提交 `633c307`。

- 设置单独一页：起始日期、每轮最多几条、识别语言。说明是中文，页面和接口说明都不提配置文件名。
- 识别语言用中文选项：中文、英文、自动判断。
- 同一页用红绿灯显示后台任务是否在跑、上次扫描、完全磁盘访问。检查详情和原始日志默认折叠。
- 测试：`.venv/bin/python -m pytest -m "not e2e" -q` → 55 passed, 1 deselected。

### 阶段 5｜导入

提交 `efde660`。

- 顶部有「导入音频」按钮，也可以把文件拖到窗口上。
- 导入时显示上传进度，传完后显示「正在转写」，结束后显示中文状态。
- 测试：`.venv/bin/python -m pytest -m "not e2e" -q` → 56 passed, 1 deselected。

### 阶段 6｜视觉

提交 `1c95c8f`。

- 保留米色，加大标题和正文的字号差，列表筛选吸顶，正文行宽大约 44 字。
- 深色用同一套暖色，默认跟随系统。顶部按钮可在跟随系统、深色、浅色之间切换，选择记在本机浏览器里。地址加 `?theme=dark` 或 `?theme=light` 只影响这一次打开。
- 测试：`.venv/bin/python -m pytest -m "not e2e" -q` → 57 passed, 1 deselected。

## 下一步

收尾已完成。

- 全部单测再跑一次：`.venv/bin/python -m pytest -m "not e2e" -q` → 57 passed, 1 deselected in 9.52s。端到端没有跑。
- 截图在 `~/grokbuild-shots/voiceinbox-web2/`：`list-light.png`、`list-dark.png`、`detail-light.png`、`detail-dark.png`、`settings-light.png`、`settings-dark.png`。用的是临时目录、示例转写，以及假的检查和后台状态。没有读真实语音备忘录，没有加载模型。
- 截图用的临时服务已经停掉。没有占用 8791。
- 交付说明：`~/Downloads/GrokBot Handoff/iOS语音备忘录_Web界面v2_交付说明.md`。

## 约束（全程不要碰）

- 不改、不重签 `bin/voiceinbox-python`。不跑 `install_launchd.sh` / `build_launcher.sh`。不 `launchctl load/unload/bootstrap/kickstart`。
- 原录音只读。转写和 launchd 共用同一把锁。CSRF 保留。只监听 `127.0.0.1`。
- 只本地 commit，不 push。不动 `~/asr` 和其他项目。
