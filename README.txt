
FILE STORE BOT - QUICK SETUP

1. Install Python 3.11+.
2. Open CMD in this folder.
3. Create a virtual environment:
   py -m venv venv
4. Activate it:
   venv\Scripts\activate
5. Install packages:
   pip install -r requirements.txt
6. Copy .env.example to .env and put your values there.

IMPORTANT:
This simple version reads environment variables directly. CMD setup below is easiest.

SET BOT TOKEN:
   set BOT_TOKEN=YOUR_BOT_TOKEN
   set ADMIN_ID=YOUR_TELEGRAM_ID
   set BASE_URL=https://YOUR_PUBLIC_HTTPS_URL
   set PORT=8080

Then run:
   py bot.py

TELEGRAM COMMANDS:
   /start
   /id
   /genlink
   /batchlink
   /done
   /users
   /stats
   /broadcast

HOW TO MAKE A FILE LINK:
- Send a file to the bot.
- Reply to the file message with /genlink.
- The bot returns BASE_URL/f/TOKEN.

BATCH:
- Send /batchlink
- Send multiple files
- Send /done
- Bot returns one batch URL.

PUBLIC URL:
The bot must have a public HTTPS BASE_URL for links to work outside your PC.
If you run it only on your computer, localhost links will not work for other people.
Use a public HTTPS tunnel/host and set BASE_URL to that address.
