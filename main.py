import asyncio
import atexit
import copy
import datetime
import html
import os
import re
import shlex
import shutil
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Optional

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from google import genai
from google.genai import types

import easter_egg

# ---------- 配置 ----------
load_dotenv()
TOKEN = (os.getenv('DISCORD_TOKEN') or '').strip() or None
FFMPEG_PATH = os.getenv('FFMPEG_PATH', 'ffmpeg')
COOKIE_FILE = os.getenv('COOKIES_FILE', 'cookies.txt')
DENO_PATH = os.getenv('DENO')
MUSIC_PATH = Path.cwd() / 'Music'
GEMINI_API_KEY = (os.getenv('GEMINI_API_KEY') or '').strip() or None
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.1-flash-lite')
GEMINI_PROMPT = os.getenv('GEMINI_PROMPT', "You're a helpful assistant, a member of the group chat. Respond in at most 3 sentences.")

# 聊天功能可选：没有 key 时不在 import 期初始化 Gemini 客户端，
# 否则纯音乐部署会在启动时直接抛 ValueError。
gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as exc:
        print(f"[警告] Gemini 客户端初始化失败，聊天功能将不可用：{exc}")
else:
    print("[警告] 未配置 GEMINI_API_KEY，聊天功能将不可用（音乐功能不受影响）")

COMMAND_PREFIX = '$'
MUSIC_EXTENSIONS = ('mp3', 'mp4', 'wav', 'm4a', 'ogg')
HISTORY_LIMIT = 7
HISTORY_TEXT_LIMIT = 300
REPLY_TEXT_LIMIT = 500
REPLY_SUFFIX = "...\n[字数超限]"
TEXT_OMITTED = "...(此处省略一万字垃圾话)"
MAX_MESSAGE_LEN = 1900  # Discord 上限 2000，留点余量

URL_PATTERN = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", re.IGNORECASE)
SEARCH_PREFIX_PATTERN = re.compile(r'^ytsearch\w*:', re.IGNORECASE)


def clip_text(text, limit: int = MAX_MESSAGE_LEN) -> str:
    """把消息裁剪到 Discord 2000 字限制之内，避免 HTTPException。"""
    text = str(text or '')
    if len(text) <= limit:
        return text
    suffix = "\n...(内容过长已截断)"
    return text[:max(0, limit - len(suffix))] + suffix


def split_url(raw: str):
    """识别 http(s) 链接；不是链接时返回 None（供搜索用）。

    只截取 URL 本身：分享文案的前后缀（如 "【标题】 https://b23.tv/x 快来围观"）不会带进 yt-dlp。
    """
    match = URL_PATTERN.search(raw or '')
    if not match:
        return None, raw
    candidate = match.group(0).rstrip(".,;:!?)]}'")
    try:
        parts = urllib.parse.urlsplit(candidate)
    except ValueError:
        return None, raw
    if parts.netloc:
        return candidate, candidate
    return None, raw


def is_search_request(text: str) -> bool:
    return bool(SEARCH_PREFIX_PATTERN.match((text or '').strip()))


# YouTube 客户端尝试顺序，用 .env 的 YOUTUBE_CLIENTS 覆盖；空表示不轮换
YOUTUBE_CLIENTS = [
    client.strip() for client in os.getenv('YOUTUBE_CLIENTS', 'android_vr,mweb').split(',') if client.strip()
]
YTDLP_VERBOSE = os.getenv('YTDLP_VERBOSE', '').lower() in ('1', 'true', 'yes')

# is_retryable_ytdlp_error 的判定词表
RETRYABLE_ERROR_SIGNS = (
    'not a bot', 'sign in', 'login_required', 'drm',
    'no video formats', 'requested format', '403', 'unable to extract'
)
PERMANENT_ERROR_SIGNS = (
    'video unavailable', 'private video', 'removed',
    'does not exist', 'members-only',
    # 注意：不要用裸 'age'，它会误命中 'webpage' / 'message' / 'language' 等瞬时错误
    'confirm your age', 'age-restricted', 'inappropriate for some users',
)


def resolve_deno():
    """返回传给 yt-dlp 的 deno 路径；.env 的 DENO 失效时回退到 PATH。"""
    configured = (DENO_PATH or '').strip().strip('"').strip("'")
    if not configured:
        return shutil.which('deno')
    found = shutil.which(configured)
    if found:
        return found
    if Path(configured).is_file():
        return configured
    fallback = shutil.which('deno')
    print(f"[警告] .env 的 DENO={configured!r} 不存在，改用 {fallback!r}")
    return fallback


DENO_EXEC = resolve_deno()


def resolve_cookiefile(path):
    """返回可安全交给 yt-dlp 的 cookies 路径。

    yt-dlp 在 YoutubeDL.close() 时会回写 cookies；Docker 以 :ro 挂载时回写会触发
    [Errno 30] Read-only file system，导致每次抽取都被当成失败。只读时复制到 0600 临时文件。
    """
    cookie_path = Path(path)
    if not cookie_path.is_file():
        return None
    if os.access(cookie_path, os.W_OK):
        return str(cookie_path)
    try:
        fd, temp_name = tempfile.mkstemp(prefix='discord-music-bot-cookies-', suffix='.txt')
        os.close(fd)
        temp_path = Path(temp_name)
        shutil.copyfile(cookie_path, temp_path)
        atexit.register(lambda p=temp_path: p.unlink(missing_ok=True))
        print("[信息] cookies 文件只读，已复制到临时目录供 yt-dlp 使用（刷新不会写回宿主机）。")
        return str(temp_path)
    except OSError as exc:
        print(f"[警告] cookies 文件只读且无法复制到临时目录（{exc}），将不加载 cookies。")
        return None


YTDLP_COOKIEFILE = resolve_cookiefile(COOKIE_FILE)

YDL_OPTIONS = {
    'playlist_items': '1:5',
    'extract_flat': 'in_playlist',
    'format': (
        'bestaudio[extractor_key=Bilibili] / worst[extractor_key=Bilibili] / '
        'bestaudio[protocol^=http][protocol!*=dash][protocol!*=m3u8] / '
        'best[protocol^=http][protocol!*=dash][protocol!*=m3u8]'
    ),
    'noplaylist': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'js_runtimes': {'deno': {'path': DENO_EXEC}},
    'remote_components': ['ejs:github'],
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    },
    'socket_timeout': 20,
    'retries': 2,
    'extractor_retries': 1,
    'verbose': YTDLP_VERBOSE,
}

# 只在 cookies.txt 是普通文件时才传 cookiefile：
# - 文件缺失时 yt-dlp 会在 close() 里把空 cookie 写回文件（并发下互相覆盖）
# - Docker 里 cookies.txt 不存在时会被挂载成同名空目录，传进去会让每次 /play 都失败
if YTDLP_COOKIEFILE:
    YDL_OPTIONS['cookiefile'] = YTDLP_COOKIEFILE

server_states = {}
_background_tasks = set()

# ---------- Bot 初始化 ----------
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True


class MusicBot(commands.Bot):
    async def setup_hook(self):
        """一次性初始化：控制台输入只启动一次、斜杠命令只同步一次。"""
        if sys.stdin.isatty():
            task = asyncio.create_task(console_input())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
        else:
            print("[信息] 非交互式 stdin，控制台输入功能已禁用。")
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} slash command(s)")
        except Exception as e:
            print(f"Failed to sync commands: {e}")


bot = MusicBot(
    command_prefix=COMMAND_PREFIX,
    intents=intents,
    help_command=None
)
_first_ready = False


# ---------- 前缀命令 ----------
@bot.command()
@commands.guild_only()
@commands.has_guild_permissions(move_members=True)
async def kickbot(ctx, user: discord.Member):
    print(f"[kickbot] 审计日志：{ctx.author} ({ctx.author.id}) 将 {user} ({user.id}) 移出语音频道，guild={ctx.guild.id}")
    if user.id == ctx.author.id:
        await ctx.reply("不能把自己移出语音频道。", mention_author=False)
        return
    # 移出语音频道
    try:
        await user.move_to(None)
    except discord.Forbidden:
        await ctx.reply("权限不足：机器人需要”移动成员”权限，且不能移动权限更高的成员。", mention_author=False)
        return
    except Exception as e:
        print(f"踢人失败：{e}")
        return
    # 删除指令消息（需要管理消息权限，失败不影响踢人结果）
    try:
        if ctx.guild.me and ctx.guild.me.guild_permissions.manage_messages:
            await ctx.message.delete()
    except Exception as e:
        print(f"信息删除失败：{e}")


# ---------- 控制台 ----------
async def console_input():
    """从标准输入发送消息，格式 channel_id:content。"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            user_input = await asyncio.to_thread(input, "Enter message (ID:Content):")
        except (EOFError, KeyboardInterrupt):
            print("[信息] 控制台输入已关闭。")
            return
        try:
            if ":" in user_input:
                channel_id, msg_content = user_input.split(":", 1)
                channel = bot.get_channel(int(channel_id.strip()))
                if channel:
                    await channel.send(msg_content.strip())
                else:
                    print("Channel not found")
        except Exception as e:
            print(f"Error: {e}")


# ---------- 事件 ----------
@bot.event
async def on_ready():
    global _first_ready
    print(f'Logged in as {bot.user}')
    print(f'yt-dlp {yt_dlp.version.__version__}')
    # 一次性工作：只在第一次 ready 执行，重连时不能再跑（否则会重置语音状态、重复同步命令）
    if _first_ready:
        return
    _first_ready = True
    for guild in bot.guilds:
        if guild.me and guild.me.voice:
            try:
                await guild.change_voice_state(channel=None)
                print(f"Successfully cleared ghost voice connection in: {guild.name}")
            except Exception as e:
                print(f"Failed to clear ghost voice connection in {guild.name}: {e}")
    if not Path(COOKIE_FILE).is_file():
        print(f"[警告] 找不到 cookie 文件（或它是个目录）：{Path(COOKIE_FILE).resolve()}")
    if not Path(easter_egg.IMAGE_PATH).is_file():
        print(f"[警告] 找不到彩蛋图片：{Path(easter_egg.IMAGE_PATH).resolve()}")
    if DENO_EXEC:
        print(f"JS runtime: deno -> {DENO_EXEC}")
    else:
        print("[警告] 找不到 deno，yt-dlp 无法执行 JS 挑战")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("权限不足：机器人缺少执行该命令所需的权限。", mention_author=False)
    elif isinstance(error, commands.MemberNotFound):
        await ctx.reply("找不到该成员，请检查用户名/ID。", mention_author=False)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.reply("该命令只能在服务器内使用。", mention_author=False)
    else:
        print(f"[命令错误] {ctx.command}: {error!r}")


@bot.event
async def on_guild_remove(guild):
    state = server_states.pop(guild.id, None)
    if state:
        state['closing'] = True
        state['load_gen'] += 1
        print(f"已离开服务器 {guild.name} ({guild.id})，成功释放内存。")


@bot.event
async def on_voice_state_update(member, before, after):
    """机器人被管理员踢出/移动时的兜底清理，避免残留状态导致复活消息。"""
    if bot.user is None or member.id != bot.user.id:
        return
    if after.channel is not None:
        return
    voice_client = member.guild.voice_client
    if voice_client is not None and voice_client.is_connected():
        return
    state = server_states.pop(member.guild.id, None)
    if state:
        state['closing'] = True
        state['load_gen'] += 1


@bot.event
async def on_message(message):
    if message.author == bot.user or message.author.bot:
        return
    if message.guild is None:
        await message.channel.send("DM Unsupported.", allowed_mentions=discord.AllowedMentions.none())
        return
    # 只有命中彩蛋关键词时才取播放状态，避免每条消息都创建 state
    if message.content == easter_egg.TRIGGER:
        if await easter_egg.handle(message, get_state(message.guild.id), play_next):
            return
    if bot.user in message.mentions:
        user_question = re.sub(rf'<@!?{bot.user.id}>', '', message.content).strip()
        if user_question:
            await reply_with_gemini(message)
    await bot.process_commands(message)


# ---------- 播放状态 ----------
def get_state(guild_id, create: bool = True):
    state = server_states.get(guild_id)
    if state is None and create:
        state = {
            'queue': [],
            'skipped': {'was_skipped': False, 'skipped_title': None},
            'interrupted': False,
            'current': None,
            'current_title': None,
            'is_loading': False,
            'load_gen': 0,
            'closing': False,
        }
        server_states[guild_id] = state
    return state


def _log_callback_error(future):
    if future.cancelled():
        return
    try:
        future.result()
    except Exception as e:
        print(f"[错误] 播放回调任务失败：{e!r}")


def _schedule(coro):
    """线程安全地把协程投递到事件循环。

    discord.py 的 play(after=...) 回调运行在 AudioPlayer 线程，不能直接
    bot.loop.create_task()；run_coroutine_threadsafe 会唤醒 selector 并保证任务被执行。
    """
    try:
        future = asyncio.run_coroutine_threadsafe(coro, bot.loop)
    except RuntimeError:
        coro.close()
        return None
    future.add_done_callback(_log_callback_error)
    return future


async def audio_after(err, channel):
    """单曲播放结束后的收尾：出队、循环判定、续播下一首。"""
    guild_id = channel.guild.id
    state = get_state(guild_id, create=False)
    if state is None or state.get('closing'):
        return
    voice_client = channel.guild.voice_client
    if voice_client is None or not voice_client.is_connected():
        # 播放期间连接已丢失：静默清理，避免发出 "Song finished: **None**" 这类误导消息
        state['closing'] = True
        state['load_gen'] += 1
        server_states.pop(guild_id, None)
        return
    current_item = state['current']
    if current_item is None:
        # /stop 已经清理过播放状态，这次回调不需要再出队/播报
        return
    is_silent = current_item.get('silent', False)

    if err:
        print(f"Audio error: {err}")
    was_skipped = state['skipped']['was_skipped']
    skipped_title = state['skipped']['skipped_title']
    state['skipped']['was_skipped'] = False
    state['skipped']['skipped_title'] = None
    interrupted = state.get('interrupted', False)
    state['interrupted'] = False

    if was_skipped:
        if not is_silent:
            await channel.send(clip_text(f"Skipped: **{skipped_title}**"))
    elif not interrupted and not current_item.get('loop', False) and not is_silent:
        # 循环曲目不播报"播放完成"；被彩蛋打断的曲目也不播报
        await channel.send(clip_text(f"Song finished playing: **{state['current_title']}**"))

    # 出队按对象身份匹配：同一首歌可能重复入队，dict 值相等会删错
    for index, item in enumerate(state['queue']):
        if item is current_item:
            state['queue'].pop(index)
            break
    if state['current'] is current_item:
        state['current'] = None
        state['current_title'] = None
    # 循环：放回队首；被 /skip 或彩蛋打断时不再放回
    if current_item.get('loop', False) and not interrupted and not was_skipped:
        state['queue'].insert(0, current_item)
    bot.loop.create_task(play_next(channel, was_silent=is_silent))


async def play_next(channel, was_silent: bool = False):
    """播放队首曲目；出队由 audio_after 负责。"""
    state = get_state(channel.guild.id, create=False)
    if state is None or state.get('closing'):
        return
    voice_client = channel.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        state['queue'].clear()
        state['is_loading'] = False
        server_states.pop(channel.guild.id, None)
        if not was_silent:
            await channel.send("Bot is disconnected from the voice channel.")
        return

    if state['is_loading']:
        return
    if not state['queue']:
        if not was_silent:
            await channel.send("Queue finished.")
        return

    state['is_loading'] = True
    load_gen = state['load_gen']
    try:
        source = None
        title = "Unknown"
        url = None
        load_error = None
        current_item = state['queue'][0]

        if current_item['type'] == 'url':
            url = current_item['data']
            source, title, load_error = await source_obj_compiler(url)
            if load_gen != state['load_gen'] or state.get('closing'):
                # /stop 或 /disconnect 已让这次加载作废：丢弃并回收 FFmpeg 子进程
                if source is not None:
                    source.cleanup()
                return
        elif current_item['type'] == 'file':
            url = current_item['data']
            title = Path(url).name
            if Path(url).is_file():
                source = discord.FFmpegOpusAudio(url, executable=FFMPEG_PATH, options='-vn')
            else:
                load_error = f"本地文件不存在：{url}"

        if source:
            state['current'] = current_item
            state['current_title'] = title
            if current_item.get('outputFirstTime'):
                if not current_item.get('silent', False):
                    await channel.send(clip_text(f"Now playing: **{title}**"))
                current_item['outputFirstTime'] = False
            # 加载期间可能已断开，播放前重新确认连接
            current_client = channel.guild.voice_client
            if not current_client or not current_client.is_connected():
                source.cleanup()
                state['queue'].clear()
                state['is_loading'] = False
                server_states.pop(channel.guild.id, None)
                if not was_silent:
                    await channel.send("Bot is disconnected from the voice channel.")
                return
            current_client.play(source, after=lambda e: _schedule(audio_after(e, channel)))
        else:
            if not current_item.get('silent', False):
                reason = f"（{clip_text(load_error, 500)}）" if load_error else ""
                await channel.send(clip_text(f"Failed to play: {url}, skipping...{reason}"))
            for index, item in enumerate(state['queue']):
                if item is current_item:
                    state['queue'].pop(index)
                    break
            if state['current'] is current_item:
                state['current'] = None
                state['current_title'] = None
            bot.loop.create_task(play_next(channel))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"Error in play_next: {e}")
        try:
            await channel.send("An unexpected error occurred. Skipping... [from func play_next()]")
        except Exception:
            pass
        if state['queue']:
            state['queue'].pop(0)
        state['current'] = None
        state['current_title'] = None
        if channel.guild.voice_client and channel.guild.voice_client.is_connected():
            bot.loop.create_task(play_next(channel))
        else:
            state['queue'].clear()
    finally:
        # 只有仍然属于当前这次加载时才复位，避免 /stop 之后的旧任务踩掉新任务的状态
        if load_gen == state['load_gen']:
            state['is_loading'] = False


RECONNECT_OPTIONS = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'


def build_audio_options(headers) -> str:
    """构造 FFmpeg before_options。

    - headers 通过 shlex.quote 包裹：discord.py 用 shlex.split 解析 before_options，
      裸双引号会抛 ValueError 或注入任意 FFmpeg 参数。
    - 不再加 -re：AudioPlayer 已按 20ms/帧节流，-re 只会牺牲 CDN 抖动时的缓冲余量。
    """
    options = RECONNECT_OPTIONS
    if headers:
        header_str = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
        options += ' -headers ' + shlex.quote(header_str)
    return options


def build_play_source(info):
    """把 yt-dlp 抽取结果转成音频源，返回 (source, title, stream_url)。"""
    stream_url = info.get('url')
    title = info.get('title', 'Unknown')
    if not stream_url:
        return None, title, None
    headers = info.get('http_headers', {}) or info.get('https_headers', {})
    source = discord.FFmpegOpusAudio(
        stream_url,
        before_options=build_audio_options(headers),
        options='-vn',
        executable=FFMPEG_PATH
    )
    return source, title, stream_url


# ---------- yt-dlp 抽取 ----------
YOUTUBE_HOSTS = ('youtube.com', 'youtu.be', 'youtube-nocookie.com')


def is_youtube_url(url: str) -> bool:
    try:
        host = (urllib.parse.urlsplit(url).hostname or '').lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == domain or host.endswith('.' + domain) for domain in YOUTUBE_HOSTS)


def is_retryable_ytdlp_error(err_text) -> bool:
    """客户端相关的错误可换客户端重试；视频本身的错误不可。

    必须先判永久性错误：'Private video. Sign in ...'、'Sign in to confirm your age'
    这类文案同时包含可重试词表里的 'sign in'，先判可重试会误伤。
    """
    if not err_text:
        return False
    low = str(err_text).lower()
    if any(sign in low for sign in PERMANENT_ERROR_SIGNS):
        return False
    if any(sign in low for sign in RETRYABLE_ERROR_SIGNS):
        return True
    # 未知错误：换客户端重试一次的成本很低，保持旧的兜底行为
    return True


def extract_with_client(video_url, client=None):
    """按指定客户端抽取一次，返回 (info, error)。"""
    options = copy.deepcopy(YDL_OPTIONS)
    options['extract_flat'] = False
    if client:
        options.setdefault('extractor_args', {}).setdefault('youtube', {})['player_client'] = [client]
        print(f"[yt-dlp] trying youtube player_client={client}")
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=False)
        if info and not info.get('entries'):
            return info, None
        return None, "extractor returned no playable entry"
    except Exception as e:
        print(f"[yt-dlp] {e}")
        return None, str(e)


def extract_with_clients(video_url, initial_error=None):
    """默认客户端失败且错误可重试时，逐个尝试 YOUTUBE_CLIENTS。"""
    if initial_error is None:
        info, error = extract_with_client(video_url)
        if info:
            return info, None
    else:
        error = initial_error
    if not is_youtube_url(video_url) or not is_retryable_ytdlp_error(error):
        return None, error
    for client in YOUTUBE_CLIENTS:
        info, client_error = extract_with_client(video_url, client)
        if info:
            return info, None
        error = client_error or error
    return None, error


async def source_obj_compiler(url: str):
    """把 URL/搜索请求编译成音频源，返回 (source, title, error)。"""
    options = copy.deepcopy(YDL_OPTIONS)
    if is_search_request(url):
        # 搜索只取前 5 条扁平结果，再逐条完整抽取；不再手拼 /results?search_query=...&sp=...
        options['extract_flat'] = True
        options['playlist_items'] = '1:5'
    else:
        options['extract_flat'] = False

    info = None
    error = None
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
    except Exception as e:
        error = str(e)
        print(f"[yt-dlp] 抽取失败：{e}")

    if info and info.get('entries'):
        for entry in info.get('entries') or []:
            if not entry:
                continue
            # 非搜索场景（如 B 站多分 P 视频）在 extract_flat=False 下已经拿到完整流信息，
            # 此时 entry['url'] 是带防盗链的 CDN 直链，不能再送进 extract_with_clients 二次解析（会丢 Referer 报 403）
            if entry.get('_type') not in ('url', 'url_transparent') and entry.get('url'):
                source, title, _ = build_play_source(entry)
                if source:
                    return source, title, None
                print(f"Result '{title}' missing stream URL. Trying next result...")
                continue
            ie_key = entry.get('ie_key', '')
            entry_url = entry.get('url', '') or ''
            if ie_key in ('YoutubeChannel', 'YoutubeTab', 'YoutubePlaylist'):
                continue
            if '/channel/' in entry_url or '/@' in entry_url or '/playlist' in entry_url:
                continue
            video_url = entry_url or f"https://www.youtube.com/watch?v={entry.get('id', '')}"
            if not entry_url and not entry.get('id'):
                continue
            entry_info, entry_error = await asyncio.to_thread(extract_with_clients, video_url)
            if not entry_info:
                error = entry_error or error
                print(f"Search result failed ({entry_error}). Trying next result...")
                continue
            source, title, _ = build_play_source(entry_info)
            if source:
                return source, title, None
            print(f"Result '{title}' missing stream URL. Trying next result...")
        print("Error: None of the search results could be played.")
        return None, None, error or "None of the search results could be played"

    if not info:
        info, error = await asyncio.to_thread(extract_with_clients, url, error)
    if info:
        source, title, _ = build_play_source(info)
        if source:
            return source, title, None
        print(f"Result '{title}' missing stream URL.")
        return None, None, error or "Missing stream URL"
    return None, None, error or "Extraction failed"


def enqueue(channel, item) -> str:
    """入队新曲目，返回 'started' / 'queued'（正在加载也算 queued）。"""
    state = get_state(channel.guild.id)
    state['queue'].append(item)
    voice_client = channel.guild.voice_client
    if state['is_loading'] or (voice_client and voice_client.is_playing()):
        return 'queued'
    bot.loop.create_task(play_next(channel))
    return 'started'


async def reply_with_gemini(message):
    """以频道近几条消息为上下文调用 Gemini，并回复结果。"""
    no_mentions = discord.AllowedMentions.none()
    if gemini_client is None:
        await message.channel.send("（未配置 GEMINI_API_KEY，聊天功能不可用。）", allowed_mentions=no_mentions)
        return
    async with message.channel.typing():
        try:
            contents = []
            async for msg in message.channel.history(limit=HISTORY_LIMIT):
                if not msg.content:
                    continue
                if msg.author == bot.user:
                    role = 'model'
                    text = msg.clean_content
                else:
                    role = 'user'
                    text = f"The user {msg.author.display_name} said: {html.unescape(msg.clean_content)}"
                contents.append({"role": role, "parts": [{"text": text}]})
            contents.reverse()

            merged = []
            for part in contents:
                if merged and merged[-1]["role"] == part["role"]:
                    merged[-1]["parts"][0]["text"] += f"\n{part['parts'][0]['text']}"
                else:
                    merged.append(part)
            for part in merged:
                text = part["parts"][0]["text"]
                if len(text) > HISTORY_TEXT_LIMIT:
                    part["parts"][0]["text"] = text[:HISTORY_TEXT_LIMIT] + TEXT_OMITTED

            bot_name = bot.user.display_name
            response = await gemini_client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=merged,
                config=types.GenerateContentConfig(
                    safety_settings=[
                        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
                    ],
                    system_instruction=(
                        f"Your name is {bot_name}.\n" + GEMINI_PROMPT +
                        f" Current Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    )
                )
            )

            # 被安全策略拦截时 response.text 可能是 None，直接 re.sub 会 TypeError
            reply_text = (response.text or '').strip()
            reply_text = re.sub(rf"^\**\[?{re.escape(bot_name)}\]?\**:\s*", "", reply_text).strip()
            if not reply_text:
                await message.channel.send("（模型没有返回内容，可能触发了安全策略。）", allowed_mentions=no_mentions)
                return
            if len(reply_text) > REPLY_TEXT_LIMIT:
                reply_text = reply_text[:REPLY_TEXT_LIMIT] + REPLY_SUFFIX
            await message.channel.send(reply_text, allowed_mentions=no_mentions)
        except Exception as e:
            print(f"API报错：{e}")
            if "503" in str(e):
                await message.channel.send("Busy（api busy 503）", allowed_mentions=no_mentions)
            elif "429" in str(e):
                await message.channel.send("Too frequent!（api rate limit 429）", allowed_mentions=no_mentions)
            else:
                await message.channel.send("Bad internet connection!（api Error）", allowed_mentions=no_mentions)


# ---------- 斜杠命令 ----------
HELP_USAGE = {
    'help': '/help - 显示此帮助',
    'dir': '/dir - 列出可播放的本地文件',
    'join': '/join - 让机器人加入语音频道',
    'disconnect': '/disconnect - 让机器人退出语音频道',
    'play': '/play [url] [loop] [file] - 播放音乐\n'
            '    - url: 视频链接或搜索关键词\n'
            '    - loop: 是否循环\n'
            '    - file: 可选，本地文件名',
    'skip': '/skip - 跳过当前歌曲',
    'stop': '/stop - 停止播放',
    'hello': '/hello - 打个招呼',
    'like': '/like - 点个赞',
    'dislike': '/dislike - 踩一下',
}


def build_help_text():
    """按已注册的斜杠命令生成帮助文本。"""
    return "\n".join(
        HELP_USAGE.get(command.name, f"/{command.name} - {command.description}")
        for command in bot.tree.get_commands()
    )


@bot.tree.command(name="help", description="显示所有可用命令")
@app_commands.guild_only()
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(build_help_text())


@bot.tree.command(name="hello", description="打个招呼")
@app_commands.guild_only()
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hello {interaction.user.name}.")


@bot.tree.command(name="like", description="点个赞")
@app_commands.guild_only()
async def like(interaction: discord.Interaction):
    await interaction.response.send_message("Thanks ❤️")


@bot.tree.command(name="dislike", description="踩一下")
@app_commands.guild_only()
async def dislike(interaction: discord.Interaction):
    await interaction.response.send_message("Sorry")


@bot.tree.command(name="dir", description="列出可播放的本地文件")
@app_commands.guild_only()
async def dir_list(interaction: discord.Interaction):
    filenames = [
        name for name in os.listdir(MUSIC_PATH)
        if name.lower().endswith(MUSIC_EXTENSIONS) and (MUSIC_PATH / name).is_file()
    ] if MUSIC_PATH.is_dir() else []
    if filenames:
        await interaction.response.send_message(clip_text("Available files: " + ', '.join(filenames)))
    else:
        await interaction.response.send_message("No music files found in Music folder.")


@bot.tree.command(name="join", description="让机器人加入你的语音频道")
@app_commands.guild_only()
async def join(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("You are not in a voice channel.")
        return
    channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client
    # 清理残留的失效连接，否则 channel.connect() 会抛 Already connected
    if voice_client and not voice_client.is_connected():
        try:
            await voice_client.disconnect(force=True)
        except Exception as e:
            print(f"清理失效语音连接失败：{e}")
        voice_client = None
    try:
        if voice_client:
            if voice_client.channel == channel:
                await interaction.response.send_message(f"Already connected to {channel.name}.")
                return
            await voice_client.move_to(channel)
            await interaction.response.send_message(f"Moved to {channel.name}.")
            return
        await channel.connect(self_deaf=True)
        await interaction.response.send_message(f"Joined {channel.name}.")
    except Exception as e:
        await interaction.response.send_message(f"Error: {e}")


@bot.tree.command(name="stop", description="停止当前播放的音乐")
@app_commands.guild_only()
async def stop(interaction: discord.Interaction):
    guild = interaction.guild
    state = get_state(guild.id, create=False)
    if state is None:
        await interaction.response.send_message("Not playing anything.")
        return
    voice_client = guild.voice_client
    was_playing = bool(voice_client and voice_client.is_playing())
    was_loading = state['is_loading']
    # 让在途加载作废：play_next 返回后校验 generation，丢弃已创建的 source
    state['load_gen'] += 1
    state['is_loading'] = False
    state['queue'].clear()
    state['current'] = None
    state['current_title'] = None
    state['interrupted'] = False
    state['skipped']['was_skipped'] = False
    state['skipped']['skipped_title'] = None
    if was_playing:
        voice_client.stop()
    if was_playing or was_loading:
        await interaction.response.send_message("Song terminated.")
    else:
        await interaction.response.send_message("Not playing anything.")


@bot.tree.command(name="skip", description="跳过当前歌曲")
@app_commands.guild_only()
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    state = get_state(interaction.guild.id, create=False)
    if voice_client and voice_client.is_playing() and state is not None:
        state['skipped']['was_skipped'] = True
        state['skipped']['skipped_title'] = state['current_title']
        voice_client.stop()
        await interaction.response.send_message("Skipped current song.")
    else:
        await interaction.response.send_message("Not playing anything.")


@bot.tree.command(name="disconnect", description="离开语音频道")
@app_commands.guild_only()
async def disconnect(interaction: discord.Interaction):
    guild = interaction.guild
    state = get_state(guild.id, create=False)
    if state is not None:
        # 先让状态失效，再断开；否则 audio_after 会重建 state 并发 "Song finished: **None**"
        state['closing'] = True
        state['load_gen'] += 1
        state['queue'].clear()
        state['current'] = None
        state['current_title'] = None
    voice_client = guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        server_states.pop(guild.id, None)
        await interaction.response.send_message("Disconnected.", ephemeral=True)
    else:
        server_states.pop(guild.id, None)
        await interaction.response.send_message("Not in a voice channel.", ephemeral=True)


@bot.tree.command(name="play", description="播放音乐（支持 URL、关键词、本地文件）")
@app_commands.guild_only()
async def play(
    interaction: discord.Interaction,
    url: Optional[str] = None,
    loop: bool = False,
    file: Optional[str] = None
):
    url = url or None
    file = file or None
    try:
        await interaction.response.defer(thinking=True)
    except discord.errors.NotFound:
        await interaction.channel.send(f"<@{interaction.user.id}> 交互已过期，请重新执行命令。")
        return
    except Exception as e:
        print(f"Failed to defer interaction: {e}")
        return

    voice_client = interaction.guild.voice_client
    # 清理残留的失效连接，否则 connect() 会抛 Already connected
    if voice_client and not voice_client.is_connected():
        try:
            await voice_client.disconnect(force=True)
        except Exception as e:
            print(f"清理失效语音连接失败：{e}")
        voice_client = None

    if not voice_client or not voice_client.is_connected():
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("You need to be in a voice channel first.")
            return
        try:
            voice_client = await interaction.user.voice.channel.connect(self_deaf=True)
            state = get_state(interaction.guild.id)
            state['current'] = None
            state['is_loading'] = False
        except Exception as e:
            await interaction.followup.send(f"Cannot join voice: {e}")
            return

    if file:
        music_root = MUSIC_PATH.resolve()
        file_path = Path(MUSIC_PATH) / file
        try:
            resolved = file_path.resolve()
        except OSError:
            resolved = file_path
        if not resolved.is_relative_to(music_root):
            await interaction.followup.send("Illegal directory access.")
            return
        if not resolved.is_file():
            await interaction.followup.send("File not found.")
            return
        if resolved.suffix.lower().lstrip('.') not in MUSIC_EXTENSIONS:
            await interaction.followup.send(f"Unsupported file type. Allowed: {', '.join(MUSIC_EXTENSIONS)}")
            return
        result = enqueue(interaction.channel, {
            'type': 'file',
            'data': str(resolved),
            'loop': loop,
            'outputFirstTime': True
        })
        if result == 'queued':
            await interaction.followup.send(f"Added local file to queue: {resolved.name}")
        else:
            await interaction.followup.send(f"Preparing to play: {resolved.name}")
        return

    if not url:
        await interaction.followup.send("Please provide a URL or use the file option.")
        return

    play_url, query_name = split_url(url)
    if play_url is None:
        query_name = (query_name or '').strip()
        if not query_name:
            await interaction.followup.send("Please provide a URL, search keyword or use the file option.")
            return
        # 直接交给 yt-dlp 的 ytsearch：不再手拼 /results?search_query=...&sp=%253D...
        play_url = query_name if is_search_request(query_name) else f"ytsearch5:{query_name}"

    result = enqueue(interaction.channel, {
        'type': 'url',
        'data': play_url,
        'loop': loop,
        'outputFirstTime': True
    })
    if result == 'queued':
        await interaction.followup.send(f"Added to queue: {query_name}", suppress_embeds=True)
    else:
        await interaction.followup.send(f"Preparing to play: {query_name}", suppress_embeds=True)


# ---------- 启动 ----------
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("缺少 DISCORD_TOKEN：请在 .env 中配置后重新启动。")
    bot.run(TOKEN)
