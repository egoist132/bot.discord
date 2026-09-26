import discord
from discord.ext import commands
import random

intents = discord.Intents.default()
intents.members = True  # Нужно, чтобы бот видел участников сервера

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Logged in as {bot.user}')

@bot.tree.command(name="кто_хуесос", description="Выбирает случайного хуесоса с сервера")
async def кто_хуесос(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Эта команда работает только на сервере.", ephemeral=True)
        return
    
    members = [member for member in guild.members if not member.bot]
    if not members:
        await interaction.response.send_message("На сервере нет участников для выбора.", ephemeral=True)
        return
    
    chosen = random.choice(members)
    await interaction.response.send_message(f"Хуесос сегодня - {chosen.mention} 😆")

bot.run('MTU1MzA5NzU0NjAwMjQ3MzAzMQ.GjDdrg.j-Fei9P7xj6cCtGUe6ifav98hbLhKyrS2UTa34')