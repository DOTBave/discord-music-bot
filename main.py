import discord
import os
import yt_dlp
from pathlib import Path
from dotenv import load_dotenv

YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
}
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# Load the secret token from the .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
FFMPEG_PATH = os.getenv('FFMPEG_PATH', 'ffmpeg')
MUSIC_PATH = Path.cwd() / 'Music'

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
    def check_if_done(error):
        vc = message.guild.voice_client
        was_stopped = getattr(vc, 'was_stopped', False)
        current_title = getattr(vc, 'current_title', "Unknown Song")
        if error:
            print(f"Audio stopped due to error: {error}")
        elif was_stopped:
            vc.was_stopped = False
            # these two lines were disabled due to slow speed
            # they are now in the if $stop block
            # coro = message.channel.send('Song terminated.')
            # client.loop.create_task(coro)
        else:
            coro = message.channel.send(f"Song finished playing: **{current_title}**")
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
        for file in os.listdir(MUSIC_PATH):
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
        newmessage = message.content.removeprefix('$play ')
        voice_client = message.guild.voice_client

        if voice_client and voice_client.is_connected():
            voice_client.was_stopped = False
            async with message.channel.typing():
                b_options = ""
                try:
                    if newmessage.startswith('-file'):
                        userinputfile = newmessage.removeprefix('-file').lstrip()
                        print(userinputfile)
                        # print(filename)
                        voice_client.current_title = os.path.basename(userinputfile)
                        print(voice_client.current_title)
                        filepath = Path(MUSIC_PATH).joinpath(voice_client.current_title).resolve()
                        print(MUSIC_PATH)
                        print(filepath)
                        if not userinputfile.startswith(os.path.abspath(MUSIC_PATH)) and userinputfile != voice_client.current_title:
                            await message.channel.send('Illegal Directory')
                            return
                        if not filepath.exists():
                            await message.channel.send('File not found.')
                            return
                    else:
                    # Url extraction
                        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                            info = ydl.extract_info(newmessage, download=False)
                            filepath = info['url']
                            voice_client.current_title = info['title']
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
                        filepath,
                        executable=FFMPEG_PATH,
                        before_options=b_options,
                        options='-vn'
                    )
                    voice_client.play(source, after=check_if_done)

                    # audio_source = discord.FFmpegPCMAudio(audioFileName)
                    #voice_client.play(source)
                    await message.channel.send(f'Playing **{voice_client.current_title}**.')
                except Exception as e:
                    await message.channel.send(f'Error occurred: {e}')
        else:
            await message.channel.send('You need to be in a voice channel.')
    if message.content.startswith('$stop'):
        voice_client = message.guild.voice_client
        if voice_client and voice_client.is_connected():
            voice_client.was_stopped = True
            voice_client.stop()
            await message.channel.send('Song Terminated')
            # check_if_done(None)
            # await message.channel.send('Stopped.')
        else:
            await message.channel.send('Not in a voice channel.')
            return
    if message.content == '$👍':
        await message.channel.send('Thanks ❤️')
    elif message.content == '$👎':
        await message.channel.send('Sorry (')
client.run(TOKEN)