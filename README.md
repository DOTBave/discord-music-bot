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

## 配置

复制 `.env.example` 为 `.env`：

| 变量 | 说明 |
| --- | --- |
| `DISCORD_TOKEN` | 必填，Discord Bot Token |
| `GEMINI_API_KEY` | 必填，聊天功能用 |
| `DENO` | 可选，deno 路径；留空则从 PATH 找，路径失效会自动回退 |
| `COOKIES_FILE` | 可选，默认 `cookies.txt` |
| `YOUTUBE_CLIENTS` | 可选，YouTube 客户端尝试顺序，默认 `android_vr,mweb` |
| `YTDLP_VERBOSE` | 可选，设为 `1` 打开 yt-dlp 调试日志 |
| `GEMINI_PROMPT` | 可选，Gemini 系统提示词 |

Discord 开发者后台需要开启 **Message Content Intent**；机器人要有语音权限。

## 运行

Docker：

```bash
docker build -t music-bot .
docker run -d --name my-music-bot --env-file .env \
  -v "$PWD/Music:/app/Music" \
  -v "$PWD/etc:/app/etc" \
  -v "$PWD/cookies.txt:/app/cookies.txt" \
  music-bot
```

`docker-compose.yml` 示例（仓库里没有，按需自建）：

```yaml
services:
  bot:
    build: .
    container_name: my-music-bot
    env_file: .env
    volumes:
      - ./Music:/app/Music
      - ./etc:/app/etc
      - ./cookies.txt:/app/cookies.txt
    restart: unless-stopped
```

本地：

```bash
pip install -r requirements.txt
python main.py
```

日志里出现 `JS runtime: deno -> ...` 说明 deno 认到了；找不到会打印警告。

## 目录

```
main.py         主程序：命令、队列、yt-dlp 抽取
easter_egg.py   彩蛋模块，与主程序只通过 handle() 交互
Music/          本地音频，运行时挂载进容器
etc/            彩蛋素材（图片）
cookies.txt     B 站/YouTube cookie，不进版本库
```

`Music/` 和 `etc/` 都是按**启动目录**的相对路径找的，所以要在仓库根目录运行；Docker 里对应 `/app`。素材缺失不会崩：彩蛋会退化成只发文案。

## 已知限制

- **YouTube**：从数据中心 IP（VPS）访问时，YouTube 会要求登录验证，各客户端轮换也过不去，属 IP 层面的限制。家用网络正常，B 站不受影响。
- **B 站**：没有大会员时 yt-dlp 会提示高码率格式缺失，自动降级到可用格式，首次播放会慢一些。
- 每次失败都会把 yt-dlp 的原始报错发到频道，方便排查；不希望刷屏可以调 `YTDLP_VERBOSE` 或按需改提示语。

## 开发备注

- `/help` 的文案由 `bot.tree.get_commands()` 生成，加了新命令不用再手写帮助
- `source_obj_compiler()` 返回 2 元组、`build_play_source()` 返回 3 元组，改返回值时记得同步所有解包点（解包数量对不上只会在运行时炸）
- 队列的出队只在 `audio_after()` 里做，`play_next()` 只"看"队首，不要在两处都 pop
