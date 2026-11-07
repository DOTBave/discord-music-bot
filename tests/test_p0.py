"""P0 回归测试：不依赖网络、Discord token 或真实语音。

运行：python -m pytest -q
"""
import asyncio
import shlex
from pathlib import Path

import main


# ---------- 纯函数 ----------

def test_import_without_gemini_key():
    if main.GEMINI_API_KEY:
        assert main.gemini_client is not None
    else:
        assert main.gemini_client is None


def test_split_url_strips_share_text():
    assert main.split_url("https://b23.tv/abc 快来围观") == ("https://b23.tv/abc", "https://b23.tv/abc")
    assert main.split_url("<https://youtu.be/xyz>")[0] == "https://youtu.be/xyz"
    assert main.split_url("看看这个 https://b23.tv/abc")[0] == "https://b23.tv/abc"
    assert main.split_url("周杰伦 晴天") == (None, "周杰伦 晴天")


def test_youtube_detection_uses_hostname():
    assert main.is_youtube_url("https://www.youtube.com/watch?v=x") is True
    assert main.is_youtube_url("https://youtu.be/x") is True
    assert main.is_youtube_url("https://youtube.com.evil.com/x") is False
    assert main.is_youtube_url("https://notyoutube.com/watch") is False


def test_permanent_errors_win_over_retryable_signs():
    assert main.is_retryable_ytdlp_error(
        "Sign in to confirm your age. This video may be inappropriate for some users."
    ) is False
    assert main.is_retryable_ytdlp_error(
        "ERROR: Private video. Sign in if you have been granted access"
    ) is False
    assert main.is_retryable_ytdlp_error("Sign in to confirm you are not a bot") is True
    assert main.is_retryable_ytdlp_error("Unable to download webpage") is True
    assert main.is_retryable_ytdlp_error("Video unavailable") is False
    assert main.is_retryable_ytdlp_error(None) is False


def test_cookiefile_only_when_regular_file():
    assert ("cookiefile" in main.YDL_OPTIONS) == Path(main.COOKIE_FILE).is_file()


def test_build_audio_options_quotes_headers():
    opts = main.build_audio_options({"User-Agent": 'Mozilla "quoted"', "X-Test": "a b"})
    tokens = shlex.split(opts)
    assert "-headers" in tokens
    assert "-re" not in tokens
    header_value = tokens[tokens.index("-headers") + 1]
    crlf = chr(13) + chr(10)
    assert header_value == "User-Agent: Mozilla " + chr(34) + "quoted" + chr(34) + crlf + "X-Test: a b" + crlf
    assert main.build_audio_options({}) == main.RECONNECT_OPTIONS


def test_clip_text():
    clipped = main.clip_text("x" * 3000)
    assert len(clipped) <= main.MAX_MESSAGE_LEN
    assert clipped.endswith("...(内容过长已截断)")
    assert main.clip_text("hi") == "hi"
    assert main.clip_text(None) == ""


# ---------- 状态机（fake voice client，不联网） ----------

class FakeSource:
    def __init__(self):
        self.cleaned = False

    def cleanup(self):
        self.cleaned = True


class FakeResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, content=None, **kwargs):
        self.messages.append(content)


class FakeInteraction:
    def __init__(self, guild):
        self.guild = guild
        self.response = FakeResponse()


class FakeGuild:
    _next_id = 100000

    def __init__(self):
        FakeGuild._next_id += 1
        self.id = FakeGuild._next_id
        self.voice_client = None


class FakeVoiceClient:
    def __init__(self):
        self._playing = False
        self.played = []
        self.after = None
        self.channel = None

    def is_connected(self):
        return True

    def is_playing(self):
        return self._playing

    def play(self, source, after=None):
        self._playing = True
        self.played.append(source)
        self.after = after

    def stop(self):
        self._playing = False
        after = self.after
        self.after = None
        if after:
            after(None)


class FakeChannel:
    def __init__(self, guild):
        self.guild = guild
        self.messages = []

    async def send(self, content=None, **kwargs):
        self.messages.append(content)


def _make_player():
    guild = FakeGuild()
    voice_client = FakeVoiceClient()
    guild.voice_client = voice_client
    channel = FakeChannel(guild)
    main.server_states.pop(guild.id, None)
    state = main.get_state(guild.id)
    return guild, voice_client, channel, state


def test_stop_during_load_discards_source():
    async def scenario():
        main.bot.loop = asyncio.get_running_loop()
        guild, voice_client, channel, state = _make_player()
        created = []
        compile_started = asyncio.Event()
        release = asyncio.Event()

        async def slow_compile(url):
            source = FakeSource()
            created.append(source)
            compile_started.set()
            await release.wait()
            return source, "Fake", None

        old = main.source_obj_compiler
        main.source_obj_compiler = slow_compile
        try:
            result = main.enqueue(channel, {
                "type": "url",
                "data": "https://example.com/v",
                "loop": False,
                "outputFirstTime": True,
            })
            assert result == "started"
            await asyncio.wait_for(compile_started.wait(), timeout=2)
            interaction = FakeInteraction(guild)
            await main.stop.callback(interaction)
            assert interaction.response.messages == ["Song terminated."]
            release.set()
            await asyncio.sleep(0.2)
            assert not voice_client.played
            assert created and all(source.cleaned for source in created)
            assert state["is_loading"] is False
            assert state["queue"] == []
            assert state["load_gen"] == 1
        finally:
            main.source_obj_compiler = old
            main.server_states.pop(guild.id, None)

    asyncio.run(scenario())


def test_enqueue_tri_state():
    async def scenario():
        main.bot.loop = asyncio.get_running_loop()
        guild, voice_client, channel, state = _make_player()
        state["is_loading"] = True
        assert main.enqueue(channel, {"type": "file", "data": "/tmp/x.mp3"}) == "queued"
        state["is_loading"] = False
        voice_client._playing = True
        assert main.enqueue(channel, {"type": "file", "data": "/tmp/x.mp3"}) == "queued"
        main.server_states.pop(guild.id, None)

    asyncio.run(scenario())


def test_audio_after_ignores_cleared_state():
    async def scenario():
        main.bot.loop = asyncio.get_running_loop()
        guild, voice_client, channel, state = _make_player()
        state["current"] = None
        await main.audio_after(None, channel)
        assert channel.messages == []
        main.server_states.pop(guild.id, None)

    asyncio.run(scenario())


def test_schedule_from_worker_thread():
    async def scenario():
        main.bot.loop = asyncio.get_running_loop()
        flag = {}

        async def coro():
            flag["ran"] = True

        future = await asyncio.to_thread(main._schedule, coro())
        assert future is not None
        await asyncio.sleep(0.05)
        assert flag.get("ran") is True

    asyncio.run(scenario())


def test_finished_track_advances_queue():
    async def scenario():
        main.bot.loop = asyncio.get_running_loop()
        guild, voice_client, channel, _state = _make_player()
        sources = []

        async def fake_compile(url):
            source = FakeSource()
            sources.append(source)
            return source, "T", None

        old = main.source_obj_compiler
        main.source_obj_compiler = fake_compile
        try:
            main.enqueue(channel, {
                "type": "url",
                "data": "https://example.com/1",
                "loop": False,
                "outputFirstTime": False,
            })
            await asyncio.sleep(0.1)
            assert len(voice_client.played) == 1
            after = voice_client.after
            voice_client._playing = False
            after(None)
            await asyncio.sleep(0.2)
            assert "Queue finished." in channel.messages
        finally:
            main.source_obj_compiler = old
            main.server_states.pop(guild.id, None)

    asyncio.run(scenario())
