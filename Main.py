import discord, os, logging, asyncio, json, re, sys
from discord.utils import find
from twitchAPI.twitch import Twitch

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

intents = discord.Intents(messages=True, guilds=True, reactions=True)
availableStreams = {}
guildStreams = {}

with open('.env') as f:
    for line in f:
        name = line.split("=")[0]
        value = line.split("=")[1]
        os.environ[name] = value

if os.path.exists('streams.json'):
    with open('streams.json') as f:
        availableStreams = json.loads(f.read())
                
class TwitchBot(discord.Client):
    def __inti__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
    async def on_ready(self):
        logger.debug("================= DISCORD.PY VERISON =================")
        logger.debug(discord.__version__)
        logger.debug("======================================================")
        logger.debug("We have logged in as {0.user}".format(client))
        for guild in self.guilds:
            logger.debug("starting for guild: "+guild.name)
            if guild.id not in guildStreams:
                guildStreams[guild.id] = ''
            mode = 'r'
            if not os.path.exists("% s" % guild.id+'-Stream.json'):
                mode = 'w+'
            if (mode == 'r'):
                with open("% s" % guild.id+'-Stream.json', mode) as Streams:
                    guildStreams[guild.id] = json.loads(Streams.read())
            else:
                with open("% s" % guild.id+'-Stream.json', mode) as Streams:
                    Streams.write('{}')
                    guildStreams[guild.id] = {}
        await self.checkForDailyUpdates()

    async def on_guild_join(self, guild):
        general = find(lambda x: x.name == 'general',  guild.text_channels)
        if guild.id not in guildStreams:
                guildStreams[guild.id] = ''
        if general and general.permissions_for(guild.me).send_messages:
            await general.send('Hello!\n\nI\'m the TwitchBot!\n\nFor more information type "!help"!')
            
    def writeAvailableStreamsToGuildJsonFile(self, guildId):
        with open("% s" % guildId+'-Stream.json', 'w+') as jf:
            jf.write(json.dumps(guildStreams[guildId], indent=4, sort_keys=True))

    def updateEnv(self):
        envOutput = []
        with open('.env', 'w+') as jf:
            for key in os.environ:
                if key == 'TOKEN' or key.startswith('STREAMS-CHANNEL') or key.startswith('TWITCH-APP'):
                    envOutput.append(key+'='+os.getenv(key))
            jf.write(''.join(envOutput))
    
    async def fetchStream(self, username):
        twitch = Twitch(os.getenv('TWITCH-APP-ID').strip(), os.getenv('TWITCH-APP-SECRET').strip())
        streamsinfo = twitch.get_streams(user_login=username)
        if not streamsinfo.get('data'):
            streamsinfo = twitch.get_users(logins=username)
        return streamsinfo

    async def on_message(self, message):
        
        if message.author == client.user or message.guild == None:
            return
            
        incomingGuildId = message.guild.id
        incomingChannelId = message.channel.id
        incomingChannelName = message.channel
        outgoingChannelId = os.getenv('STREAMS-CHANNEL-'+"% s" % incomingGuildId)
        try:
            if outgoingChannelId == None or incomingChannelId == int(outgoingChannelId):
                if message.content.startswith("!scc") and message.author.guild_permissions.administrator:
                    if(outgoingChannelId != None):
                        StreamsChannel = self.get_channel(int(outgoingChannelId))
                        await StreamsChannel.send('Channel '+"% s" % StreamsChannel+' is registered as streams channel for current server!')
                    else:
                        await message.channel.send('No channel has been set as dedicated streams channel!')
                        
                if message.content.startswith("!sbc") and message.author.guild_permissions.administrator:
                    if incomingGuildId not in guildStreams:
                        guildStreams[incomingGuildId] = ''
                    if(outgoingChannelId != None):
                        channel = self.get_channel(int(incomingChannelId))
                        streamsChannel = self.get_channel(int(outgoingChannelId))
                        await channel.send('Channel '+"% s" % streamsChannel+' is already registered as streams channel for current server!')
                    else :
                        os.environ['STREAMS-CHANNEL-'+"% s" % incomingGuildId] = "% s" % incomingChannelId
                        self.updateEnv()
                        channel = self.get_channel(int(incomingChannelId))
                        self.writeAvailableStreamsToGuildJsonFile(incomingGuildId)
                        await channel.send('Setting up this channel ('+"% s" % incomingChannelName+') as dedicated streams channel!\nThe bot will no longer listen to commands in other channels!')

                if message.content.startswith("!dbc") and message.author.guild_permissions.administrator:
                    channel = self.get_channel(int(incomingChannelId))
                    if(outgoingChannelId == None):
                        await channel.send('Channel doesn\'t exists in registry!')
                    elif (int(incomingChannelId) != int(outgoingChannelId)): 
                        await channel.send('Channel '+"% s" % incomingChannelName+' is not registered as streams channel for current server!')
                        streamsChannel = self.get_channel(int(outgoingChannelId))
                        await channel.send('Channel '+"% s" % streamsChannel+' is registered as streams channel for current server!')
                    else :
                        del os.environ['STREAMS-CHANNEL-'+"% s" % incomingGuildId]
                        self.updateEnv()
                        await channel.send('Channel '+"% s" % incomingChannelName+' has been removed from registry!')
                        await channel.send('To register a channel please type "!sbc" in the desired channel!')
                
                if message.content.startswith('!restart-bot') and message.author.display_name == 'dxcufgb':
                    channel = self.get_channel(int(outgoingChannelId))
                    await channel.send('Restarting...')
                    os.execv(sys.executable, ['python'] + sys.argv)
                
                if (outgoingChannelId == None):
                    channel = self.get_channel(int(incomingChannelId))
                else:
                    channel = self.get_channel(int(outgoingChannelId))
                    
                if message.content.startswith('!add-stream'):
                    exists = False
                    existingName = ''
                    Stream = {}
                    innerValues = {}
                    newStream = message.content[12:]
                    gameName = ''
                    title = ''
                    URL = 'https://www.twitch.tv/'+newStream
                    role = ''
                    newStreamData = await self.fetchStream(newStream)
                    if newStreamData.get('data'):
                        logger.debug(newStreamData.get('data')[0])
                        if 'game_name' in newStreamData.get('data')[0]:
                            gameName = newStreamData.get('data')[0]['game_name']
                        if 'title' in newStreamData.get('data')[0]:
                            title = newStreamData.get('data')[0]['title']
                        innerValues['LastStatus'] = ''
                        for Stream in availableStreams:
                            if availableStreams[Stream]['URL'] == URL:
                                exists = True
                                existingName = Stream
                                if 'Role' not in availableStreams[Stream]:
                                    newRole = 'TW-'+Stream+'-Role'
                                    availableStreams[Stream]['Role'] = newRole
                                    role = newRole
                                    with open('Streams.json', 'w+') as jf:
                                        jf.write(json.dumps(availableStreams, indent=4, sort_keys=True))
                                
                        if not exists:
                            guildStreams[incomingGuildId][newStream] = innerValues
                            self.writeAvailableStreamsToGuildJsonFile(incomingGuildId)
                            innerValues['URL'] = URL
                            innerValues['game_name'] = gameName
                            innerValues['title'] = title
                            innerValues['Role'] = 'TW-'+newStream+'-Role'
                            role = 'TW-'+newStream+'-Role'
                            availableStreams[newStream] = innerValues
                            guild = message.guild
                            await guild.create_role(name=role)
                            author = message.author
                            role = discord.utils.get(message.guild.roles, name=role)
                            await author.add_roles(role)
                            with open('Streams.json', 'w+') as jf:
                                jf.write(json.dumps(availableStreams, indent=4, sort_keys=True))
                        else: 
                            guildStreams[incomingGuildId][existingName] = innerValues
                            self.writeAvailableStreamsToGuildJsonFile(incomingGuildId)
                            
                        await channel.send('Stream "'+ newStream + '" added!')
                        await channel.send('Role "'+availableStreams[newStream]['Role']+'" will be used for notifications!')
                        if newStreamData.get('data')[0]['description'] != '' and re.sub(r'.*(?:https?:\/\/)?discord(.*(gg|com|app\.com))\S?([A-Za-z0-9]+){7}', '', newStreamData.get('data')[0]['description'], re.DOTALL) != '':
                            await channel.send('Description: '+re.sub(r'.*(?:https?:\/\/)?discord(.*(gg|com|app\.com))\S?([A-Za-z0-9]+){7}', '', newStreamData.get('data')[0]['description']))
                    else:
                        await channel.send('Stream "'+ newStream + '" could not be found!')
                    
                if message.content.startswith('!filter-stream'):
                    inputMessage = message.content[15:]
                    streamName = inputMessage[0:inputMessage.find(' ')]
                    if "filter" in guildStreams[incomingGuildId][streamName]:
                        streamFilter = guildStreams[incomingGuildId][streamName]["filter"]
                        
                    else :
                        streamFilter = []
                    streamFilter.append(inputMessage[inputMessage.find('--filter ')+9:])
                    guildStreams[incomingGuildId][streamName]["filter"] = streamFilter
                    self.writeAvailableStreamsToGuildJsonFile(incomingGuildId)
                    filterText = ''
                    for filter in streamFilter:
                        filterText = '"'+filter+'", '
                    filterText = filterText[0:-2]
                    await channel.send('Stream with key '+streamName+' will filter for streams containing the text '+filterText+'')
                
                if message.content.startswith('!clear-filters-from-stream'):
                    streamName = message.content[message.content.find(' ')+1:]
                    del guildStreams[incomingGuildId][streamName]["filter"]
                    self.writeAvailableStreamsToGuildJsonFile(incomingGuildId)
                    await channel.send('Cleared filters from stream with key '+streamName)
                    
                if message.content.startswith('!active-filters'):
                    text = ''
                    for Stream in guildStreams[incomingGuildId]:
                        if "filter" in guildStreams[incomingGuildId][Stream]:
                            for streamFilter in guildStreams[incomingGuildId][Stream]["filter"]:
                                text += Stream+': '+availableStreams[Stream]['URL']+' is filtered by '+streamFilter+'\n'
                    await channel.send(text)
                    
                if message.content.startswith('!remove-stream'):
                    inputMessage = message.content[15:]
                    del guildStreams[incomingGuildId][inputMessage]
                    self.writeAvailableStreamsToGuildJsonFile(incomingGuildId)
                    await channel.send('Stream with key '+inputMessage+' removed')
                        
                if message.content.startswith('!active-streams'):
                    text = ''
                    for Stream in guildStreams[incomingGuildId]:
                        text += Stream+': '+availableStreams[Stream]['URL']+' Role: '+availableStreams[Stream]['Role']+'\n'
                    await channel.send(text)
                
                if message.content.startswith('!available-streams'):
                    text = ''
                    for Stream in availableStreams:
                        text += Stream+': '+availableStreams[Stream]['URL']+' Role: '+availableStreams[Stream]['Role']+'\n'
                    await channel.send(text)
                    
                if message.content.startswith('!add-role'):
                    roleName = message.content[message.content.find(' ')+1:]
                    if roleName.startswith('TW-'):
                        author = message.author
                        try:
                            role = discord.utils.get(message.guild.roles, name=roleName)
                            await author.add_roles(role)
                            await channel.send('User "'+author.display_name+'" added to role "'+roleName+'"!')
                        except:
                            await channel.send('Something went wrong, does the role exist?\n\nDid you spell it wrong')
                    else:
                        await channel.send('Command only works with twitch-stream subscription roles!\n\nthey always start with "TW-"')
                       
                if message.content.startswith('!remove-role'):
                    roleName = message.content[message.content.find(' ')+1:]
                    author = message.author
                    try:
                        role = discord.utils.get(message.guild.roles, name=roleName)
                        await author.remove_roles(role)
                        await channel.send('User "'+author.display_name+'" removed from role "'+roleName+'"!')
                    except:
                       await channel.send('Something went wrong, does the role exist?\n\nDid you spell it wrong')

                if message.content.startswith('!help'):
                    text = '\n==============================ADMIN ONLY==============================\n'
                    text += '!sbc\n'
                    text += 'Set Bot Channel: assigns bot to current channel!\n'
                    text += 'All streams will be posted in this channel!\n'
                    text += 'The bot will stop listening to other channels on this server!\n'
                    text += '\n'
                    text += '!scc\n'
                    text += 'Show Current Channel: Shows current channel used by the bot!\n'
                    text += '\n'
                    text += '!dbc\n'
                    text += 'Delete Bot Channel: Deletes the assigned channel so the bot can be moved!\n'
                    text += 'The bot will start listening to other channels again!\n'
                    text += '\n'
                    text += '==============================EVERYONE==============================\n'
                    text += '!add-stream --name [NAME]\n'
                    text += 'Add a Stream with assigned name and point to URL\n'
                    text += '\n'
                    text += '!active-streams\n'
                    text += 'List active streams name and URL for this server\n'
                    text += '\n'
                    text += '!available-streams\n'
                    text += 'List available streams name and URL for the bot\n'
                    text += '\n'
                    text += '!remove-stream [NAME]\n'
                    text += 'Remove Stream with the assigned name, use "!active-streams" to list streams name and URLs\n'
                    text += '\n'
                    text += '!filter-stream [NAME] --filter [FILTER]\n'
                    text += 'Filter Streams with the assigned filter, Only streams containing the --filter [STRING] will be sent as alerts!\n'
                    text += '\n'
                    text += '!active-filters\n'
                    text += 'List active filters on stream name and URL for this server\n'
                    text += '\n'
                    text += '!add-role [ROLE_NAME]\n'
                    text += 'add yourself to a role on the server for subscription to certain streams!\n'
                    text += '\n'
                    text += '!remove-role [ROLE_NAME]\n'
                    text += 'Remove yourself from a role on the server!\n'
                    text += '\n'
                    text += '!help\n'
                    text += 'shows this help section!\n'
                    text += '\n'
                    text += '\n'
                    text += 'Created by Dxcufgb - 2022'
                    await channel.send(text)
        except:
            StreamsChannel = self.get_channel(int(outgoingChannelId))
            await StreamsChannel.send('Something went wrong!\n\nUse "!help" for help with commands!\n\nIf you were trying to modify or remove a stream or stream filters, make sure the stream key was correct!')
    
    async def checkForDailyUpdates(self):
        await self.wait_until_ready()
        while not self.is_closed():
            logger.debug('Fetching streams status!')
            for Stream in availableStreams:
                logger.debug('Fetching '+Stream+'!')
                fetchedStatus = await self.fetchStream(Stream)
                availableStreams[Stream]['LastStatus'] = fetchedStatus.get('data')[0]['type']
                gameName = ''
                title = ''
                if 'game_name' in fetchedStatus.get('data')[0]:
                    gameName = fetchedStatus.get('data')[0]['game_name']
                if 'title' in fetchedStatus.get('data')[0]:
                    title = fetchedStatus.get('data')[0]['title']
                availableStreams[Stream]['game_name'] = gameName
                availableStreams[Stream]['title'] = title
                with open('Streams.json', 'w+') as jf:
                    jf.write(json.dumps(availableStreams, indent=4, sort_keys=True))
            await self.sendToAllGuilds()
            logger.debug('Idling for 60 seconds...')
            await asyncio.sleep(60)

    async def sendToAllGuilds(self):
        for stream in availableStreams:
            logger.debug(availableStreams[stream]['LastStatus'])
            for guild in self.guilds:
                if availableStreams[stream]['LastStatus'] == 'live' and stream in guildStreams[guild.id]:
                    logger.debug('running task for guild: '+"% s" % guild.id)
                    channel = self.get_channel(int(os.getenv('STREAMS-CHANNEL-'+"% s" % guild.id).strip()))
                    if guildStreams[guild.id][stream]['LastStatus'] != 'live' and ("filter" not in guildStreams[guild.id][stream] or any(filter in availableStreams[stream]['title'] for filter in guildStreams[guild.id][stream]["filter"])):
                        guildStreams[guild.id][stream]['LastStatus'] = 'live'
                        role = discord.utils.get(guild.roles, name=availableStreams[stream]['Role'])
                        try:
                            await channel.send(role.mention+' '+stream+' went live, streaming '+availableStreams[stream]['game_name']+'!\n\n'+availableStreams[stream]['title']+'\n\n'+ availableStreams[stream]['URL'])
                        except:
                            await channel.send(availableStreams[stream]['Role']+' missing!')
                            await channel.send('@here '+stream+' went live, streaming '+availableStreams[stream]['game_name']+'!\n\n'+availableStreams[stream]['title']+'\n\n'+ availableStreams[stream]['URL'])
                        self.writeAvailableStreamsToGuildJsonFile(guild.id)
                else:
                    if stream in guildStreams[guild.id]:
                        guildStreams[guild.id][stream]['LastStatus'] = ''
                        self.writeAvailableStreamsToGuildJsonFile(guild.id)
        
client = TwitchBot()
client.run(os.getenv('TOKEN'))