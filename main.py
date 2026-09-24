import discord
from discord.ext import commands
from discord import app_commands
import os
import shutil
import html
import yt_dlp
import asyncio
import copy
import datetime
import re
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional

from google import genai
from google.genai import types

import easter_egg

# ---------- 配置 ----------
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
FFMPEG_PATH = os.getenv('FFMPEG_PATH', 'ffmpeg')
COOKIE_FILE = os.getenv('COOKIES_FILE', 'cookies.txt')
DENO_PATH = os.getenv('DENO')
MUSIC_PATH = Path.cwd() / 'Music'
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_MODEL = 'gemini-3.1-flash-lite'
GEMINI_PROMPT = os.getenv('GEMINI_PROMPT', "You're a helpful assistant, a member of the group chat. Respond in at most 3 sentences.")
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

COMMAND_PREFIX = '$'
MUSIC_EXTENSIONS = ('mp3', 'mp4', 'wav', 'm4a', 'ogg')
HISTORY_LIMIT = 7
HISTORY_TEXT_LIMIT = 300
REPLY_TEXT_LIMIT = 500
REPLY_SUFFIX = "...\n[字数超限]"
TEXT_OMITTED = "...(此处省略一万字垃圾话)"

URL_SCHEMES = ('http://', 'https://')


def split_url(raw: str):
    """识别 http(s) 链接；不是链接时返回 None（供搜索用）。"""
    for scheme in URL_SCHEMES:
        index = raw.find(scheme)
        if index >= 0:
            candidate = raw[index:]
            try:
                parts = urllib.parse.urlsplit(candidate)
            except ValueError:
                continue
            if parts.netloc:
                return candidate, candidate
    return None, raw

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
    'does not exist', 'members-only', 'age'
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
    'cookiefile': COOKIE_FILE,
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

server_states = {}
LAST_YTDLP_ERROR = None

# ---------- Bot 初始化 ----------
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=COMMAND_PREFIX,
    intents=intents,
    help_command=None
)


# ---------- 前缀命令 ----------
@bot.command()
async def kickbot(ctx, user: discord.Member):
    # 删除指令消息
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        print("权限不足")
    except Exception as e:
        print(f"信息删除失败：{e}")
    # 移出语音频道
    try:
        await user.move_to(None)
    except discord.Forbidden:
        print("权限不足")
    except Exception as e:
        print(f"踢人失败：{e}")


# ---------- 控制台 ----------
async def console_input():
    """从标准输入发送消息，格式 channel_id:content。"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        user_input = await asyncio.to_thread(input, "Enter message (ID:Content):")
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
    print(f'Logged in as {bot.user}')
    print(f'yt-dlp {yt_dlp.version.__version__}')
    bot.loop.create_task(console_input())
    for guild in bot.guilds:
        if guild.me and guild.me.voice:
            try:
                await guild.change_voice_state(channel=None)
                print(f"Successfully cleared ghost voice connection in: {guild.name}")
            except Exception as e:
                print(f"Failed to clear ghost voice connection in {guild.name}: {e}")
    if not Path(COOKIE_FILE).exists():
        print(f"[警告] 找不到 cookie 文件：{Path(COOKIE_FILE).resolve()}")
    if not Path(easter_egg.IMAGE_PATH).is_file():
        print(f"[警告] 找不到彩蛋图片：{Path(easter_egg.IMAGE_PATH).resolve()}")
    if DENO_EXEC:
        print(f"JS runtime: deno -> {DENO_EXEC}")
    else:
        print("[警告] 找不到 deno，yt-dlp 无法执行 JS 挑战")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.event
async def on_guild_remove(guild):
    if guild.id in server_states:
        server_states.pop(guild.id)
        print(f"已离开服务器 {guild.name} ({guild.id})，成功释放内存。")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.guild is None:
        await message.channel.send("DM Unssupported.")
        return
    if await easter_egg.handle(message, get_state(message.guild.id), play_next):
        return
    if bot.user in message.mentions:
        user_question = re.sub(rf'<@!?{bot.user.id}>', '', message.content).strip()
        if user_question:
            await reply_with_gemini(message)
    await bot.process_commands(message)


# ---------- 播放状态 ----------
def get_state(guild_id):
    if guild_id not in server_states:
        server_states[guild_id] = {
            'queue': [],
            'was_stopped': False,
            'skipped': {'was_skipped': False, 'skipped_title': None},
            'current': None,
            'current_title': None,
            'is_loading': False,
            'echoText': False,
            'force_next': False,
        }
    return server_states[guild_id]


def record_ytdlp_error(err):
    """保存 yt-dlp 的原始报错，供播放失败提示使用。"""
    global LAST_YTDLP_ERROR
    LAST_YTDLP_ERROR = str(err)
    print(f"[yt-dlp] {err}")


async def audio_after(err, channel):
    """单曲播放结束后的收尾：出队、循环判定、续播下一首。"""
    state = get_state(channel.guild.id)
    current_item = state['current'] or {}
    is_silent = current_item.get('silent', False)

    if err:
        print(f"Audio error: {err}")
    if state['was_stopped']:
        state['was_stopped'] = False
        return
    was_skipped = state['skipped']['was_skipped']
    # force_next：彩蛋等外部插队，播完当前曲目后直接前进、不放回队列
    force_next = state['force_next'] or was_skipped
    state['force_next'] = False
    if state['echoText']:
        state['echoText'] = False
        await channel.send(easter_egg.TEXT_MESSAGE)
    elif was_skipped:
        state['skipped']['was_skipped'] = False
        if not is_silent:
            await channel.send(f"Skipped: **{state['skipped']['skipped_title']}**")
    elif not current_item.get('loop', False) and not is_silent:
        # 循环曲目不播报"播放完成"，与最初的逻辑一致
        await channel.send(f"Song finished playing: **{state['current_title']}**")

    if current_item in state['queue']:
        state['queue'].remove(current_item)
    if state['current'] is current_item:
        state['current'] = None
    if current_item.get('loop', False) and not force_next:
        # 循环：放回队首；被 /skip 或彩蛋插队时不再放回
        state['queue'].insert(0, current_item)
    bot.loop.create_task(play_next(channel, was_silent=is_silent))


async def play_next(channel, was_silent: bool = False):
    """播放队首曲目；出队由 audio_after 负责。"""
    state = get_state(channel.guild.id)
    voice_client = channel.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        state['is_loading'] = False
        state['queue'].clear()
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
    try:
        source = None
        title = "Unknown"
        url = None
        current_item = state['queue'][0]

        if current_item['type'] == 'url':
            url = current_item['data']
            source, title = await source_obj_compiler(url)
        elif current_item['type'] == 'file':
            url = current_item['data']
            title = Path(url).name
            source = discord.FFmpegOpusAudio(url, executable=FFMPEG_PATH, options='-vn')

        if source:
            state['current'] = current_item
            state['current_title'] = title
            if current_item.get('outputFirstTime'):
                if not current_item.get('silent', False):
                    await channel.send(f"Now playing: **{title}**")
                current_item['outputFirstTime'] = False
            voice_client.play(source, after=lambda e: bot.loop.create_task(audio_after(e, channel)))
        else:
            if not current_item.get('silent', False):
                reason = f"（{LAST_YTDLP_ERROR}）" if LAST_YTDLP_ERROR else ""
                await channel.send(f"Failed to play: {url}, skipping...{reason}")
            if state['queue']:
                state['queue'].pop(0)
            if state['current'] is current_item:
                state['current'] = None
            bot.loop.create_task(play_next(channel))
    except Exception as e:
        print(f"Error in play_next: {e}")
        await channel.send("An unexpected error occurred. Skipping... [from func play_next()]")
        if state['queue']:
            state['queue'].pop(0)
        state['current'] = None
        if channel.guild.voice_client and channel.guild.voice_client.is_connected():
            bot.loop.create_task(play_next(channel))
        else:
            state['queue'].clear()
    finally:
        state['is_loading'] = False


def build_play_source(info):
    """把 yt-dlp 抽取结果转成音频源，返回 (source, title, stream_url)。"""
    stream_url = info.get('url')
    title = info.get('title', 'Unknown')
    if not stream_url:
        return None, title, None
    headers = info.get('http_headers', {}) or info.get('https_headers', {})
    b_options = '-re -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
    if headers:
        header_str = "".join([f"{k}: {v}\r\n" for k, v in headers.items()])
        b_options += f' -headers "{header_str}"'
    source = discord.FFmpegOpusAudio(
        stream_url,
        before_options=b_options,
        options='-vn ',
        executable=FFMPEG_PATH
    )
    return source, title, stream_url


# ---------- yt-dlp 抽取 ----------
def is_youtube_url(url: str) -> bool:
    return 'youtube.com' in url or 'youtu.be' in url


def is_retryable_ytdlp_error(err_text: str) -> bool:
    """客户端相关的错误可换客户端重试；视频本身的错误不可。"""
    if not err_text:
        return False
    low = err_text.lower()
    if any(sign in low for sign in RETRYABLE_ERROR_SIGNS):
        return True
    return not any(sign in low for sign in PERMANENT_ERROR_SIGNS)


def extract_with_client(video_url, client=None):
    """按指定客户端抽取一次，失败返回 None 并记录报错。"""
    options = copy.deepcopy(YDL_OPTIONS)
    options['extract_flat'] = False
    if client:
        options.setdefault('extractor_args', {}).setdefault('youtube', {})['player_client'] = [client]
        print(f"[yt-dlp] trying youtube player_client={client}")
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=False)
        if info and not info.get('entries'):
            return info
        record_ytdlp_error("extractor returned no playable entry")
    except Exception as e:
        record_ytdlp_error(e)
    return None


def extract_with_clients(video_url):
    """非 YouTube、或错误不可重试时只抽一次，否则逐个尝试 YOUTUBE_CLIENTS。"""
    if not is_youtube_url(video_url) or not is_retryable_ytdlp_error(LAST_YTDLP_ERROR):
        return extract_with_client(video_url)
    for client in YOUTUBE_CLIENTS:
        info = extract_with_client(video_url, client)
        if info:
            return info
    return None


async def source_obj_compiler(url: str):
    """把 URL 编译成音频源，返回 (source, title)；搜索页会逐条尝试。"""
    global LAST_YTDLP_ERROR
    LAST_YTDLP_ERROR = None
    try:
        try:
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)
        except Exception as e:
            record_ytdlp_error(e)
            info = None

        if info and info.get('entries'):
            for entry in info.get('entries') or []:
                if not entry:
                    continue
                ie_key = entry.get('ie_key', '')
                entry_url = entry.get('url', '') or ''
                if ie_key in ('YoutubeChannel', 'YoutubeTab', 'YoutubePlaylist'):
                    continue
                if '/channel/' in entry_url or '/@' in entry_url or '/playlist' in entry_url:
                    continue
                video_url = entry_url or f"https://www.youtube.com/watch?v={entry['id']}"
                entry_info = await asyncio.to_thread(extract_with_clients, video_url)
                if not entry_info:
                    print(f"Search result failed ({LAST_YTDLP_ERROR}). Trying next result...")
                    continue
                source, title, _ = build_play_source(entry_info)
                if source:
                    return source, title
                print(f"Result '{title}' missing stream URL. Trying next result...")
            print("Error: None of the search results could be played.")
            return None, None

        if not info:
            info = await asyncio.to_thread(extract_with_clients, url)
        if info:
            source, title, stream_url = build_play_source(info)
            if source:
                return source, title
            print(f"Result '{title}' missing stream URL.")
        return None, None
    except Exception as e:
        record_ytdlp_error(e)
        return None, None


def enqueue(channel, item):
    """入队新曲目；空闲时立即起播，返回是否已在播放。"""
    state = get_state(channel.guild.id)
    state['was_stopped'] = False
    state['queue'].append(item)
    voice_client = channel.guild.voice_client
    if voice_client and voice_client.is_playing():
        return True
    bot.loop.create_task(play_next(channel))
    return False


async def reply_with_gemini(message):
    """以频道近几条消息为上下文调用 Gemini，并回复结果。"""
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

            reply_text = response.text
            reply_text = re.sub(rf"^\**\[?{re.escape(bot_name)}\]?\**:\s*", "", reply_text).strip()
            if len(reply_text) > REPLY_TEXT_LIMIT:
                reply_text = reply_text[:REPLY_TEXT_LIMIT] + REPLY_SUFFIX
            await message.channel.send(reply_text)
        except Exception as e:
            print(f"API报错：{e}")
            if "503" in str(e):
                await message.channel.send("Busy（api busy 503）")
            elif "429" in str(e):
                await message.channel.send("Too frequent!（api rate limit 429）")
            else:
                await message.channel.send("Bad internet connection!（api Error）")


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
        if name.lower().endswith(MUSIC_EXTENSIONS)
    ] if MUSIC_PATH.exists() else []
    if filenames:
        await interaction.response.send_message("Available files: " + ', '.join(filenames))
    else:
        await interaction.response.send_message("No music files found in Music folder.")


@bot.tree.command(name="join", description="让机器人加入你的语音频道")
@app_commands.guild_only()
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("You are not in a voice channel.")
        return
    channel = interaction.user.voice.channel
    try:
        await channel.connect(self_deaf=True)
        await interaction.response.send_message(f"Joined {channel.name}.")
    except Exception as e:
        await interaction.response.send_message(f"Error: {e}")


@bot.tree.command(name="stop", description="停止当前播放的音乐")
@app_commands.guild_only()
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    state = get_state(interaction.guild.id)
    was_busy = state['is_loading']
    state['queue'].clear()
    state['current'] = None
    state['is_loading'] = False
    state['echoText'] = False
    if voice_client and (voice_client.is_playing() or was_busy):
        state['was_stopped'] = True
        voice_client.stop()
        await interaction.response.send_message("Song terminated.")
    else:
        await interaction.response.send_message("Not playing anything.")


@bot.tree.command(name="skip", description="跳过当前歌曲")
@app_commands.guild_only()
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    state = get_state(interaction.guild.id)
    if voice_client and voice_client.is_playing():
        state['skipped']['was_skipped'] = True
        state['skipped']['skipped_title'] = state['current_title']
        voice_client.stop()
        await interaction.response.send_message("Skipped current song.")
    else:
        await interaction.response.send_message("Not playing anything.")


@bot.tree.command(name="disconnect", description="离开语音频道")
@app_commands.guild_only()
async def disconnect(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        server_states.pop(interaction.guild.id, None)
        await interaction.response.send_message("Disconnected.", ephemeral=True)
    else:
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
        await interaction.channel.send(f"<@{interaction.user.id}> 服务器有点卡顿，但我正在处理你的请求...")
        return
    except Exception as e:
        print(f"Failed to defer interaction: {e}")
        return

    state = get_state(interaction.guild.id)
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        if not interaction.user.voice:
            await interaction.followup.send("You need to be in a voice channel first.")
            return
        try:
            voice_client = await interaction.user.voice.channel.connect(self_deaf=True)
            state['current'] = None
            state['is_loading'] = False
        except Exception as e:
            await interaction.followup.send(f"Cannot join voice: {e}")
            return

    if file:
        file_path = Path(MUSIC_PATH) / file
        if not file_path.resolve().is_relative_to(MUSIC_PATH.resolve()):
            await interaction.followup.send("Illegal directory access.")
            return
        if not file_path.exists():
            await interaction.followup.send("File not found.")
            return
        enqueue(interaction.channel, {
            'type': 'file',
            'data': str(file_path),
            'loop': loop,
            'title': file_path.name,
            'outputFirstTime': True
        })
        await interaction.followup.send(f"Added local file to queue: {file_path.name}")
        return

    if not url:
        await interaction.followup.send("Please provide a URL or use `file` option.")
        return

    play_url, query_name = split_url(url)
    if play_url is None:
        encoded = urllib.parse.quote(query_name)
        play_url = f"https://www.youtube.com/results?search_query={encoded}&sp=EgIQAQ%253D%253D"

    playing = enqueue(interaction.channel, {
        'type': 'url',
        'data': play_url,
        'loop': loop,
        'outputFirstTime': True
    })
    if playing:
        await interaction.followup.send(f"Added to queue: {query_name}", suppress_embeds=True)
    else:
        await interaction.followup.send(f"Preparing to play: {query_name}", suppress_embeds=True)


# ---------- 启动 ----------
if __name__ == "__main__":
    bot.run(TOKEN)
