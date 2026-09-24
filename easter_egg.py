"""自用彩蛋：'我坐好了' 触发，不影响主程序风格，主程序只调用 handle()。"""
from pathlib import Path

import discord

TRIGGER = '我坐好了'
TEXT_MESSAGE = '真拿你没办法，坐好喽。 那是科隆Major赛场，一场足以载入CS史册的巅峰加冕。场馆万众屏息，喧嚣与沉寂反复更迭，每一次枪声、每一回残局，都是追梦路上最滚烫的注脚。十余载浮沉逐梦，无数次决赛折戟、无数轮遗憾离场，命运无数次将希望与重担，压在孤身前行的NiKo肩上。 他是驰骋赛场多年的传奇，枪法、心态、大赛底蕴早已炉火纯青。常年游走于生死残局，历经无数豪门对决与高压对局，他早已习惯孤身扛队，在绝境之中寻找破局的微光，是无数人心中永不低头的赛场利刃。 这条夺冠之路跌宕滚烫，布满荆棘与考验。小组赛的坚韧坚守，淘汰赛的绝境翻盘，对阵各路豪强的鏖战争锋，一路跌撞前行、逆势而上。褪去年少的急躁锋芒，如今的NiKo沉稳笃定，没有花哨的操作，唯有千锤百炼的功底与千帆过尽的从容。每一次架枪坚守，都是重压之下的担当；每一次残局突围，都是不甘遗憾的抗争。 历经十余年漫长蛰伏，跨过无数失利低谷，熬过无数彻夜磨砺，NiKo终于登顶科隆之巅，摘下梦寐以求的Major桂冠。压抑多年的遗憾尽数释然，长久的奔赴与坚守终得圆满。赛场欢呼轰然炸裂，尽数汇聚在他身上，昔日的无冕之王，终于加冕成王。'
IMAGE_PATH = 'etc/我坐好了.jpg'
IMAGE_NAME = '我坐好了.jpg'
CLIP_PATH = 'Music/我坐好了.mp4'
GUILD_WHITELIST = [1351664011803889706, 825397782185508904]


def build_clip() -> dict:
    """彩蛋音频的队列项；silent 表示不播报曲目消息。"""
    return {
        'type': 'file',
        'data': CLIP_PATH,
        'loop': False,
        'outputFirstTime': False,
        'silent': True,
    }


async def send_egg(message):
    """发送彩蛋图文；图片素材缺失（如未挂载 etc/）时只发文案。"""
    if Path(IMAGE_PATH).is_file():
        await message.channel.send(TEXT_MESSAGE, file=discord.File(IMAGE_PATH, filename=IMAGE_NAME))
    else:
        print(f"[警告] 找不到彩蛋图片：{Path(IMAGE_PATH).resolve()}")
        await message.channel.send(TEXT_MESSAGE)


async def handle(message, state, play_next) -> bool:
    """匹配 TRIGGER 时发送图文并强制跳到彩蛋音频，返回是否已处理。"""
    if message.content != TRIGGER:
        return False
    if message.guild.id not in GUILD_WHITELIST or not message.author.voice:
        return False

    voice_client = message.guild.voice_client
    channel = message.author.voice.channel
    if voice_client and voice_client.channel != channel:
        return False
    if not voice_client:
        try:
            voice_client = await channel.connect(self_deaf=True)
        except Exception as e:
            print(f"Failed to connect to voice channel: {e}")
            return True

    await send_egg(message)

    if not Path(CLIP_PATH).is_file():
        print(f"[警告] 找不到彩蛋音频：{Path(CLIP_PATH).resolve()}")
        return True

    state['was_stopped'] = False
    if voice_client.is_playing():
        state['queue'].insert(0, build_clip())
        state['skipped']['was_skipped'] = False
        state['force_next'] = True
        voice_client.stop()
    else:
        state['echoText'] = True
        state['queue'].append(build_clip())
        await play_next(message.channel, was_silent=True)
    return True
