# Discord-Bot-PC-RC
A Discord bot that lets you check on your PC from anywhere.
**SLIGHTLY AI ASSISTED**

## Commands
- `/status`: online check, uptime, CPU, RAM, GPU (with temperature), disks
- `/screenshot [monitor]`: 1 = main screen (default), 2 = second screen, 0 = all screens
- `/open <app>`: open any app from your Start menu (autocompletes)
- `/kill <app>`: close a running app, with a confirm button (Windows system processes are blocked)
- `/processes [sort_by] [count]`: top apps by RAM or CPU
- `/focus <window> [screenshot]`: bring an open window to the front, with an optional screenshot after

Only your Discord account (`OWNER_ID`) can use the commands. Replies are only visible to you. Every command is logged in `bot.log`.

## Setup
1. Go to https://discord.com/developers/applications, click **New Application**, and name it.
2. Open **Bot**, click **Reset Token**, and copy the token.
3. Under **Installation**, tick **Guild Install** and **User Install**, and add the `applications.commands` and `bot` scopes. Copy the install link and open it to add the bot.
4. In Discord, go to Settings → Advanced and turn on **Developer Mode**. Then right-click your name and choose **Copy User ID**.
5. Copy `.env.example` to `.env` and fill in:
   - `DISCORD_TOKEN`: the token from step 2
   - `OWNER_ID`: your user ID from step 4
   - `GUILD_ID` (optional): your server's ID. Commands show up instantly there. Without it, they can take a few minutes to appear.
6. Double-click `start.bat`.

Never share `.env`, because anyone with the token can control the bot.

## Requirements
discord.py>=2.4
psutil
mss
Pillow
python-dotenv
