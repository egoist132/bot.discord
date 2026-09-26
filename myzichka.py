import asyncio
import os
import shutil
import time
from collections import deque
from pathlib import Path

import discord
from discord.ext import commands
import yt_dlp as youtube_dl


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": False,
    "no_warnings": True,
    "default_search": "auto",
}
FFMPEG_OPTIONS = {"options": "-vn"}
ytdl = youtube_dl.YoutubeDL(YTDL_OPTIONS)


def find_ffmpeg():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        package_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        candidates = sorted(
            package_root.glob("Gyan.FFmpeg_*/ffmpeg-*/bin/ffmpeg.exe"),
            reverse=True,
        )
        if candidates:
            return str(candidates[0])
    return "ffmpeg"


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title") or "Без названия"

    @classmethod
    async def from_url(cls, url, *, start_seconds=0, volume=0.5):
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=False)
        )
        if not data:
            raise RuntimeError("YouTube не вернул данные о видео.")
        if "entries" in data:
            data = next((entry for entry in data["entries"] if entry), None)
        if not data:
            raise RuntimeError("Не удалось найти видео по этой ссылке.")

        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        if start_seconds > 0:
            before_options = f"-ss {start_seconds:.2f} " + before_options
        source = discord.FFmpegPCMAudio(
            data["url"],
            executable=find_ffmpeg(),
            before_options=before_options,
            **FFMPEG_OPTIONS,
        )
        return cls(source, data=data, volume=volume)


class MusicState:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = deque()
        self.queue_ready = asyncio.Event()
        self.voice = None
        self.worker = None
        self.current = None
        self.repeat_current = False
        self.skip_requested = False
        self.seek_requested = False
        self.seek_target = 0.0
        self.position_offset = 0.0
        self.started_at = None
        self.paused_at = None
        self.paused_total = 0.0
        self.volume = 0.5
        self.text_channel = None


def current_position(state):
    if state.started_at is None:
        return state.position_offset
    now = state.paused_at if state.paused_at is not None else time.monotonic()
    elapsed = max(0.0, now - state.started_at - state.paused_total)
    return state.position_offset + elapsed


music_states = {}


def get_state(guild_id):
    state = music_states.get(guild_id)
    if state is None:
        state = MusicState(guild_id)
        music_states[guild_id] = state
    return state


async def resolve_track(query):
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(
        None, lambda: ytdl.extract_info(query, download=False)
    )
    if not info:
        raise RuntimeError("Не удалось найти видео по этой ссылке или запросу.")
    if "entries" in info:
        info = next((entry for entry in info["entries"] if entry), None)
    if not info:
        raise RuntimeError("По запросу не найдено видео.")

    return {
        "title": info.get("title") or "Без названия",
        "url": info.get("webpage_url") or info.get("original_url") or query,
        "duration": info.get("duration"),
    }


async def connect_member_to_voice(member, guild, state):
    if member.voice is None or member.voice.channel is None:
        raise RuntimeError("Сначала зайди в голосовой канал.")

    channel = member.voice.channel
    voice = guild.voice_client
    if voice is None:
        voice = await channel.connect()
    elif voice.channel != channel:
        await voice.move_to(channel)

    state.voice = voice
    return voice


async def enqueue_track(query, member, guild, text_channel):
    state = get_state(guild.id)
    await connect_member_to_voice(member, guild, state)
    track = await resolve_track(query)
    state.queue.append(track)
    state.queue_ready.set()
    state.text_channel = text_channel

    if state.worker is None or state.worker.done():
        state.worker = asyncio.create_task(play_queue(guild, state))
    position = len(state.queue) + (1 if state.current else 0)
    return track, position, state


class AddTrackModal(discord.ui.Modal, title="Добавить музыку в очередь"):
    link = discord.ui.TextInput(
        label="YouTube-ссылка или поисковый запрос",
        placeholder="Вставь ссылку или введи название песни",
        max_length=500,
    )

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message(
                "Эта панель относится к другому серверу.", ephemeral=True
            )
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Команда доступна только на сервере.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            track, position, _ = await enqueue_track(
                str(self.link.value).strip(),
                interaction.user,
                interaction.guild,
                interaction.channel,
            )
            await interaction.followup.send(
                f"Добавлено в очередь №{position}: **{track['title']}**",
                ephemeral=True,
            )
        except Exception as error:
            print(f"Ошибка добавления трека: {error}")
            await interaction.followup.send(str(error), ephemeral=True)


class MusicControls(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=3600)
        self.guild_id = guild_id
        state = music_states.get(guild_id)
        self.repeat_button.label = (
            "🔁 Повтор: вкл" if state and state.repeat_current else "🔁 Повтор: выкл"
        )

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message(
                "Эта панель относится к другому серверу.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="⏯ Пауза / продолжить", style=discord.ButtonStyle.secondary, row=0)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = music_states.get(self.guild_id)
        voice = state.voice if state else None
        if voice is None or not voice.is_connected():
            await interaction.response.send_message("Музыка сейчас не играет.", ephemeral=True)
            return
        if voice.is_paused():
            state.paused_total += time.monotonic() - state.paused_at
            state.paused_at = None
            voice.resume()
            message = "Воспроизведение продолжено."
        elif voice.is_playing():
            state.paused_at = time.monotonic()
            voice.pause()
            message = "Воспроизведение приостановлено."
        else:
            await interaction.response.send_message("Музыка сейчас не играет.", ephemeral=True)
            return
        await interaction.response.send_message(message, ephemeral=True)

    async def seek(self, interaction: discord.Interaction, seconds):
        state = music_states.get(self.guild_id)
        voice = state.voice if state else None
        if state is None or state.current is None or voice is None:
            await interaction.response.send_message("Сейчас нечего перематывать.", ephemeral=True)
            return
        if not (voice.is_playing() or voice.is_paused()):
            await interaction.response.send_message("Сейчас нечего перематывать.", ephemeral=True)
            return

        target = max(0.0, current_position(state) + seconds)
        duration = state.current.get("duration")
        if duration:
            target = min(target, max(0.0, duration - 1))
        state.seek_target = target
        state.seek_requested = True
        voice.stop()
        direction = "вперёд" if seconds > 0 else "назад"
        await interaction.response.send_message(
            f"Перематываю на 10 секунд {direction}.", ephemeral=True
        )

    @discord.ui.button(label="⏪ −10 сек", style=discord.ButtonStyle.secondary, row=0)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.seek(interaction, -10)

    @discord.ui.button(label="⏩ +10 сек", style=discord.ButtonStyle.secondary, row=0)
    async def forward_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.seek(interaction, 10)

    @discord.ui.button(label="⏭ Пропустить", style=discord.ButtonStyle.primary, row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = music_states.get(self.guild_id)
        voice = state.voice if state else None
        if voice is None or not (voice.is_playing() or voice.is_paused()):
            await interaction.response.send_message("Сейчас нечего пропускать.", ephemeral=True)
            return
        state.skip_requested = True
        voice.stop()
        await interaction.response.send_message("Трек пропущен.", ephemeral=True)

    @discord.ui.button(label="🔁 Повтор: выкл", style=discord.ButtonStyle.secondary, row=0)
    async def repeat_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = music_states.get(self.guild_id)
        if state is None:
            await interaction.response.send_message("Музыкальная очередь пуста.", ephemeral=True)
            return
        state.repeat_current = not state.repeat_current
        button.label = "🔁 Повтор: вкл" if state.repeat_current else "🔁 Повтор: выкл"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="🔉 Громкость −", style=discord.ButtonStyle.secondary, row=1)
    async def volume_down_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.change_volume(interaction, -0.1)

    @discord.ui.button(label="🔊 Громкость +", style=discord.ButtonStyle.secondary, row=1)
    async def volume_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.change_volume(interaction, 0.1)

    async def change_volume(self, interaction: discord.Interaction, delta):
        state = music_states.get(self.guild_id)
        if state is None:
            await interaction.response.send_message("Музыка сейчас не играет.", ephemeral=True)
            return
        state.volume = min(2.0, max(0.0, state.volume + delta))
        voice = state.voice
        if voice is not None and voice.source is not None:
            voice.source.volume = state.volume
        await interaction.response.send_message(
            f"Громкость: {round(state.volume * 100)}%.", ephemeral=True
        )

    @discord.ui.button(label="📜 Очередь", style=discord.ButtonStyle.secondary, row=1)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = music_states.get(self.guild_id)
        if state is None:
            await interaction.response.send_message("Очередь пуста.", ephemeral=True)
            return
        lines = []
        if state.current:
            lines.append(f"Сейчас играет: **{state.current['title']}**")
        lines.extend(
            f"{index}. {track['title']}"
            for index, track in enumerate(list(state.queue)[:10], start=1)
        )
        await interaction.response.send_message(
            "\n".join(lines) if lines else "Очередь пуста.", ephemeral=True
        )

    @discord.ui.button(label="➕ Добавить в очередь", style=discord.ButtonStyle.success, row=1)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddTrackModal(self.guild_id))


async def play_queue(guild, state):
    try:
        while True:
            if state.skip_requested:
                state.skip_requested = False
                state.current = None
                state.position_offset = 0.0
            elif not state.repeat_current or state.current is None:
                state.current = None
                state.position_offset = 0.0

            if state.current is None:
                while not state.queue:
                    state.queue_ready.clear()
                    await state.queue_ready.wait()
                state.current = state.queue.popleft()

            track = state.current
            voice = guild.voice_client
            if voice is None or not voice.is_connected():
                raise RuntimeError("Бот отключился от голосового канала.")
            state.voice = voice

            while True:
                player = await YTDLSource.from_url(
                    track["url"],
                    start_seconds=state.position_offset,
                    volume=state.volume,
                )
                loop = asyncio.get_running_loop()
                finished = loop.create_future()
                state.started_at = time.monotonic()
                state.paused_at = None
                state.paused_total = 0.0

                def after_play(error):
                    def finish_on_loop():
                        if not finished.done():
                            finished.set_result(error)
                    loop.call_soon_threadsafe(finish_on_loop)

                voice.play(player, after=after_play)
                if state.text_channel is not None:
                    suffix = f" (с {int(state.position_offset)} сек.)" if state.position_offset else ""
                    await state.text_channel.send(
                        f"🎵 Сейчас играет: **{track['title']}**{suffix}",
                        view=MusicControls(guild.id),
                    )
                error = await finished
                state.started_at = None
                state.paused_at = None

                if state.seek_requested:
                    state.position_offset = state.seek_target
                    state.seek_requested = False
                    continue
                if error is not None:
                    print(f"Ошибка воспроизведения: {error}")
                    if state.text_channel is not None:
                        await state.text_channel.send(
                            f"Не удалось воспроизвести **{track['title']}**: {error}"
                        )
                    state.repeat_current = False
                break
    except asyncio.CancelledError:
        raise
    except Exception as error:
        print(f"Ошибка музыкальной очереди: {error}")
        if state.text_channel is not None:
            await state.text_channel.send(f"Ошибка музыкальной очереди: {error}")
    finally:
        state.worker = None


@bot.command(name="play", help="Воспроизводит музыку или добавляет её в очередь")
async def play(ctx, *, url):
    if ctx.guild is None or not isinstance(ctx.author, discord.Member):
        await ctx.send("Эта команда работает только на сервере.")
        return
    if ctx.author.voice is None or ctx.author.voice.channel is None:
        await ctx.send("Ты должен находиться в голосовом канале, чтобы слушать музыку!")
        return

    try:
        async with ctx.typing():
            track, position, state = await enqueue_track(
                url, ctx.author, ctx.guild, ctx.channel
            )
        if position <= 1 and state.current is None:
            message = f"Добавлено в очередь: **{track['title']}**"
        else:
            message = f"Добавлено в очередь №{position}: **{track['title']}**"
        await ctx.send(message, view=MusicControls(ctx.guild.id))
    except Exception as error:
        print(f"Ошибка команды !play: {error}")
        await ctx.send(f"Ошибка при добавлении музыки: {error}")


@bot.command(name="stop", help="Останавливает музыку и отключает бота")
async def stop(ctx):
    if ctx.guild is None:
        await ctx.send("Эта команда работает только на сервере.")
        return

    state = music_states.pop(ctx.guild.id, None)
    voice = ctx.guild.voice_client
    if state is not None and state.worker is not None:
        state.worker.cancel()
    if voice is None:
        await ctx.send("Я не подключён к голосовому каналу.")
        return

    if voice.is_playing() or voice.is_paused():
        voice.stop()
    await voice.disconnect()
    await ctx.send("Очередь очищена, отключаюсь от голосового канала.")


@bot.event
async def on_ready():
    print(f"Музыкальный бот запущен: {bot.user}")


if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("Токен не задан. Запусти start_myzichka.ps1.")
    bot.run(token)