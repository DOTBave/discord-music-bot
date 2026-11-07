# discord-music-bot

一个自用的 Discord 音乐机器人：语音频道播放 + 简单聊天。

支持播放本地文件、B 站及 YouTube 视频/音频。其中 B 站与 YouTube 在线解析均需配置 Cookies（配置方法详见 [How do I pass cookies to yt-dlp?](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)）。队列状态机（`state['current']` + `audio_after` 出队）集中在 `main.py`，彩蛋独立在 `easter_egg.py`。

## 功能

- `/play` 播放链接、关键词搜索或本地文件，支持队列和 `loop`
- `/skip` `/stop` `/join` `/disconnect` 控制播放
- `/dir` 列出 `Music/` 下可播放的文件（mp3/mp4/wav/m4a/ogg）
- `$kickbot <username>` 隐藏指令，用于静默地将指定用户移出语音频道，需要bot有该权限
- 被 @ 时用 Gemini 结合频道最近几条消息回复
- 发"我坐好了"触发彩蛋（`easter_egg.py`，不想留可以直接删掉这个文件和 main.py 里的两处引用）

## 依赖

- Python 3.12（Docker 镜像里已经装好）
- ffmpeg、deno：yt-dlp 取流和 JS 挑战用
- cookies.txt：B 站部分内容（如高码率）需要登录态
- Docker 构建使用 `requirements.lock`（uv 生成的跨平台精确锁文件），本地开发也可以直接 `pip install -r requirements.txt`
- 镜像里的 Deno 版本固定在 Dockerfile 的 `ARG DENO_VERSION`，构建时按 CPU 架构下载并校验 sha256；基础镜像也按 digest 固定（升级时更新 Dockerfile 的 FROM）

## 配置

复制 `.env.example` 为 `.env`：

| 变量 | 说明 |
| --- | --- |
| `DISCORD_TOKEN` | 必填，Discord Bot Token |
| `GEMINI_API_KEY` | 可选，聊天功能用；不配置则禁用聊天回复，音乐播放不受影响 |
| `DENO` | 可选，deno 路径；留空则从 PATH 找，路径失效会自动回退 |
| `COOKIES_FILE` | 可选，默认 `cookies.txt` |
| `YOUTUBE_CLIENTS` | 可选，YouTube 客户端尝试顺序，默认 `android_vr,mweb` |
| `YTDLP_VERBOSE` | 可选，设为 `1` 打开 yt-dlp 调试日志 |
| `GEMINI_PROMPT` | 可选，Gemini 系统提示词 |
| `GEMINI_MODEL` | 可选，Gemini 模型名，默认 gemini-3.1-flash-lite |

Discord 开发者后台需要开启 **Message Content Intent**；机器人要有语音权限。

## 运行

Docker：

```bash
# cookies.txt 必须先存在（空文件也行）；文件不存在时 Docker 会在挂载点创建空目录，导致所有 /play 报 cookie 加载失败
touch cookies.txt
docker build -t music-bot .
docker run -d --name my-music-bot --env-file .env \
  -v "$PWD/Music:/app/Music" \
  -v "$PWD/etc:/app/etc" \
  -v "$PWD/cookies.txt:/app/cookies.txt:ro" \
  music-bot
```

Cookies 也可以放在 `data/` 目录里，挂载 `-v "$PWD/data:/app/data"`，并在 `.env` 里设置 `COOKIES_FILE=data/cookies.txt`。注意：宿主机上文件不存在时，Docker 会在挂载点自动创建一个空目录，yt-dlp 读取 cookie 会报错，表现为每次 `/play` 都失败，所以运行前务必先 `touch` 出文件。

`docker-compose.yml` 示例（仓库里没有，按需自建）：

```yaml
services:
  bot:
    build: .
    container_name: my-music-bot
    env_file: .env
    # 运行前先执行 touch cookies.txt
    volumes:
      - ./Music:/app/Music
      - ./etc:/app/etc
      - ./cookies.txt:/app/cookies.txt:ro
    restart: unless-stopped
```

非交互环境（如 `docker run -d`）下控制台输入会被自动禁用；Dockerfile 里设置了 `PYTHONUNBUFFERED=1`，日志可以用 `docker logs -f my-music-bot` 实时查看。

镜像以非 root 用户 `app`（uid 1000）运行，挂载进去的 `Music/`、`etc/`、`cookies.txt` 需要对 uid 1000 可读。若 `cookies.txt` 是 600 权限，先 `chmod 644 cookies.txt`，或改用 `docker run --user "$(id -u):$(id -g)" ...`。

本地：

```bash
pip install -r requirements.txt
python main.py
```

日志里出现 `JS runtime: deno -> ...` 说明 deno 认到了；找不到会打印警告。

## 目录

```
main.py             主程序：命令、队列、yt-dlp 抽取
easter_egg.py       彩蛋模块，与主程序只通过 handle() 交互
tests/              回归测试（pytest，不依赖网络/语音）
Music/              本地音频，运行时挂载进容器
etc/                彩蛋素材（图片）
cookies.txt         B 站/YouTube cookie，不进版本库
requirements.lock   精确依赖锁（Docker 使用）
.github/workflows/  CI（ruff + pytest + docker build）
```

`Music/` 和 `etc/` 都是按**启动目录**的相对路径找的，所以要在仓库根目录运行；Docker 里对应 `/app`。素材缺失不会崩：彩蛋会退化成只发文案。

## 已知限制

- **YouTube**：从数据中心 IP（VPS）访问时，YouTube 会要求登录验证，各客户端轮换也过不去，属 IP 层面的限制。家用网络正常，B 站不受影响。
- **B 站**：没有大会员时 yt-dlp 会提示高码率格式缺失，自动降级到可用格式，首次播放会慢一些。
- 每次失败都会把 yt-dlp 的原始报错发到频道，方便排查；不希望刷屏可以调 `YTDLP_VERBOSE` 或按需改提示语。

## 开发备注

- `/help` 的文案由 `bot.tree.get_commands()` 生成，加了新命令不用再手写帮助
- `source_obj_compiler()` 返回 3 元组（source, title, error），`build_play_source()` 返回 3 元组（source, title, stream_url）；yt-dlp 的报错改为直接返回，不再存到全局变量，改返回值时记得同步所有解包点（解包数量对不上只会在运行时炸）
- 队列的出队只在 `audio_after()` 里做，`play_next()` 只"看"队首，不要在两处都 pop
- `audio_after()` 出队按对象身份（`is`）匹配，而不是比较 dict 是否相等——同一首歌可能被连续点两次
- 回归测试：`python -m pytest -q`（不依赖网络、Discord token 或语音）；CI 见 `.github/workflows/ci.yml`（ruff + pytest + docker build）
- 更新依赖锁：`uv pip compile requirements.txt --universal --python-version 3.12 -o requirements.lock`
