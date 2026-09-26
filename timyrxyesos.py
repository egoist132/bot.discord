import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import discord
from discord.ext import commands
import edge_tts
from edge_tts.exceptions import NoAudioReceived


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


class VoiceBot(commands.Bot):
    async def setup_hook(self):
        await self.tree.sync()


intents = discord.Intents.default()
bot = VoiceBot(command_prefix="!", intents=intents)


@bot.tree.command(
    name="кто_хуесос",
    description="Введите имя, и бот назовёт его хуесосом в голосовом канале",
)
@discord.app_commands.describe(имя="Введите имя, которое бот произнесёт")
async def say_phrase(interaction: discord.Interaction, имя: str):
    if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
        await interaction.response.send_message(
            "Эта команда работает только на сервере.", ephemeral=True
        )
        return

    if interaction.user.voice is None or interaction.user.voice.channel is None:
        await interaction.response.send_message(
            "Сначала зайдите в голосовой канал.", ephemeral=True
        )
        return

    имя = " ".join(имя.split())
    if not имя or len(имя) > 80:
        await interaction.response.send_message("Введите имя длиной до 80 символов.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    audio_path = None
    voice = None

    try:
        file_descriptor, audio_path = tempfile.mkstemp(suffix=".mp3")
        os.close(file_descriptor)
        for attempt in range(3):
            communicate = edge_tts.Communicate(f"{имя}, хуесос", "ru-RU-DmitryNeural")
            try:
                await communicate.save(audio_path)
                break
            except NoAudioReceived:
                if attempt == 2:
                    raise
                await asyncio.sleep(attempt + 1)

        voice = interaction.guild.voice_client
        if voice is None:
            voice = await interaction.user.voice.channel.connect()
        elif voice.channel != interaction.user.voice.channel:
            await voice.move_to(interaction.user.voice.channel)

        if voice.is_playing():
            voice.stop()

        loop = asyncio.get_running_loop()
        finished = loop.create_future()

        def after_play(error):
            loop.call_soon_threadsafe(finished.set_result, error)

        voice.play(discord.FFmpegPCMAudio(audio_path, executable=find_ffmpeg()), after=after_play)
        error = await finished
        if error is not None:
            raise error

        await interaction.followup.send(f"Сказал: {имя}, хуесос.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as error:
        print(f"Ошибка голосовой команды: {error}")
        await interaction.followup.send(
            "Не получилось воспроизвести фразу. Проверьте подключение к интернету и установку edge-tts/FFmpeg.",
            ephemeral=True,
        )
    finally:
        if voice is not None and voice.is_connected():
            await voice.disconnect(force=True)
        if audio_path is not None and os.path.exists(audio_path):
            os.remove(audio_path)


if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("Токен не задан. Запустите бота через start_bot.ps1.")
    bot.run(token)