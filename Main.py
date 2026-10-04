"""Entry point: ``python Main.py`` (or use start-bot.sh / start-bot.cmd).

Options: ``--check`` verifies .env and the Discord/Twitch credentials, ``--version`` prints the version.
"""

import sys

from twitchbot.bot import main

if __name__ == "__main__":
    sys.exit(main())
