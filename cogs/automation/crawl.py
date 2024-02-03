import discord
from discord import app_commands
from discord.ext import tasks, commands
from utils.asset import Assets
from utils.lc_utils import LC_utils
from cogs.cmd_interface.task import Task
import os
import asyncio
import traceback
from database_api_layer.api import DatabaseAPILayer

class Crawl(commands.Cog):
    def __init__(self, client):
        self.client = client
        if os.getenv('START_UP_TASKS') == "True":
            self.crawling.start()
        self.db_api = DatabaseAPILayer(client)

    def cog_unload(self):
        self.crawling.cancel()
    
    async def submissions(self):
        leaderboard = self.db_api.read_current_month_leaderboard()
        for user in leaderboard:
            username = user['leetcodeUsername']
            recent_solved = []
            recent_info = LC_utils.get_recent_ac(username, 20)
            if (recent_info == None):
                continue

            for submission in recent_info:
                problem = self.db_api.read_problem_from_slug(submission['titleSlug'])
                await self.db_api.register_new_submission(user['userId'], problem['id'] ,submission['id'])

    @tasks.loop(seconds = 10) # this interval is like 100x shorter than the process of crawling data of everyone now q:
    async def crawling(self):
        await self.submissions()

    @crawling.error
    async def on_error(self, exception):
        guild = await self.client.fetch_guild(1085444549125611530)
        channel = await guild.fetch_channel(1091763595777409025)
        await channel.send(f"Crawling error```py\n{exception}```")

        self.crawling.restart()

    @commands.command()
    @commands.has_permissions(administrator = True)
    async def stop_crawling(self, ctx):
        self.crawling.cancel()
        await ctx.send(f"{Assets.green_tick} **Submission crawling task stopped.**")

    @commands.command()
    @commands.has_permissions(administrator = True)
    async def start_crawling(self, ctx):
        self.crawling.start()
        await ctx.send(f"{Assets.green_tick} **Submission crawling task started.**")

async def setup(client):
    await client.add_cog(Crawl(client), guilds=[discord.Object(id=1085444549125611530)])
