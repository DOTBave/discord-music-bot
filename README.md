# discord-music-bot

一个自用的 Discord 音乐机器人，支持在语音频道播放本地音频、B 站与 YouTube 音视频，并集成 Gemini 群聊回复与独立彩蛋模块。

## 功能列表

### 音乐与语音指令
- `/play [url] [loop] [file]`：播放 URL（支持分享文案混排自动提取）、关键词搜索（`ytsearch5`）或 `Music/` 下的本地文件；支持队列排队与单曲循环
- `/skip`：跳过当前曲目
- `/stop`：停止播放并清空队列（同时作废在途加载，回收 FFmpeg 子进程）
- `/join`：加入或切换到你所在的语音频道
- `/disconnect`：退出语音频道并释放当前服务器的队列状态
- `/dir`：列出 `Music/` 下可播放的文件（`mp3`、`mp4`、`wav`、`m4a`、`ogg`）

### 互动与管理
- `@机器人 <内容>`：结合频道最近 7 条消息上下文调用 Gemini 回复（未配置 `GEMINI_API_KEY` 时自动禁用聊天，不影响音乐功能）
- `/hello`、`/like`、`/dislike`、`/help`：基础交互与自动生成的命令帮助
- `$kickbot <username>`：隐藏前缀指令，静默删除指令消息并将指定成员移出语音频道（需要机器人拥有对应权限）
- 彩蛋触发：在白名单服务器语音频道内发送 `我坐好了` 触发图文与插队音频（逻辑独立在 [easter_egg.py](easter_egg.py)，不想保留可移除该文件及 `main.py` 中的引用）

## 环境与依赖

- **Python 3.12**（Docker 镜像内已内置）
- **FFmpeg**：用于音频转码与流媒体拉取
- **Deno**：用于 `yt-dlp` 执行 JS 挑战（Dockerfile 内按架构固定安装 `DENO_VERSION=2.9.8` 并校验 sha256）
- **DAVE 语音加密兼容性**：当前使用 `discord.py==2.7.1` 时，依赖中必须将 `davey` 锁定在 **`>=0.1.0,<0.2.0`**（`requirements.lock` 锁定为 `davey==0.1.7`）。`davey 0.2.0` 的会话过渡握手接口有破坏性变更，`discord.py 2.7.1` 尚未适配，若误装会导致连接成功且绿圈亮起但语音全程无声
- **Cookies**：B 站与 YouTube 在线解析按需配置 `cookies.txt`（格式说明见 [yt-dlp Cookies FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)）

## 配置（`.env`）

复制 `.env.example` 为 `.env` 并按需修改：

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | 是 | - | Discord Bot Token |
| `GEMINI_API_KEY` | 否 | - | Gemini API Key；留空则关闭 `@` 聊天回复 |
| `GEMINI_MODEL` | 否 | `gemini-3.1-flash-lite` | 使用的 Gemini 模型名 |
| `GEMINI_PROMPT` | 否 | 内置群聊助手提示词 | Gemini 系统提示词 |
| `DENO` | 否 | 自动从 `PATH` 查找 | Deno 可执行文件路径；路径失效会自动回退 |
| `COOKIES_FILE` | 否 | `cookies.txt` | Cookies 文件路径 |
| `YOUTUBE_CLIENTS` | 否 | `android_vr,mweb` | YouTube 默认抽取失败后的客户端回退顺序 |
| `YTDLP_VERBOSE` | 否 | `0` | 设为 `1` / `true` 输出 `yt-dlp` 详细调试日志 |
| `FFMPEG_PATH` | 否 | `ffmpeg` | FFmpeg 可执行文件路径 |

> Discord Developer Portal 中需开启 **Message Content Intent**，并授予机器人连接语音、说话及（如需使用 `$kickbot`）移动成员、管理消息等权限。

## 运行指南

### Docker 运行（推荐）

镜像使用基于 digest 固定的 `python:3.12-slim`，通过 `requirements.lock` 安装精确依赖，并以非 root 用户 `app`（uid `1000`）运行。

```bash
# 1. 提前确保 cookies.txt 存在（哪怕是空文件）；否则 Docker 会将其挂载成空目录导致每次 /play 失败
touch cookies.txt

# 2. 构建镜像
docker build -t music-bot .

# 3. 启动容器
docker run -d --name my-music-bot --env-file .env \
  -v "$PWD/Music:/app/Music" \
  -v "$PWD/etc:/app/etc" \
  -v "$PWD/cookies.txt:/app/cookies.txt:ro" \
  music-bot
```

`docker-compose.yml` 示例（按需自建）：

```yaml
services:
  bot:
    build: .
    container_name: my-music-bot
    env_file: .env
    # 启动前请先确保宿主机存在 cookies.txt（如 touch cookies.txt）
    volumes:
      - ./Music:/app/Music
      - ./etc:/app/etc
      - ./cookies.txt:/app/cookies.txt:ro
    restart: unless-stopped
```

**挂载与权限说明：**
- **只读 Cookies（`:ro`）**：`yt-dlp` 在关闭时默认会回写 Cookie。当检测到 `cookies.txt` 为只读时，程序会在启动时将其复制到权限为 `0600` 的容器临时文件再交给 `yt-dlp`（并在退出时自动清理），不会触发 `[Errno 30] Read-only file system`，但刷新的 Cookie 也不会写回宿主机；若需持久写回，去掉 `:ro` 即可。也可将 Cookies 放入 `data/` 目录并挂载 `-v "$PWD/data:/app/data"`，配合 `COOKIES_FILE=data/cookies.txt`。
- **容器内用户权限**：因容器以 uid `1000` 运行，宿主机挂载的 `Music/`、`etc/` 与 `cookies.txt` 需对 uid `1000` 可读（例如 `chmod 644 cookies.txt`，或改用 `docker run --user "$(id -u):$(id -g)" ...`）。
- **日志与控制台输入**：非交互环境（如 `docker run -d`）下标准输入监听会自动禁用；镜像已设置 `PYTHONUNBUFFERED=1`，可通过 `docker logs -f my-music-bot` 实时查看日志。

### 本地运行

```bash
pip install -r requirements.txt
python main.py
```

启动日志中出现 `JS runtime: deno -> ...` 表示 Deno 识别正常。`Music/` 和 `etc/` 均相对启动工作目录解析，请在仓库根目录执行命令。

## 目录结构

```text
main.py             主程序：斜杠/前缀命令、播放状态机、yt-dlp 抽取与 Gemini 聊天
easter_egg.py       彩蛋模块（与主程序通过 handle() 交互；缺失图片时自动降级为纯文本）
tests/              回归测试套件（pytest，不依赖网络与真实 Discord 语音）
Music/              本地音频目录（运行时挂载，不进版本库）
etc/                彩蛋图片素材目录
cookies.txt         B 站 / YouTube Cookies（不进版本库）
requirements.txt    抽象依赖声明（含 davey 版本上限约束）
requirements.lock   uv 生成的跨平台精确依赖锁（Docker / CI 使用）
.github/workflows/  GitHub Actions CI（compileall + ruff + pytest + docker build 冒烟测试）
```

## 取流策略与常见问题排查

### 1. 音频优先与低带宽回退策略
`YDL_OPTIONS` 采用“纯音频优先、合并流保音质降画质”的选择策略：
- **优先直抓纯音频流（`bestaudio`）**：先选取普通有损音频流（排除体积过大且服务器跨国拉流易卡顿的 `flac` 无损流），仅在无其他选择时回退到 `flac`。
- **无独立音频流时的合并流回退（`best`）**：当站点只提供音视频合并流时，通过 `format_sort`（`hasaud, abr, asr, channels, +res, +fps, +vbr, +tbr, +size`）**先按音质字段降序、再按视频分辨率/帧率/视频码率/体积升序**，自动选中在不牺牲音质的前提下视频流最小、画质最低的格式，避免 FFmpeg `-vn` 丢弃视频流时白白浪费大量带宽。

### 2. B 站 `HTTP Error 412: Precondition Failed` 风控
B 站返回 `412` 通常是触发了网页端反爬/账号风控（`bili-sec`），常见原因与处理方式：
- **追踪/指纹参数**：`split_url()` / `clean_bilibili_url()` 已自动剔除 B 站 `/video/` 链接中的 `spm_id_from`、`vd_source`、`share_source` 等追踪参数和 `#fragment`（仅保留分 P 参数 `?p=`，并将 `m.bilibili.com` 规范化为 `www.bilibili.com`）。
- **大会员账号异地防共享风控（重点）**：如果使用**开通了大会员（含 7 天体验卡）的主力账号**导出 `cookies.txt` 给云服务器/海外机房 IP 使用，当本地设备与机房 IP 同时活跃或账号处于会员态并发请求时，极易触发 B 站防共享/防代下风控导致 `412`（IP 通常会被临时封禁 10～30 分钟）。
  - **推荐做法**：专门用无痕窗口登录一个**没有大会员的 B 站小号**导出 `cookies.txt` 给服务器独占使用（听歌场景普通 `m4a` 音轨完全够用，极少触发异地 `412`）。
- **请求头**：已移除过时的固定 `Chrome/124` UA 覆写，默认交由 `yt-dlp` 内置 `std_headers` 生成现代 Chrome 请求头。

### 3. YouTube 限制
从部分数据中心/VPS IP 访问 YouTube 时可能被要求登录验证（即使轮换 `YOUTUBE_CLIENTS` 也无法绕过），属于出口 IP 层面限制；家用网络通常正常，B 站不受该限制影响。

## 开发备注

- **帮助文案**：`/help` 内容由 `bot.tree.get_commands()` 自动生成，新增斜杠命令后无需手写同步（需自定义格式时补充 `HELP_USAGE`）。
- **函数返回约定**：`source_obj_compiler()` 返回 `(source, title, error)` 三元组，`build_play_source()` 返回 `(source, title, stream_url)` 三元组；非搜索场景的多分 P 列表（如 B 站合集/分 P）在 `extract_flat=False` 已产出直链时直接构造音源，避免丢掉防盗链 `Referer` 二次解析报 `403`。
- **播放状态机约定**：
  - 正常播完后的出队仅在 `audio_after()` 中按对象身份（`is`）匹配执行，支持同一首歌连续重复入队。
  - `play_next()` 仅在加载失败/异常时清理队首，并通过 `load_gen` 代次校验确保 `/stop`、`/disconnect` 后迟到的异步加载自动作废并调用 `source.cleanup()` 释放 FFmpeg 进程。
  - `voice_client.play(..., after=...)` 回调运行在 `AudioPlayer` 后台线程，必须通过 `_schedule()`（`asyncio.run_coroutine_threadsafe`）投递回主事件循环。
- **测试与锁文件**：
  - 运行离线回归测试：`python -m pytest -q`
  - 更新精确依赖锁：`uv pip compile requirements.txt --universal --python-version 3.12 -o requirements.lock`
