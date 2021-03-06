import discord
import random
import asyncio
import lib.dynamodb_handler as dynamo
import lib.utils as utils
import lib.constants as const

pokedex_load_bulk_num = 0
pokedex_load_bulk_stop = 0
previous_command = None
poke_loader = None
upload_failures = []
sleep_time = 2

class PokeLoader:
    def __init__(self, name):
        self.name = name
        self.all_variants = []
        for v in const.POKEMON_VARIANTS:
            self.all_variants.append(v)
            self.all_variants.append(f'Shiny {v}')

    def get_next_pokemon(self):
        return f'{self.all_variants.pop()} {self.name}' if self.all_variants else None

class PokeInfo:
    def __init__(self, dex_num, name, url, img_hash, is_shiny, variant):
        self.dex_num = dex_num
        self.name = name
        self.url = url
        self.img_hash = img_hash
        self.is_shiny = is_shiny
        self.variant = variant

# Scrape information from dex info command
# Format of title: #<dex-num> - <variant?> <name> <x/y?> <shiny?>
# Possibilities:
# 1    #<dex-num> - <name>
# 2    #<dex-num> - <name> <shiny>
# 3    #<dex-num> - <variant> <name>
# 4    #<dex-num> - <variant> <name> <shiny>
# 5    #<dex-num> - <mega> <name> <x/y>
# 6    #<dex-num> - <mega> <name> <x/y> <shiny>
def scrape_dex_info(e):
    deets = e.title.split()
    is_shiny = False
    variant = ''
    
    name = None
    if len(deets) == 3:
        name = deets[2]
    elif len(deets) == 4:
        if deets[3] == const.SHINY_SYMBOL:
            name = deets[2]
            is_shiny = True
        else :
            variant = deets[2]
            name = deets[3]

    elif len(deets) == 5:
        if deets[4] == const.SHINY_SYMBOL:
            variant = deets[2]
            name = deets[3]
            is_shiny = True
        else: # poke with 2 megas
            variant = deets[2]
            name = f'{deets[3]} {deets[4]}'

    elif len(deets) == 6:
        variant = deets[2]
        name = f'{deets[3]} {deets[4]}'
        is_shiny = True

    return PokeInfo(deets[0][1:], name, e.image.url, utils.get_img_hash(e.image.url), is_shiny, variant)

def set_bulk_load_params(start, end):
    global pokedex_load_bulk_num, pokedex_load_bulk_stop
    pokedex_load_bulk_num = start
    pokedex_load_bulk_stop = end

async def handle_bulk_loading():
    global poke_loader, pokedex_load_bulk_num, pokedex_load_bulk_stop, previous_command, upload_failures

    next_variant = poke_loader.get_next_pokemon()
    msg = None
    # completely done
    if next_variant is None and pokedex_load_bulk_num == pokedex_load_bulk_stop:
        msg = f'Finished bulk loading Quack! Failures: {upload_failures}. Variants loaded: {const.POKEMON_VARIANTS}'
        print(f'loading failures: {upload_failures}')
        poke_loader = None
        previous_command = None
        upload_failures = []
        pokedex_load_bulk_num = 0
        pokedex_load_bulk_stop = 0

    # Done with this pokemon. Move on to the next
    elif next_variant is None:
        pokedex_load_bulk_num += 1
        poke_loader = None
        msg = f'.pokedex #{pokedex_load_bulk_num}'
    # Still more variants of current pokemon to go through
    else:
        msg = f'.pokedex {next_variant}'

    previous_command = msg
    # sleep for a bit so discord does not time us out
    await asyncio.sleep(sleep_time)
    return msg

# Handle all incoming messages pertaining to pokecord
async def handle_pokecord(message, pal, upload_to_dynamo, catch_sleep_time):
    global poke_loader

    if not pokedex_load_bulk_stop == 0 and not message.embeds:
        print(message.content)
        # Not a valid pokemon variant so lets move on   
        if 'That pokémon doesn\'t seem to exist... Maybe you spelled it wrong?' in message.content:
            msg = handle_bulk_loading()
            await message.channel.send(msg)
        # We got throttled by pokecord. Waiting a bit and then starting from previous
        elif'You seem to be sending commands too fast' in message.content:
            await asyncio.sleep(5)
            await message.channel.send(previous_command)

    if not message.embeds:
        return

    e = message.embeds[0]
    print(f'title: {e.title}')
    print(f'image url: {e.image.url}')

    if '#' in e.title and '-' in e.title:
        p_info = scrape_dex_info(e)
        
        if poke_loader is None and not pokedex_load_bulk_stop == 0:
            poke_loader = PokeLoader(p_info.name)
        
        print(p_info.img_hash)

        s_name = p_info.name
        if p_info.is_shiny:
            s_name = f'Shiny {p_info.variant} {p_info.name}'
        else:
            s_name = f'{p_info.variant} {p_info.name}'

        if poke_loader is None:
            await message.channel.send(f'The displayed Pokémon is a(n) {s_name} with National Dex entry: {p_info.dex_num}.\nHash: {p_info.img_hash}')

        if upload_to_dynamo:
            is_success = dynamo.upload_to_poke_table(p_info)
            if not pokedex_load_bulk_stop == 0:
                if not is_success:
                    upload_failures.append(p_info)
                msg = handle_bulk_loading()
                await message.channel.send(msg)
            else:
                if not is_success:
                    print(f'Failed to upload {s_name} with National Dex entry: {p_info.dex_num}.\nHash: {p_info.img_hash}')
                    await message.channel.send(f'Failed to upload {s_name} with National Dex entry: {p_info.dex_num}.\nHash: {p_info.img_hash}')
                else:
                    print(f'Uploaded {s_name} with National Dex entry: {p_info.dex_num}.\nHash: {p_info.img_hash}')


    elif 'Level' in e.title:
        img_hash = utils.get_img_hash(e.image.url)
        print(img_hash)
        msg = None
        found, name = dynamo.try_retrieve_pokemon(img_hash)
        if found:
            msg = f'Nice {name}! Quack!\nHash: {img_hash}'
        else:
            msg =  f'Who\'s that Pokémon? Quack\nHash: {img_hash}'

        await message.channel.send(msg)

    elif 'A wild pokémon has аppeаred!' in e.title:
        img_hash = utils.get_img_hash(e.image.url)
        print(img_hash)
        msg = None
        if pal == utils.PokeAssist.none:
            msg = f'A wild Pokémon... Quack. What could it be?\nHash: {img_hash}'
        else:
            found, name = dynamo.try_retrieve_pokemon(img_hash)  
            if found:
                if pal == utils.PokeAssist.assist:
                    msg = f'Quack Quack! A wild {name} appeared!'
                else: # catch mode
                    await asyncio.sleep(catch_sleep_time) # wait a period before catching
                    msg = f'.catch {name}'
            else:
                msg = f'A wild Pokémon I don\'t recognize Quack... can\'t {pal.name}...\nHash: {img_hash}'

        await message.channel.send(msg)

