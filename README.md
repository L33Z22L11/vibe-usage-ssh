# Vibe Usage SSH

通过 SSH 查看 [VibeFriends 用量](https://vibefriends.com/usage)（下称 Vibe Usage）的小看板，使用 Python、AsyncSSH 和 Textual。支持鼠标和能传递终端鼠标事件的触屏客户端，也可以通过 frpc 内网穿透，让朋友从外网通过 SSH 连接，看看你的用量。

- 费用、Token、缓存命中率和活跃时长。
- 今天、24 小时、7/30/90 天与自定义日期。
- 终端、工具、模型、项目筛选。
- 小时/日柱状图，悬停查看详情；窄屏横向滚动，每根柱子保持原始时间粒度。
- 终端、工具、模型、项目分布，分别切换 Token/费用。
- 点击费用切换美元/人民币，点击 Token 切换 K/M/B 与万/亿；下方数值同步。
- 默认跟随终端原生色板，也可切换 Catppuccin Mocha 等内置主题；宽窄屏自适应，支持 Nerd Font 图标。

## 安装运行

需要 Python 3.10+，支持 macOS 和 Linux。

克隆仓库并安装：

```sh
git clone https://github.com/L33Z22L11/vibe-usage-ssh.git
cd vibe-usage-ssh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp .env.example .env
```

在 [Vibe Usage 设置](https://vibefriends.com/usage/setup)生成 API Key，然后编辑 `.env`，填入自己的 API 地址和 Key：

```dotenv
VIBE_API_URL=https://vibefriends.com
VIBE_API_KEY=your-api-key
VIBE_SSH_HOST=127.0.0.1
VIBE_SSH_PORT=5163
```

启动后连接：

```sh
sh scripts/run.sh
# 在本机连接：
ssh -p 5163 127.0.0.1
```

用户名任意，无需密码。SSH 连接进入看板，不提供系统 Shell。

通过 `VIBE_SSH_HOST` 选择监听地址：默认 `127.0.0.1`，只有本机能连接；填写某个网卡的 IP 就只监听该地址，填写 `0.0.0.0` 则监听所有 IPv4 接口。服务接受所有能到达该监听地址的客户端，无需密码。

修改 `.env` 后重启服务，再用主机对应的 IP 地址连接。

已配置 Vibe Usage 桌面端的用户可以省略 API 环境变量，程序会读取 `~/.vibe-usage/config.json`。SSH 主机密钥首次启动时生成在 `~/.local/share/vibe-usage-ssh/host_key`；也可通过 `VIBE_SSH_HOST_KEY` 指定已有密钥。

## 操作

| 操作                     | 方法                      |
| ------------------------ | ------------------------- |
| 筛选数据                 | 点击顶部下拉框            |
| 重置筛选和时间范围       | `x`（回到 24H，保留主题） |
| 切换币种                 | 点击费用卡片，或按 `c`    |
| 切换 Token 单位          | 点击 Token 卡片，或按 `t` |
| 顶栏吸顶开关（默认关闭） | `s`                       |
| 刷新                     | `r`                       |
| 精确数字                 | `e`                       |
| 轮换主题                 | `p`                       |
| 选择主题/恢复终端配色    | `Ctrl+P`                  |
| 关闭日期弹窗             | Esc 或点击背景            |
| 退出                     | `q`                       |

默认整页一起滚动；开启吸顶后，时间选择和筛选固定在顶部。Token 柱子用文本色显示未缓存部分、淡化文本色显示缓存部分，费用使用主题绿色，活跃时长使用分布条形图的第一色。

顶部卡片也支持 Tab 聚焦后按 Enter/空格操作。底栏可点击。客户端使用 Nerd Font 时可正常显示图标。

SSH 会按来源 IP、客户端标识和用户名的组合记住筛选、时间范围及主题，保存在服务器的 `~/.local/share/vibe-usage-ssh/preferences/`。同一组合重新连接会恢复偏好；IP 或客户端版本变化时可能会建立新的配置。不同设备经同一出口、使用相同客户端和用户名时，也可能共用配置。

人民币换算使用 [Frankfurter](https://frankfurter.dev/) 最新 USD/CNY 参考汇率，每小时刷新。汇率暂不可用时显示 `¥—`，已获取过的汇率会保留在当前会话。费用来自 Vibe Usage 估算，活跃时长来自会话数据；会话没有模型字段，因此模型筛选不改变活跃时长。缓存命中率按缓存读取 ÷（未缓存输入 + 缓存读取）计算。

## 常驻运行

macOS：

```sh
.venv/bin/python scripts/install-launch-agent.py
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.vibe-usage.ssh.plist
```

登录时自动启动。日志在 `~/Library/Logs/vibe-usage-ssh.log`。停止：

```sh
launchctl bootout gui/$(id -u)/dev.vibe-usage.ssh
```

Linux：将 `examples/vibe-usage-ssh.service` 中的两处项目路径改为实际绝对路径，保存到 `~/.config/systemd/user/vibe-usage-ssh.service`，然后运行：

```sh
systemctl --user daemon-reload
systemctl --user enable --now vibe-usage-ssh
```

需要开机启动、退出登录后继续运行时，启用当前用户的 linger：

```sh
sudo loginctl enable-linger "$USER"
```

查看状态和日志：

```sh
systemctl --user status vibe-usage-ssh
journalctl --user -u vibe-usage-ssh -f
```

停止服务并取消自动启动：

```sh
systemctl --user disable --now vibe-usage-ssh
```

用量请求、汇率和统计失败的原因会写入服务日志，包含错误类型、HTTP 状态码或代码位置，不记录 API Key、请求内容和用量数据。

## 开发

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff format .
.venv/bin/ruff check .
.venv/bin/python -m unittest discover -s tests -v
```

`vibe_usage_ssh/` 中，`ui.py` 负责页面与交互，`charts.py` 负责主图表和气泡，`widgets.py` 放筛选、日期弹窗和分布卡片组件，样式放在 `dashboard.tcss`。统计计算、API、SSH 连接和偏好保存分别在独立模块中。

测试放在 `tests/`，服务脚本与示例分别放在 `scripts/`、`examples/`。依赖和工具配置统一在 `pyproject.toml`。

## License

MIT
