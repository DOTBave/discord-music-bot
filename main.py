import discord
import os
import yt_dlp
#import pathlib
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
    def check_if_done(error, terminated=False):
        if error:
            print(f"Audio stopped due to error: {error}")
        elif terminated:
            coro = message.channel.send('Song terminated.')
            client.loop.create_task(coro)
        else:
            coro = message.channel.send(f"Song finished playing: **{title}**")
            client.loop.create_task(coro)
    if message.author == client.user:
        return
    if message.content == '$help':
         await message.channel.send("$help for this command\n$dir for available files for playing\n$join for the bot to join the channel\n$play [url] to play with an url\n$play -file [filename] to play a local file\n$stop to stop playing")
    if message.content == '$hello':
        await message.channel.send(f'Hello {message.author.name}.')
    if message.content == '$dir':
        extensions = ('mp3', 'mp4', 'wav', 'm4a', 'ogg')
        filenames = []
        for file in os.listdir(os.getcwd()):
            if file.endswith(extensions):
                filenames.append(file)
        await message.channel.send(f"Available files: " + ', '.join(filenames))
    if message.content == '$join':
        if message.author.voice:
            try:
                channel = message.author.voice.channel
                await channel.connect()
                await message.channel.send('Joined channel.')
            except Exception as e:
                await message.channel.send(f'Error occurred while joining channel: {e}')
        else:
            await message.channel.send('You need to be in a voice channel.')
    if message.content.startswith('$play '):
        newMessage = message.content.removeprefix('$play ')
        voice_client = message.guild.voice_client

        if voice_client and voice_client.is_connected():
            async with message.channel.typing():
                b_options = ""
                try:
                    if newMessage.startswith('-file '):
                        filePath = newMessage.removeprefix('-file ')
                        title = filePath
                        # voice_client.play(localFileName, after=check_if_done)
                    else:
                    # Url extraction
                        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                            info = ydl.extract_info(newMessage, download=False)
                            filePath = info['url']
                            title = info['title']
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
                        filePath,
                        executable=FFMPEG_PATH,
                        before_options=b_options,
                        options='-vn'
                    )
                    voice_client.play(source, after=check_if_done)

                    # audio_source = discord.FFmpegPCMAudio(audioFileName)
                    #voice_client.play(source)
                    await message.channel.send(f'Playing **{title}**.')
                except Exception as e:
                    await message.channel.send(f'Error occurred: {e}')
        else:
            await message.channel.send('You need to be in a voice channel.')
    if message.content.startswith('$stop'):
        voice_client = message.guild.voice_client
        if voice_client and voice_client.is_connected():
            voice_client.stop()
            check_if_done(None,True)
            # await message.channel.send('Stopped.')
    if message.content == '$👍':
        await message.channel.send('Thanks 👍')
    elif message.content == '$👎':
        await message.channel.send('Sorry :(')
client.run(TOKEN)