import discord
import os
import yt_dlp
from dotenv import load_dotenv

YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': 'True',
}
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# Load the secret token from the .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
FFMPEG_PATH = os.getenv('FFMPEG_PATH', 'ffmpeg')

# Define "Intents"
intents = discord.Intents.default()
intents.message_content = True

# Initialize the bot client
client = discord.Client(intents=intents)

# Create an event listener for when the bot finishes logging in
@client.event
async def on_ready():
    print(f'Success! Logged in as {client.user}')

# For sending and replying to messages
@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if message.content == '!hello':
        await message.channel.send(f'Hello {message.author.name}.')
    if message.content == '!join':
        if message.author.voice:
            try:
                channel = message.author.voice.channel
                await channel.connect()
                await message.channel.send('Joined channel.')
            except Exception as e:
                await message.channel.send(f'Error occurred while joining channel: {e}')
        else:
            await message.channel.send('You need to be in a voice channel.')
    if message.content.startswith('!play '):
        url = message.content.removeprefix('!play ')
        voice_client = message.guild.voice_client

        if voice_client and voice_client.is_connected():
            async with message.channel.typing():
                try:
                    # Url extraction
                    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                        info = ydl.extract_info(url, download=False)
                        url2 = info['url']

                        # Extract the security headers ---
                        headers = info.get('http_headers', {})
                        header_args = ""
                        for key, value in headers.items():
                            header_args += f"{key}: {value}\r\n"

                        # Inject the headers into FFmpeg ---
                        b_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
                        if header_args:
                            b_options += f' -headers "{header_args}"'

                    source = discord.FFmpegOpusAudio(
                        url2,
                        executable=FFMPEG_PATH,
                        before_options=b_options,
                        options='-vn'
                    )

                    def check_if_done(error):
                        if error:
                            print(f"Audio stopped due to error: {error}")
                        else:
                            coro = message.channel.send(f"Song finished playing: **{info['title']}**")
                            client.loop.create_task(coro)
                    voice_client.play(source, after=check_if_done)

                    # audio_source = discord.FFmpegPCMAudio(audioFileName)
                    #voice_client.play(source)
                    await message.channel.send(f'Playing **{info["title"]}**.')
                except Exception as e:
                    await message.channel.send(f'Error occurred: {e}')
        else:
            await message.channel.send('You need to be in a voice channel.')
    if message.content == '!👍':
        await message.channel.send('Thanks 👍')
client.run(TOKEN)