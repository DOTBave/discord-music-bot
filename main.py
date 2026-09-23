import discord
from discord.ext import commands
from discord import app_commands
import os
import yt_dlp
print(yt_dlp.version.__version__)
import asyncio
import re
import urllib.parse
#from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional

# Gemini import
import datetime
from google import genai
from google.genai import types

# --配置--
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
FFMPEG_PATH = os.getenv('FFMPEG_PATH', 'ffmpeg')
# SERVER_WHITELIST = os.getenv('SERVER_WHITELIST', '').split(',')
MUSIC_PATH = Path.cwd() / 'Music'
GEMINI_PROMPT = os.getenv('GEMINI_PROMPT', "You're a helpful assistant, a member of the group chat. Respond in at most 3 sentences.")

URL_REGEXP = re.compile(
    r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

DENO_PATH = os.getenv('DENO')

server_states = {}

# gemini_model = genai.GenerativeModel('gemini-1.5-flash')
# system_instruction="You are a casual chat assistant on Discord. No matter what the user asks, your response must be extremely brief and direct. Strictly limit the length of your replies: a maximum of 1 to 3 sentences. Absolutely do not write long-winded paragraphs, create long lists, or include any fluff."

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
    'cookiefile': 'cookies.txt',
    'source_address': '0.0.0.0',
    'js_runtimes': {'deno': {'path': DENO_PATH}},
    'remote_components': ['ejs:github'],
    'extractor_args': {
        'youtube': {'player_client': ['web']},
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        #'Referer': 'https://www.bilibili.com/'
    }
}

# ---------- Bot 初始化 ----------
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
synced_flag = False

bot = commands.Bot(
    command_prefix='$',
    intents=intents,
    help_command=None
)


@bot.command()
async def kickbot(ctx, user: discord.Member):
    # 删除发送的指令
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        print("权限不足")
    except Exception as e:
        print(f"信息删除失败：{e}")
        pass
    # 踢出语音频道
    try:
        await user.move_to(None)
    except discord.Forbidden:
        print("权限不足")
    except Exception as e:
        print(f"踢人失败：{e}")
        pass

# --控制台输入--
async def console_input():
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


async def delete_specific_message():
    channel_id = None
    message_id = None

    channel = bot.get_channel(channel_id)

    if channel:
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
            print("Message deleted successfully.")

        except discord.NotFound:
            print("Error: Message not found. It may have already been deleted.")
        except discord.Forbidden:
            print("Error: The bot does not have permission to delete this message.")
        except discord.HTTPException as e:
            print(f"Failed to delete message due to an HTTP error: {e}")
    else:
        print("Error: Channel not found.")

# --事件--
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    # await delete_specific_message()
    bot.loop.create_task(console_input())
    # 启动时检查幽灵语音连接
    for guild in bot.guilds:
        if guild.me and guild.me.voice:
            try:
                await guild.change_voice_state(channel=None)
                print(f"Successfully cleared ghost voice connection in: {guild.name}")
            except Exception as e:
                print(f"Failed to clear ghost voice connection in {guild.name}: {e}")
    # 同步命令
    global synced_flag
    if not synced_flag:
        try:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} slash command(s)")
            synced_flag = True
        except Exception as e:
            print(f"Failed to sync commands: {e}")

# --辅助函数--
def get_state(guild_id):
    if guild_id not in server_states:
        server_states[guild_id] = {
            'queue': [],
            #'loop': False,
            'was_stopped': False,
            'skipped': {'was_skipped': False, 'skipped_title': None},
            'current_title': None,
            'current_url': None,
            'local_file': False,
            'is_loading': False
        }
    return server_states[guild_id]

async def audio_after(error, channel):
    """播放完毕后处理"""
    state = get_state(channel.guild.id)
    current_item = state['queue'][0] if state['queue'] else {}
    is_silent = current_item.get('silent', False)

    if error:
        print(f"Audio error: {error}")
    if state['was_stopped']:
        state['was_stopped'] = False
        return
    if state['skipped']['was_skipped']:
        state['skipped']['was_skipped'] = False
        if not is_silent:
            await channel.send(f"Skipped: **{state['skipped']['skipped_title']}**")
    elif state['queue'] and state['queue'][0]['loop']:
        bot.loop.create_task(play_next(channel))
        return
    else:
        if not is_silent:
            await channel.send(f"Song finished playing: **{state['current_title']}**")
    if state['queue']:
        state['queue'].pop(0)
    bot.loop.create_task(play_next(channel, was_silent=is_silent))

# async def play_next(channel, voice_client, was_silent: bool = False):
# **voice_client was removed as a parameter, due to internet instabilities possibly causing desynchronizations and breaking the object.**
async def play_next(channel, was_silent: bool = False):

    guild = channel.guild
    state = get_state(channel.guild.id)

    voice_client = guild.voice_client
    if not voice_client or not voice_client.is_connected():
        state['is_loading'] = False
        state['queue'].clear()
        if not was_silent:
            await channel.send("Bot is disconnected from the voice channel.")
        return

    if state['is_loading']:
        return
    elif not state['queue'] or len(state['queue']) == 0:
        if not was_silent:
            await channel.send("Queue finished.")
        return

    state['is_loading'] = True
    
    try:
        source = None
        title = "Unknown"

        current_item = state['queue'][0]
        
        if current_item['type'] == 'url':
            url = state['queue'][0]['data']
            source, title = await source_obj_compiler(url)
        elif current_item['type'] == 'file':
            url = state['queue'][0]['data']
            title = Path(url).name
            source = discord.FFmpegOpusAudio(url, executable=FFMPEG_PATH, options='-vn')
            state['local_file'] = True

        if source:
            state['current_title'] = title
            state['current_url'] = url
            #if not state['loop']:
            if state['queue'][0]['outputFirstTime']:
                if not state['queue'][0].get('silent', False):
                    await channel.send(f"Now playing: **{title}**")
                state['queue'][0]['outputFirstTime'] = False
                
            voice_client.play(source, after=lambda e: bot.loop.create_task(audio_after(e, channel)))
            state['is_loading'] = False
        else:
            if not state['queue'][0].get('silent', False):
                await channel.send(f"Failed to play: {url}, skipping...")
            # 继续尝试播放下一首
            state['is_loading'] = False
            if state['queue']:
                state['queue'].pop(0)
            bot.loop.create_task(play_next(channel))
        #if not state['queue'][0]['loop'] and not state['skipped']['was_skipped']:
        #    state['queue'].pop(0)
    except Exception as e:
        print(f"Error in play_next: {e}")
        await channel.send("An unexpected error occurred. Skipping... [from func play_next()]")

        state['is_loading'] = False
        if state['queue']:
            state['queue'].pop(0)
        if channel.guild.voice_client and channel.guild.voice_client.is_connected():
            bot.loop.create_task(play_next(channel))
        else:
            state['queue'].clear()
        
async def source_obj_compiler(url: str):
    try:
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
            
            if 'entries' in info:
                entries = info.get('entries', [])
                
                for entry in entries:
                    if not entry:
                        continue
                    ie_key = entry.get('ie_key', '')
                    entry_url = entry.get('url', '') or ''
                    if ie_key in ['YoutubeChannel', 'YoutubeTab', 'YoutubePlaylist']:
                        continue
                    if '/channel/' in entry_url or '/@' in entry_url or '/playlist' in entry_url:
                        continue
                    
                    try:
                        if entry.get('_type') == 'url' or not entry.get('url'):
                            video_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry['id']}"
                            deep_options = YDL_OPTIONS.copy()
                            deep_options['extract_flat'] = False
                            
                            with yt_dlp.YoutubeDL(deep_options) as ydl_deep:
                                entry_info = await asyncio.to_thread(ydl_deep.extract_info, video_url, download=False)
                        else:
                            entry_info = entry
                        
                        stream_url = entry_info.get('url')
                        if not stream_url:
                            print(f"Result '{entry_info.get('title')}' missing stream URL. Trying next search result...")
                            continue
                            
                        title = entry_info.get('title', 'Unknown')
                        headers = entry_info.get('http_headers', {}) or entry_info.get('https_headers', {})
                        
                        b_options = '-re -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
                        if headers:
                            header_str = "".join([f"{k}: {v}\r\n" for k, v in headers.items()])
                            b_options += f' -headers "{header_str}"'
                            
                        a_options = '-vn '
                        
                        source = discord.FFmpegOpusAudio(
                            stream_url,
                            before_options=b_options,
                            options=a_options,
                            executable=FFMPEG_PATH
                        )
                        return source, title
                        
                    except Exception as entry_err:
                        print(f"Search result failed deep extraction ({entry_err}). Trying next result...")
                        continue
                
                print("Error: None of the search results could be played.")
                return None, None
            
            else:
                stream_url = info.get('url')
                if not stream_url:
                    return None, None
                    
                title = info.get('title', 'Unknown')
                headers = info.get('http_headers', {}) or info.get('https_headers', {})
                
                b_options = '-re -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
                if headers:
                    header_str = "".join([f"{k}: {v}\r\n" for k, v in headers.items()])
                    b_options += f' -headers "{header_str}"'
                    
                a_options = '-vn '
                
                source = discord.FFmpegOpusAudio(
                    stream_url,
                    before_options=b_options,
                    options=a_options,
                    executable=FFMPEG_PATH
                )
                return source, title

    except Exception as e:
        print(f"Critical error when compiling Source obj: {e}")
        return None, None

# ---------- 斜杠命令 ----------

# 1. /help
@bot.tree.command(name="help", description="显示所有可用命令")
@app_commands.guild_only()
async def help_command(interaction: discord.Interaction):
    help_text = (
        "/help - 显示此帮助\n"
        "/dir - 列出可播放的本地文件\n"
        "/join - 让机器人加入语音频道\n"
        "/disconnect - 让机器人退出语音频道\n"
        "/play [url] [loop] [file] - 播放音乐\n"
        "    - url: 视频链接或搜索关键词\n"
        "    - loop: 是否循环\n"
        "    - file: 可选，本地文件名\n"
        "/stop - 停止播放\n"
        "/hello - 打个招呼\n"
        "/like - 点个赞\n"
        "/dislike - 踩一下"
    )
    await interaction.response.send_message(help_text)

# 2. /hello
@bot.tree.command(name="hello", description="打个招呼")
@app_commands.guild_only()
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hello {interaction.user.name}.")

# 3. /like
@bot.tree.command(name="like", description="点个赞")
@app_commands.guild_only()
async def like(interaction: discord.Interaction):
    await interaction.response.send_message("Thanks ❤️")

# 4. /dislike
@bot.tree.command(name="dislike", description="踩一下")
@app_commands.guild_only()
async def dislike(interaction: discord.Interaction):
    await interaction.response.send_message("Sorry")

# 5. /dir
@bot.tree.command(name="dir", description="列出可播放的本地文件")
@app_commands.guild_only()
async def dir_list(interaction: discord.Interaction):
    extensions = ('mp3', 'mp4', 'wav', 'm4a', 'ogg')
    filenames = []
    if MUSIC_PATH.exists():
        for file in os.listdir(MUSIC_PATH):
            if file.lower().endswith(extensions):
                filenames.append(file)
    if filenames:
        await interaction.response.send_message("Available files: " + ', '.join(filenames))
    else:
        await interaction.response.send_message("No music files found in Music folder.")

# 6. /join
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

# 7. /kickbot

#@bot.tree.command(name="kickbot", description="将用户踢出语音频道（需要移动成员权限）")
#async def kickbot(interaction: discord.Interaction, user: discord.Member):
#   if not interaction.user.guild_permissions.move_members:
#        await interaction.response.send_message("You need 'Move Members' permission.")
#        return
#    if user.voice:
#        try:
#            await user.move_to(None)
#            await interaction.response.send_message(f"Kicked {user.display_name} from voice.")
#        except Exception as e:
#            await interaction.response.send_message(f"Error: {e}")
#    else:
#        await interaction.response.send_message("User is not in a voice channel.")

# 8. /stop
@bot.tree.command(name="stop", description="停止当前播放的音乐")
@app_commands.guild_only()
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    state = get_state(interaction.guild.id)
    state['queue'].clear()  # Clear the queue when stopping
    if voice_client and voice_client.is_playing():
        state['was_stopped'] = True
        voice_client.stop()

        await interaction.response.send_message("Song terminated.")
    else:
        await interaction.response.send_message("Not playing anything.")

# 9. /play
@bot.tree.command(name="play", description="播放音乐（支持 URL、关键词、本地文件）")
@app_commands.guild_only()
async def play(
    interaction: discord.Interaction,
    url: Optional[str] = None,
    loop: bool = False,
    file: Optional[str] = None
):
    """通过斜杠命令参数接收播放内容"""
    try:
        await interaction.response.defer(thinking=True)
    except discord.errors.NotFound:
        await interaction.channel.send(f"<@{interaction.user.id}> 服务器有点卡顿，但我正在处理你的请求...")
        return
    except Exception as e:
        print(f"Failed to defer interaction: {e}")
        return

    voice_client = interaction.guild.voice_client
    state = get_state(interaction.guild.id)

    if not voice_client or not voice_client.is_connected():
        if interaction.user.voice:
            try:
                voice_client = await interaction.user.voice.channel.connect(self_deaf=True)
                # new playlist
                state['queue'] = []
            except Exception as e:
                await interaction.followup.send(f"Cannot join voice: {e}")
                return
        else:
            await interaction.followup.send("You need to be in a voice channel first.")
            return

    # 如果指定了 file 参数，使用本地文件；否则使用 url
    if file:
        # 防止路径遍历
        file_path = Path(MUSIC_PATH) / file

        if not file_path.resolve().is_relative_to(MUSIC_PATH.resolve()):
            await interaction.followup.send("Illegal directory access.")
            return

        if not file_path.exists():
            await interaction.followup.send("File not found.")
            return
        # state['current_title'] = file_path.name
        # state['current_url'] = str(file_path)
        state['local_file'] = True
        #state['loop'] = loop
        state['was_stopped'] = False

        state['queue'].append({
            'type': 'file',
            'data': str(file_path),
            'loop': loop,
            'title': file_path.name,
            'outputFirstTime': True
        })
        await interaction.followup.send(f"Added local file to queue: {file_path.name}")

        if not voice_client.is_playing():
            await play_next(interaction.channel)
    else:
        # 需要 url 参数
        tempName = url;
        if not url:
            await interaction.followup.send("Please provide a URL or use `file` option.")
            return
        if not URL_REGEXP.match(url):
            search_query = f"{url}"
            encoded_query = urllib.parse.quote(search_query)
            url = f"https://www.youtube.com/results?search_query={encoded_query}&sp=EgIQAQ%253D%253D"
        #state['loop'] = loop
        state['was_stopped'] = False
        state['local_file'] = False
        state['queue'].append({
            'type': 'url',
            'data': url,
            'loop': loop,
            'outputFirstTime': True
        })
        if voice_client.is_playing():
            await interaction.followup.send(f"Added to queue: {tempName}", suppress_embeds=True)
            return
        else:
            await interaction.followup.send(f"Preparing to play: {tempName}", suppress_embeds=True)
    if not voice_client.is_playing():
        await play_next(interaction.channel)
    return


# 10. /skip
@bot.tree.command(name="skip", description="跳过当前歌曲")
@app_commands.guild_only()
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    state = get_state(interaction.guild.id)
    if voice_client and voice_client.is_playing():
        # state['was_stopped'] = True  # 标记主动停止，避免被误判为正常结束
        state['skipped']['was_skipped'] = True
        state['skipped']['skipped_title'] = state['current_title']
        voice_client.stop()
        await interaction.response.send_message("Skipped current song.")
    else:
        await interaction.response.send_message("Not playing anything.")

# 11. /disconnect
@bot.tree.command(name="disconnect", description="离开语音频道")
@app_commands.guild_only()
async def disconnect(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()

        # 释放内存
        server_states.pop(interaction.guild.id, None)

        await interaction.response.send_message("Disconnected.", ephemeral=True)
    else:
        await interaction.response.send_message("Not in a voice channel.", ephemeral=True)
# 监听消息事件
@bot.event
async def on_message(message):
    # 防止精神分裂
    if message.author == bot.user:
        return
    elif message.guild is None:
        await message.channel.send("DM Unssupported.")
        return

    if message.content == '我坐好了':
        file = discord.File("etc/我坐好了.jpg", filename="我坐好了.jpg")
        await message.channel.send("真拿你没办法，坐好喽。 那是科隆Major赛场，一场足以载入CS史册的巅峰加冕。场馆万众屏息，喧嚣与沉寂反复更迭，每一次枪声、每一回残局，都是追梦路上最滚烫的注脚。十余载浮沉逐梦，无数次决赛折戟、无数轮遗憾离场，命运无数次将希望与重担，压在孤身前行的NiKo肩上。 他是驰骋赛场多年的传奇，枪法、心态、大赛底蕴早已炉火纯青。常年游走于生死残局，历经无数豪门对决与高压对局，他早已习惯孤身扛队，在绝境之中寻找破局的微光，是无数人心中永不低头的赛场利刃。 这条夺冠之路跌宕滚烫，布满荆棘与考验。小组赛的坚韧坚守，淘汰赛的绝境翻盘，对阵各路豪强的鏖战争锋，一路跌撞前行、逆势而上。褪去年少的急躁锋芒，如今的NiKo沉稳笃定，没有花哨的操作，唯有千锤百炼的功底与千帆过尽的从容。每一次架枪坚守，都是重压之下的担当；每一次残局突围，都是不甘遗憾的抗争。 历经十余年漫长蛰伏，跨过无数失利低谷，熬过无数彻夜磨砺，NiKo终于登顶科隆之巅，摘下梦寐以求的Major桂冠。压抑多年的遗憾尽数释然，长久的奔赴与坚守终得圆满。赛场欢呼轰然炸裂，聚光灯尽数汇聚在他身上，昔日的无冕之王，终于加冕成王。", file=file)
        guild_list = [1351664011803889706, 825397782185508904, ]
        if message.guild.id in guild_list and message.author.voice:
            user_voice_channel = message.author.voice.channel
            voice_client = message.guild.voice_client

            if not voice_client:
                try:
                    voice_client = await user_voice_channel.connect(self_deaf=True)
                except Exception as e:
                    print(f"Failed to connect to voice channel: {e}")
                    return
            elif voice_client.channel != user_voice_channel:
                return

            state = get_state(message.guild.id)
            state['was_stopped'] = False

            new_song = {
                'type': 'file',
                'data': str(Path(MUSIC_PATH) / '我坐好了.mp4'),
                'loop': False,
                'outputFirstTime': False,
                'silent': True
            }

            if voice_client.is_playing():
                state['queue'].insert(1, new_song)
                state['skipped']['was_skipped'] = False
                voice_client.stop()
            else:
                state['queue'].append(new_song)
                await play_next(message.channel, was_silent=True)
            

    # 判断这条消息是否有@
    if bot.user in message.mentions:
        user_question = message.content.replace(f'<@{bot.user.id}>', '').strip()

        # 防止有人只@
        if user_question:
            # 显示正在输入中
            async with message.channel.typing():
                try:
                    contents = []
                    async for msg in message.channel.history(limit=7):
                        if not msg.content: continue

                        role = 'model' if msg.author == bot.user else 'user'

                        if msg.author == bot.user:
                            text_content = msg.clean_content
                        else:
                            text_content = f"The user {msg.author.display_name} said: {msg.clean_content}"

                        contents.append({
                            "role": role,
                            "parts": [{"text": text_content}]
                        })

                    contents.reverse()

                    merged_contents = []
                    for msg in contents:
                        if merged_contents and merged_contents[-1]["role"] == msg["role"]:
                            merged_contents[-1]["parts"][0]["text"] += f"\n{msg['parts'][0]['text']}"
                        else:
                            merged_contents.append(msg)
                    
                    # 统一对合并后的每一块内容进行字数检查与截断
                    for msg in merged_contents:
                        original_text = msg["parts"][0]["text"]
                        if len(original_text) > 300:
                            msg["parts"][0]["text"] = original_text[:300] + "...(此处省略一万字垃圾话)"

                    # 获取机器人真名 身份锚定
                    bot_name = bot.user.display_name
                    
                    response = await gemini_client.aio.models.generate_content(
                        model='gemini-3.1-flash-lite',
                        contents=merged_contents,
                        config=types.GenerateContentConfig(
                            safety_settings=[
                                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
                            ],
                            system_instruction=f"Your name is {bot_name}.\n" + GEMINI_PROMPT + f" Current Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        )
                    )

                    reply_text = response.text
                    
                    pattern = re.compile(rf"^\**\[?{re.escape(bot_name)}\]?\**:\s*")

                    # 只替换开头匹配到的部分一次
                    reply_text = pattern.sub("", reply_text).strip()

                    if len(reply_text) > 500:
                        reply_text = reply_text[:500] + "...\n[字数超限]"

                    await message.channel.send(reply_text)

                except Exception as e:
                    print(f"API报错：{e}")
                    if "503" in str(e):
                        await message.channel.send("Busy（api busy 503）")
                    elif "429" in str(e):
                        await message.channel.send("Too frequent!（api rate limit 429）")
                    else:
                        await message.channel.send("Bad internet connection!（api Error）")
    # 放行 $kickbot
    await bot.process_commands(message)
# 监听退群事件
@bot.event
async def on_guild_remove(guild):
    if guild.id in server_states:
        server_states.pop(guild.id)
        print(f"已离开服务器 {guild.name} ({guild.id})，成功释放内存。")
# ---------- 启动 ----------
if __name__ == "__main__":
    bot.run(TOKEN)