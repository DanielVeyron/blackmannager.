
import re
import time
import urllib
import random
import sys
import asyncio
from datetime import datetime, timedelta

from vkbottle.bot import Bot, Message, rules
from vkbottle import Keyboard, Callback, KeyboardButtonColor, Text, GroupEventType, GroupTypes, User
import json
import sqlite3

with open("config.json", "r") as js:
    open_file = json.load(js)

bot = Bot(token=open_file['bot-token'])

ACTIVATION_OWNER_ID = int(open_file.get('owner_id', 1070063849))
ACTIVATION_DENY_TEXT = (
    "Бота может запустить только Специальный руководитель.\n"
    "https://vk.com/id1070063849\n"
    "Чтобы его запустить, добавьте меня в беседу, выдайте боту звезду, а я запущу."
)

async def get_logic(number = int):
    if number < 1: return False
    else: return True

async def getID(arg=str):
    arg = str(arg).strip()
    if not arg:
        return False

    mention = re.search(r'\[(id|club)(-?\d+)\|', arg)
    if mention:
        value = int(mention.group(2))
        return -value if mention.group(1) == "club" else value

    arg = arg.split("?")[0].split("#")[0].rstrip("/")
    if arg.startswith("@"):
        arg = arg[1:]

    link = re.search(r'(?:https?://)?(?:m\.)?vk\.(?:com|ru)/([^/?#]+)', arg, re.IGNORECASE)
    if link:
        arg = link.group(1)

    if re.fullmatch(r'id\d+', arg, re.IGNORECASE):
        return int(arg[2:])
    if re.fullmatch(r'club\d+', arg, re.IGNORECASE):
        return -int(arg[4:])
    if arg.isnumeric():
        return int(arg)

    try:
        scr_split = await bot.api.utils.resolve_screen_name(arg)
        x = json.loads(scr_split.json())
        object_id = int(x['object_id'])
        if x.get('type') in ['group', 'page', 'application']:
            return -object_id
        return object_id
    except:
        return False

async def can_activate_bot(user_id: int) -> bool:
    if ACTIVATION_OWNER_ID and ACTIVATION_OWNER_ID > 0:
        return int(user_id) == ACTIVATION_OWNER_ID
    return False

async def get_registration_date(user_id=int):
    try:
        vk_link = f"http://vk.com/foaf.php?id={user_id}"
        with urllib.request.urlopen(vk_link) as response:
            vk_xml = response.read().decode("windows-1251")

        parsed_xml = re.findall(r'created dc:date="(.*)"', vk_xml)
        if not parsed_xml:
            return "Не удалось определить"

        item = parsed_xml[0]
        sp_i = item.split('+')
        date_str = sp_i[0]

        PATTERN_IN = "%Y-%m-%dT%H:%M:%S"
        date_obj = datetime.strptime(date_str, PATTERN_IN)

        month_name_en = date_obj.strftime("%B")
        locales = {
            "November": "ноября", "October": "октября", "September": "сентября",
            "August": "августа", "July": "июля", "June": "июня", "May": "мая",
            "April": "апреля", "March": "марта", "February": "февраля",
            "January": "января", "December": "декабря"
        }
        month_name_ru = locales.get(month_name_en)

        return date_obj.strftime(f"%d-ого {month_name_ru} %Yг")

    except Exception:
        return "Не удалось определить"

async def get_string(text=[], arg=int):
    data_string = []
    for i in range(len(text)):
        if i < arg: pass
        else: data_string.append(text[i])
    return_string = " ".join(data_string)
    if return_string == "": return False
    else: return return_string

database = sqlite3.connect('database.db')
sql = database.cursor()

sql.execute("CREATE TABLE IF NOT EXISTS chats (chat_id BIGINT PRIMARY KEY, peer_id BIGINT, owner_id BIGINT, welcome TEXT, invite_kick INTEGER, leave_kick INTEGER, in_pull INTEGER, silence INTEGER, filter INTEGER, antiflood INTEGER);")
sql.execute("CREATE TABLE IF NOT EXISTS global_managers (user_id BIGINT PRIMARY KEY, level INTEGER);")
sql.execute("CREATE TABLE IF NOT EXISTS chat_roles (chat_id BIGINT, name TEXT, priority INTEGER, PRIMARY KEY(chat_id, name));")
sql.execute("CREATE TABLE IF NOT EXISTS user_roles (chat_id BIGINT, user_id BIGINT, role_name TEXT, PRIMARY KEY(chat_id, user_id));")
sql.execute("CREATE TABLE IF NOT EXISTS command_perms (chat_id BIGINT, command TEXT, priority INTEGER, PRIMARY KEY(chat_id, command));")
sql.execute("CREATE TABLE IF NOT EXISTS user_commands (chat_id BIGINT, user_id BIGINT, command TEXT, PRIMARY KEY(chat_id, user_id, command));")
sql.execute("CREATE TABLE IF NOT EXISTS forms (chat_id BIGINT PRIMARY KEY, title TEXT, questions TEXT, is_open INTEGER DEFAULT 0, target_peer_id BIGINT);")
sql.execute("CREATE TABLE IF NOT EXISTS applications (app_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id BIGINT, user_id BIGINT, answers TEXT, status TEXT DEFAULT 'pending', date TEXT);")
# Вызовы на дуэль храним в базе, а не в оперативной памяти — иначе после
# перезапуска процесса (падение/переподключение к VK) все ожидающие
# подтверждения дуэли просто исчезали бы.
sql.execute("CREATE TABLE IF NOT EXISTS pending_duels (chat_id BIGINT, target_id BIGINT, challenger_id BIGINT, amount BIGINT, created_at REAL, PRIMARY KEY(chat_id, target_id));")
database.commit()

user_states = {}
form_states = {}
processed_command_messages = {}
DUEL_TIMEOUT_SECONDS = 300

async def get_pending_duel(chat_id: int, target_id: int):
    sql.execute("SELECT challenger_id, amount, created_at FROM pending_duels WHERE chat_id = ? AND target_id = ?", (chat_id, target_id))
    fetch = sql.fetchone()
    if not fetch:
        return None
    return {'challenger': fetch[0], 'amount': fetch[1], 'created_at': fetch[2]}

async def set_pending_duel(chat_id: int, target_id: int, challenger_id: int, amount: int):
    sql.execute(
        "INSERT OR REPLACE INTO pending_duels (chat_id, target_id, challenger_id, amount, created_at) VALUES (?, ?, ?, ?, ?)",
        (chat_id, target_id, challenger_id, amount, time.time())
    )
    database.commit()

async def delete_pending_duel(chat_id: int, target_id: int):
    sql.execute("DELETE FROM pending_duels WHERE chat_id = ? AND target_id = ?", (chat_id, target_id))
    database.commit()

async def get_priority(user_id, chat_id):
    if int(user_id) == ACTIVATION_OWNER_ID:
        return 999

    sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch and fetch[0] == 4: return 500
    if fetch and fetch[0] == 3: return 400
    if fetch and fetch[0] == 2: return 200
    
    sql.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch and fetch[0] == user_id: return 100
    
    sql.execute("SELECT r.priority, r.name FROM user_roles ur JOIN chat_roles r ON ur.chat_id = r.chat_id AND ur.role_name = r.name WHERE ur.chat_id = ? AND ur.user_id = ?", (chat_id, user_id))
    fetch = sql.fetchone()
    if fetch: return fetch[0]
    
    sql.execute(f"SELECT level FROM permissions_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch:
        levels = {0:0, 1:21, 2:41, 3:51, 4:61}
        return levels.get(fetch[0], 0)
    return 0

_priority_to_level = {0: 0, 21: 1, 41: 2, 51: 3, 61: 4, 80: 5, 100: 6, 200: 6}

async def get_role(user_id: int, chat_id: int) -> int:
    if int(user_id) == ACTIVATION_OWNER_ID:
        return 50

    sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch and fetch[0] == 4:
        return 10
    if fetch and fetch[0] == 3:
        return 9
    if fetch and fetch[0] == 2:
        return 6

    sql.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch and fetch[0] == user_id:
        return 7

    sql.execute("SELECT r.priority FROM user_roles ur JOIN chat_roles r ON ur.chat_id = r.chat_id AND ur.role_name = r.name WHERE ur.chat_id = ? AND ur.user_id = ?", (chat_id, user_id))
    fetch = sql.fetchone()
    if fetch:
        priority = fetch[0]
        if priority >= 100:
            return 6
        if priority >= 80:
            return 5
        if priority >= 61:
            return 4
        if priority >= 51:
            return 3
        if priority >= 41:
            return 2
        if priority >= 21:
            return 1
        return 0

    try:
        sql.execute(f"SELECT level FROM permissions_{chat_id} WHERE user_id = ?", (user_id,))
        fetch = sql.fetchone()
    except sqlite3.OperationalError:
        fetch = None
    if fetch:
        levels = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}
        return levels.get(fetch[0], 0)
    return 0

async def is_chat_owner(user_id: int, chat_id: int) -> bool:
    sql.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    return bool(fetch and fetch[0] == user_id)

async def save_chat_owner(chat_id: int, owner_id: int):
    if owner_id == 0:
        return None
    sql.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch and fetch[0] != owner_id:
        sql.execute("UPDATE chats SET owner_id = ? WHERE chat_id = ?", (owner_id, chat_id))
        if owner_id > 0:
            sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = ?", (owner_id,))
            sql.execute("DELETE FROM user_roles WHERE chat_id = ? AND user_id = ?", (chat_id, owner_id))
        database.commit()
    return owner_id

async def sync_chat_owner_from_vk(chat_id: int, peer_id: int, group_id: int):
    try:
        try:
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=group_id)
        except Exception:
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings')
        x = json.loads(x.json())
        for i in x.get('items', []):
            owner_id = int(i.get("chat_settings", {}).get("owner_id", 0))
            if owner_id != 0:
                return await save_chat_owner(chat_id, owner_id)
    except Exception:
        pass

    try:
        members = await bot.api.messages.get_conversation_members(peer_id=peer_id)
        members = json.loads(members.json())
        for item in members.get("items", []):
            if item.get("is_owner") or item.get("role") == "owner":
                owner_id = int(item.get("member_id", 0))
                if owner_id != 0:
                    return await save_chat_owner(chat_id, owner_id)
    except Exception:
        pass

    return None

async def can_grant_sa(user_id: int, chat_id: int) -> bool:
    if int(user_id) == ACTIVATION_OWNER_ID:
        return True
    if await is_chat_owner(user_id, chat_id):
        return True
    sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    return bool(fetch and fetch[0] == 2)

async def get_custom_role_name(user_id, chat_id):
    sql.execute("SELECT role_name FROM user_roles WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    fetch = sql.fetchone()
    return fetch[0] if fetch else None

async def check_perm(user_id, chat_id, command, def_lvl):
    p = await get_priority(user_id, chat_id)
    if p >= 200: return True
    
    sql.execute("SELECT 1 FROM user_commands WHERE chat_id = ? AND user_id = ? AND command = ?", (chat_id, user_id, command.lower()))
    if sql.fetchone(): return True
    
    sql.execute("SELECT priority FROM command_perms WHERE chat_id = ? AND command = ?", (chat_id, command.lower()))
    fetch = sql.fetchone()
    if fetch: return p >= fetch[0]
    lvls = {0:0, 1:21, 2:41, 3:51, 4:61, 5:80, 6:100, 7:100}
    return p >= lvls.get(def_lvl, 0)

for col, defval in [("mute_warns", "0"), ("protect", "0"), ("minigames", "1"), ("gsync", "0")]:
    try:
        sql.execute(f"ALTER TABLE chats ADD COLUMN {col} INTEGER DEFAULT {defval}")
        database.commit()
    except: pass

sql.execute("CREATE TABLE IF NOT EXISTS user_data (user_id BIGINT PRIMARY KEY, age INTEGER DEFAULT 0, has_pc INTEGER DEFAULT 0, discord TEXT DEFAULT 'Не указан', forum TEXT DEFAULT 'Не указан', points INTEGER DEFAULT 0, last_appointment TEXT DEFAULT '0', global_ban INTEGER DEFAULT 0, aban INTEGER DEFAULT 0, preds INTEGER DEFAULT 0);")
sql.execute("CREATE TABLE IF NOT EXISTS economy (user_id BIGINT PRIMARY KEY, balance INTEGER DEFAULT 0, bank INTEGER DEFAULT 0, charity INTEGER DEFAULT 0, vip_until TEXT DEFAULT '0', last_prize TEXT DEFAULT '0', deposit_amount INTEGER DEFAULT 0, deposit_opened TEXT DEFAULT '0');")
sql.execute("CREATE TABLE IF NOT EXISTS businesses (user_id BIGINT PRIMARY KEY, level INTEGER DEFAULT 0, cash INTEGER DEFAULT 0, products INTEGER DEFAULT 0, last_income TEXT DEFAULT '0');")
sql.execute("CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, amount INTEGER DEFAULT 0, activations INTEGER DEFAULT 0);")
sql.execute("CREATE TABLE IF NOT EXISTS promo_uses (code TEXT, user_id BIGINT, PRIMARY KEY(code, user_id));")
sql.execute("CREATE TABLE IF NOT EXISTS global_bans (user_id BIGINT PRIMARY KEY, moder_id BIGINT, reason TEXT, date TEXT);")
database.commit()

for _col, _def in [
    ("duel_wins", "0"), ("duel_losses", "0"),
    ("total_won", "0"), ("total_lost", "0"),
    ("roulette_won", "0"), ("roulette_lost", "0"),
    ("sent_transfers", "0"), ("received_transfers", "0"),
    ("deposit_days", "10"), ("deposit_percent", "35"),
]:
    try:
        sql.execute(f"ALTER TABLE economy ADD COLUMN {_col} INTEGER DEFAULT {_def}")
        database.commit()
    except: pass

async def get_user_data(user_id: int):
    sql.execute("SELECT age, has_pc, discord, forum, points, last_appointment, global_ban, aban, preds FROM user_data WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch is None:
        sql.execute("INSERT INTO user_data (user_id) VALUES (?)", (user_id,))
        database.commit()
        return {
            'age': 0, 'has_pc': 0, 'discord': 'Не указан', 'forum': 'Не указан',
            'points': 0, 'last_appointment': '0', 'global_ban': 0, 'aban': 0, 'preds': 0
        }
    return {
        'age': fetch[0], 'has_pc': fetch[1], 'discord': fetch[2], 'forum': fetch[3],
        'points': fetch[4], 'last_appointment': fetch[5], 'global_ban': fetch[6], 'aban': fetch[7], 'preds': fetch[8]
    }

async def update_user_data(user_id: int, key: str, value):
    sql.execute(f"UPDATE user_data SET {key} = ? WHERE user_id = ?", (value, user_id))
    database.commit()

async def get_global_ban_info(user_id: int):
    sql.execute("SELECT moder_id, reason, date FROM global_bans WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch:
        return {'moder_id': fetch[0], 'reason': fetch[1], 'date': fetch[2]}
    return None

async def set_global_ban(user_id: int, moder_id: int, reason: str):
    date_str = datetime.now().strftime("%d/%m/%Y %I:%M:%S %p")
    sql.execute("INSERT OR REPLACE INTO global_bans (user_id, moder_id, reason, date) VALUES (?, ?, ?, ?)",
                (user_id, moder_id, reason, date_str))
    sql.execute("INSERT OR IGNORE INTO user_data (user_id) VALUES (?)", (user_id,))
    sql.execute("UPDATE user_data SET global_ban = 1 WHERE user_id = ?", (user_id,))
    database.commit()

async def remove_global_ban(user_id: int):
    sql.execute("DELETE FROM global_bans WHERE user_id = ?", (user_id,))
    sql.execute("INSERT OR IGNORE INTO user_data (user_id) VALUES (?)", (user_id,))
    sql.execute("UPDATE user_data SET global_ban = 0 WHERE user_id = ?", (user_id,))
    database.commit()

def today_string():
    return datetime.now().strftime("%Y-%m-%d")

def parse_money(value):
    try:
        amount = int(str(value).replace("к", "000").replace("k", "000"))
        return amount if amount > 0 else False
    except:
        return False

async def get_economy(user_id: int):
    sql.execute("SELECT balance, bank, charity, vip_until, last_prize, deposit_amount, deposit_opened, duel_wins, duel_losses, total_won, total_lost, roulette_won, roulette_lost, sent_transfers, received_transfers, deposit_days, deposit_percent FROM economy WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch is None:
        sql.execute("INSERT INTO economy (user_id) VALUES (?)", (user_id,))
        database.commit()
        return {'balance': 0, 'bank': 0, 'charity': 0, 'vip_until': '0', 'last_prize': '0', 'deposit_amount': 0, 'deposit_opened': '0', 'duel_wins': 0, 'duel_losses': 0, 'total_won': 0, 'total_lost': 0, 'roulette_won': 0, 'roulette_lost': 0, 'sent_transfers': 0, 'received_transfers': 0, 'deposit_days': 10, 'deposit_percent': 35}
    return {'balance': fetch[0], 'bank': fetch[1], 'charity': fetch[2], 'vip_until': fetch[3], 'last_prize': fetch[4], 'deposit_amount': fetch[5], 'deposit_opened': fetch[6], 'duel_wins': fetch[7] or 0, 'duel_losses': fetch[8] or 0, 'total_won': fetch[9] or 0, 'total_lost': fetch[10] or 0, 'roulette_won': fetch[11] or 0, 'roulette_lost': fetch[12] or 0, 'sent_transfers': fetch[13] or 0, 'received_transfers': fetch[14] or 0, 'deposit_days': fetch[15] or 10, 'deposit_percent': fetch[16] or 35}

async def update_economy(user_id: int, key: str, value):
    await get_economy(user_id)
    sql.execute(f"UPDATE economy SET {key} = ? WHERE user_id = ?", (value, user_id))
    database.commit()

async def add_balance(user_id: int, amount: int):
    data = await get_economy(user_id)
    await update_economy(user_id, 'balance', data['balance'] + amount)

async def is_vip(user_id: int):
    data = await get_economy(user_id)
    try:
        return datetime.fromisoformat(data['vip_until']) > datetime.now()
    except:
        return False

async def get_business(user_id: int):
    sql.execute("SELECT level, cash, products, last_income FROM businesses WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch is None:
        sql.execute("INSERT INTO businesses (user_id) VALUES (?)", (user_id,))
        database.commit()
        return {'level': 0, 'cash': 0, 'products': 0, 'last_income': '0'}
    return {'level': fetch[0], 'cash': fetch[1], 'products': fetch[2], 'last_income': fetch[3]}

async def update_business(user_id: int, key: str, value):
    await get_business(user_id)
    sql.execute(f"UPDATE businesses SET {key} = ? WHERE user_id = ?", (value, user_id))
    database.commit()

async def check_chat(chat_id=int):
    sql.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))
    if sql.fetchone() == None: return False
    else: return True

async def new_chat(chat_id=int, peer_id=int, owner_id=int, activated_by=int):
    if not await can_activate_bot(activated_by):
        return False
    sql.execute("INSERT INTO chats VALUES (?, ?, ?, 'Добро пожаловать, уважаемый %i пользователь!', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);", (chat_id, peer_id, owner_id))
    sql.execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS nicks_{chat_id} (user_id BIGINT, nick TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS banwords_{chat_id} (banword TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS warns_{chat_id} (user_id BIGINT, count BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS mutes_{chat_id} (user_id BIGINT, moder TEXT, reason TEXT, date BIGINT, date_string TEXT, time BIGINT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS bans_{chat_id} (user_id BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS messages_{chat_id} (user_id BIGINT, date BIGINT, date_string TEXT, message_id BIGINT, cmid BIGINT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS warnhistory_{chat_id} (user_id BIGINT, count BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS punishments_{chat_id} (user_id BIGINT, date TEXT);")
    database.commit()
    return True

async def get_warns(user_id=int, chat_id=int):
    sql.execute(f"SELECT count FROM warns_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch == None: return 0
    else: return fetch[0]

async def get_user_name(user_id=int, chat_id=int):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch is not None:
        return fetch[0]
    else:
        try:
            info = await bot.api.users.get(user_ids=user_id)
            if info:
                return f"{info[0].first_name} {info[0].last_name}"
            else:
                return 'Пользователь'
        except Exception:
            return 'Пользователь'

async def is_nick(user_id=int, chat_id=int):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
    if sql.fetchone() == None: return False
    else: return True

async def setnick(user_id=int, chat_id=int, nick=str):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
    if sql.fetchone() == None:
        sql.execute(f"INSERT INTO nicks_{chat_id} VALUES (?, ?)", (user_id, nick))
        database.commit()
    else:
        sql.execute(f"UPDATE nicks_{chat_id} SET nick = ? WHERE user_id = ?", (nick, user_id))
        database.commit()

async def rnick(user_id=int, chat_id=int):
    sql.execute(f"DELETE FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
    database.commit()

async def equals_roles(user_id_sender=int, user_id_two=int, chat_id=int):
    if int(user_id_sender) == ACTIVATION_OWNER_ID:
        return 2
    if int(user_id_two) == ACTIVATION_OWNER_ID:
        return 0
    if await get_role(user_id_sender, chat_id) > await get_role(user_id_two, chat_id):
        return 2
    elif await get_role(user_id_sender, chat_id) == await get_role(user_id_two, chat_id):
        return 1
    else: return 0

async def get_acc(chat_id=int, nick=str):
    sql.execute(f"SELECT user_id FROM nicks_{chat_id} WHERE nick = ?", (nick,))
    fetch = sql.fetchone()
    if fetch == None: return False
    else: return fetch[0]

async def get_nick(user_id=int, chat_id=int):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    else: return fetch[0]

async def nlist(chat_id=int, page=int):
    sql.execute(f"SELECT * FROM nicks_{chat_id}")
    fetch = sql.fetchall()
    nicks = []
    gi = 0
    with open("config.json", "r") as json_file:
        open_file = json.load(json_file)
    max_nicks = open_file['nicks_max']
    for i in fetch:
        gi = gi + 1
        if page * max_nicks >= gi and page * max_nicks - max_nicks < gi:
            info = await bot.api.users.get(user_ids=i[0])
            if info:
                user_name = f"{info[0].first_name} {info[0].last_name}"
            else:
                user_name = "Удаленный пользователь"
            
            nicks.append(f'{gi}) @id{i[0]} ({user_name}) - {i[1]}')
    return nicks

async def nonick(chat_id=int, page=int):
    sql.execute(f"SELECT * FROM nicks_{chat_id}")
    fetch = sql.fetchall()
    nicks = []
    for i in fetch:
        nicks.append(i[0])

    gi = 0
    nonick = []
    with open("config.json", "r") as json_file:
        open_file = json.load(json_file)
    max_nonick = open_file['nonick_max']
    users = await bot.api.messages.get_conversation_members(peer_id=2000000000+chat_id)
    users = json.loads(users.json())
    for i in users["profiles"]:
        if not i['id'] in nicks:
            gi = gi + 1
            if page*max_nonick >= gi and page*max_nonick-max_nonick < gi:
                nonick.append(f"{gi}) @id{i['id']} ({i['first_name']} {i['last_name']})")

    return nonick

async def warn(chat_id=int, user_id=int, moder=int, reason=str):
    actualy_warns = await get_warns(user_id, chat_id)
    date = time.time()
    cd = str(datetime.now()).split('.')
    date_string = cd[0]
    sql.execute(f"INSERT INTO warnhistory_{chat_id} VALUES (?, ?, ?, ?, ?, ?)", (user_id, actualy_warns+1, moder, reason, date, date_string))
    database.commit()
    if actualy_warns < 1:
        sql.execute(f"INSERT INTO warns_{chat_id} VALUES (?, 1, ?, ?, ?, ?)", (user_id, moder, reason, date, date_string))
        database.commit()
        return 1
    else:
        sql.execute(f"UPDATE warns_{chat_id} SET user_id = ?, count = ?, moder = ?, reason = ?, date = ?, date_string = ? WHERE user_id = ?", (user_id, actualy_warns+1, moder, reason, date, date_string, user_id))
        database.commit()
        return actualy_warns+1

async def clear_warns(chat_id=int, user_id=int):
    sql.execute(f"DELETE FROM warns_{chat_id} WHERE user_id = ?", (user_id,))
    database.commit()

async def unwarn(chat_id=int, user_id=int):
    warns = await get_warns(user_id, chat_id)
    if warns < 2: await clear_warns(chat_id, user_id)
    else:
        sql.execute(f"UPDATE warns_{chat_id} SET count = ? WHERE user_id = ?", (warns-1, user_id))
        database.commit()

    return warns-1

async def gwarn(user_id=int, chat_id=int):
    sql.execute(f"SELECT * FROM warns_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    else:
        return {
            'count': fetch[1],
            'moder': fetch[2],
            'reason': fetch[3],
            'time': fetch[5]
        }

async def warnhistory(user_id=int, chat_id=int):
    sql.execute(f"SELECT * FROM warnhistory_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchall()
    warnhistory_mass = []
    gi = 0
    if fetch == None: return False
    else:
        for i in fetch:
            gi = gi + 1
            warnhistory_mass.append(f"{gi}) [id{i[2]}|Модератор] | {i[3]} | {i[5]}")

    return warnhistory_mass

async def warnlist(chat_id=int):
    sql.execute(f"SELECT * FROM warns_{chat_id}")
    fetch = sql.fetchall()
    warns = []
    gi = 0
    for i in fetch:
        gi = gi + 1
        warns.append(f"{gi}) [id{i[0]}|Пользователь] | {i[3]} | [id{i[2]}|Модератор] | {i[1]}/3 | {i[5]}")

    if fetch == None: return False
    return warns

async def staff(chat_id=int):
    sql.execute(f"SELECT * FROM permissions_{chat_id}")
    fetch = sql.fetchall()

    roles_data = {
        "Спец администраторы": [],
        "Зам.спец администратора": [],
        "Старшие администраторы": [],
        "Администраторы": [],
        "Старшие модераторы": [],
        "Модераторы": []
    }

    std_names = {1: "Модераторы", 2: "Старшие модераторы", 3: "Администраторы", 4: "Старшие администраторы"}

    def add_user(role_name, user_id, user_name):
        if role_name not in roles_data:
            roles_data[role_name] = []
        user_link = f"[id{user_id}|{user_name}]" if int(user_id) > 0 else user_name
        if user_link not in roles_data[role_name]:
            roles_data[role_name].append(user_link)

    def detect_staff_group(role_name, priority):
        normalized = role_name.lower().replace("ё", "е").strip()

        if ("зам" in normalized or "зам." in normalized) and "спец" in normalized:
            return "Зам.спец администратора"
        if "спец" in normalized or "специальн" in normalized:
            return "Спец администраторы"
        if "руководитель модерации" in normalized or "ст админ" in normalized or "ст. админ" in normalized or ("старш" in normalized and "админ" in normalized):
            return "Старшие администраторы"
        if normalized in ["гм", "главный модератор"] or "администратор" in normalized or normalized == "админ":
            return "Администраторы"
        if normalized in ["км", "озгм", "згм"] or "куратор" in normalized or "зам глав" in normalized or "старш" in normalized and "модер" in normalized:
            return "Старшие модераторы"
        if "модер" in normalized:
            return "Модераторы"

        # Раньше любое кастомное название роли, не совпавшее ни с одним
        # ключевым словом выше, тихо попадало в стандартный бакет по
        # приоритету — и последний бакет по умолчанию был "Модераторы".
        # Из-за этого пользователь с произвольной кастомной ролью (например,
        # "VIP" с небольшим приоритетом) отображался в /staff как обычный
        # модератор, хотя это была никак не связанная с модерацией роль.
        # Теперь непризнанное имя роли показывается под своим собственным
        # названием, а не подменяется на "Модераторы".
        return role_name

    if fetch:
        for i in fetch:
            level = i[1]
            if level in std_names:
                role_name = std_names[level]
                name = await get_user_name(i[0], chat_id)
                add_user(role_name, i[0], name)

    sql.execute("SELECT user_id FROM global_managers WHERE level = 2")
    fetch_global = sql.fetchall()
    for i in fetch_global:
        if int(i[0]) == ACTIVATION_OWNER_ID:
            continue
        name = await get_user_name(i[0], chat_id)
        add_user("Спец администраторы", i[0], name)

    sql.execute("SELECT ur.user_id, ur.role_name, r.priority FROM user_roles ur JOIN chat_roles r ON ur.chat_id = r.chat_id AND ur.role_name = r.name WHERE ur.chat_id = ? ORDER BY r.priority DESC", (chat_id,))
    fetch_custom = sql.fetchall()
    for i in fetch_custom:
        if int(i[0]) == ACTIVATION_OWNER_ID:
            continue
        role_name = detect_staff_group(i[1], i[2])
        name = await get_user_name(i[0], chat_id)
        add_user(role_name, i[0], name)

    return roles_data

async def mute(user_id=int, chat_id=int, moder=int, reason=str, mute_time=int):
    cd = str(datetime.now()).split('.')
    date_string = cd[0]
    sql.execute(f"INSERT INTO mutes_{chat_id} VALUES (?, ?, ?, ?, ?, ?)", (user_id, moder, reason, time.time(), date_string, mute_time))
    database.commit()

async def get_mute(user_id=int, chat_id=int):
    await checkMute(chat_id, user_id)

    sql.execute(f"SELECT * FROM mutes_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()

    if fetch == None: return False
    else:
        return {
            'moder': fetch[1],
            'reason': fetch[2],
            'date': fetch[4],
            'time': fetch[5]
        }

async def unmute(user_id=int, chat_id=int):
    sql.execute(f"DELETE FROM mutes_{chat_id} WHERE user_id = ?", (user_id,))
    database.commit()

async def mutelist(chat_id=int):
    sql.execute(f"SELECT * FROM mutes_{chat_id}")
    fetch = sql.fetchall()
    mutes = []
    if fetch==None: return False
    else:
        for i in fetch:
            if not await checkMute(chat_id, i[0]):
                do_time = datetime.fromisoformat(i[4]) + timedelta(minutes=i[5])
                mute_time = str(do_time).split('.')[0]
                try:
                    int(i[1])
                    mutes.append(f"[id{i[1]}|модератор] | {i[2]} | [id{i[0]}|Пользователь] | До: {mute_time}")
                except: mutes.append(f"Бот | {i[2]} | [id{i[0]}|Пользователь] | До: {mute_time}")

    return mutes

async def checkMute(chat_id=int, user_id=int):
    sql.execute(f"SELECT * FROM mutes_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if not fetch == None:
        do_time = datetime.fromisoformat(fetch[4]) + timedelta(minutes=fetch[5])
        if datetime.now() > do_time:
            sql.execute(f"DELETE FROM mutes_{chat_id} WHERE user_id = ?", (user_id,))
            database.commit()
            return True
        else: return False
    return False

async def check_quit(chat_id=int):
    sql.execute("SELECT silence FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    else:
        return await get_logic(fetch[0])

async def get_banwords(chat_id=int):
    sql.execute(f"SELECT * FROM banwords_{chat_id}")
    banwords = []
    fetch = sql.fetchall()
    for i in fetch:
        banwords.append(i[0])

    return banwords

async def clear(user_id=int, chat_id=int, group_id=int, peer_id=int):
    sql.execute(f"SELECT cmid FROM messages_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchall()
    cmids = []
    gi = 0
    for i in fetch:
        gi = gi + 1
        if gi <= 199:
            cmids.append(i[0])
    try: await bot.api.messages.delete(group_id=group_id, peer_id=peer_id, delete_for_all=True, cmids=cmids)
    except: pass

    sql.execute(f"DELETE FROM messages_{chat_id} WHERE user_id = ?", (user_id,))
    database.commit()

async def new_message(user_id=int, message_id=int, cmid=int, chat_id=int):
    cd = str(datetime.now()).split('.')
    date_string = cd[0]
    sql.execute(f"INSERT INTO messages_{chat_id} VALUES (?, ?, ?, ?, ?)", (user_id, time.time(), date_string, message_id, cmid))
    database.commit()

async def checkban(user_id=int, chat_id=int):
    sql.execute(f"SELECT * FROM bans_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    else:
        return {
            'moder': fetch[1],
            'reason': fetch[2],
            'date': fetch[4]
        }

async def ban(user_id=int, moder=int, chat_id=int, reason=str):
    sql.execute(f"SELECT user_id FROM bans_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    cd = str(datetime.now()).split('.')
    date_string = cd[0]
    if fetch == None:
        sql.execute(f"INSERT INTO bans_{chat_id} VALUES (?, ?, ?, ?, ?)", (user_id, moder, reason, time.time(), date_string))
        database.commit()
    else:
        sql.execute(f"DELETE FROM bans_{chat_id} WHERE user_id = ?", (user_id,))
        sql.execute(f"INSERT INTO bans_{chat_id} VALUES (?, ?, ?, ?, ?)",(user_id, moder, reason, time.time(), date_string))
        database.commit()

async def unban(user_id=int, chat_id=int):
    sql.execute(f"DELETE FROM bans_{chat_id} WHERE user_id = ?", (user_id,))
    database.commit()

async def roleG(user_id=int, chat_id=int, role=int):
    if await is_chat_owner(user_id, chat_id):
        sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = ?", (user_id,))
        sql.execute("DELETE FROM user_roles WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        database.commit()
        return False

    # Стандартная (не кастомная) роль и кастомная роль хранятся в двух разных
    # таблицах. Раньше roleG трогал только permissions_{chat_id}, из-за чего
    # если у пользователя была кастомная роль (user_roles), /rrole её не снимал
    # (get_role всегда сначала смотрит user_roles), а выдача обычной роли при
    # наличии кастомной роли вообще не давала эффекта. Поэтому здесь всегда
    # приводим обе таблицы к согласованному состоянию — источник правды один.
    sql.execute("DELETE FROM user_roles WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))

    sql.execute(f"SElECT user_id FROM permissions_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch == None:
        if role == 0: sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = ?", (user_id,))
        else: sql.execute(f"INSERT INTO permissions_{chat_id} VALUES (?, ?)", (user_id, role))
    else:
        if role == 0: sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = ?", (user_id,))
        else: sql.execute(f"UPDATE permissions_{chat_id} SET level = ? WHERE user_id = ?", (role, user_id))

    database.commit()
    return True

async def banlist(chat_id=int):
    sql.execute(f"SELECT * FROM bans_{chat_id}")
    fetch = sql.fetchall()
    banlist = []
    for i in fetch:
        banlist.append(f"[id{i[1]}|Модератор] | {i[2]} | [id{i[0]}|Пользователь] | {i[4]}")

    return banlist

async def quiet(chat_id=int):
    sql.execute("SELECT silence FROM chats WHERE chat_id = ?", (chat_id,))
    result = sql.fetchone()[0]
    if not await get_logic(result):
        sql.execute("UPDATE chats SET silence = 1 WHERE chat_id = ?", (chat_id,))
        database.commit()
        return True
    else:
        sql.execute("UPDATE chats SET silence = 0 WHERE chat_id = ?", (chat_id,))
        database.commit()
        return False

async def get_pull_chats(chat_id=int):
    sql.execute("SELECT owner_id, in_pull FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    if not await get_logic(fetch[1]): return False
    sql.execute("SELECT chat_id FROM chats WHERE owner_id = ? AND in_pull = ?", (fetch[0], fetch[1]))
    result = []
    fetch2 = sql.fetchall()
    for i in fetch2:
        result.append(i[0])

    return result

async def get_pull_id(chat_id=int):
    sql.execute("SELECT in_pull FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    return fetch[0]

async def rnickall(chat_id=int):
    sql.execute(f"DELETE FROM nicks_{chat_id}")
    database.commit()

async def banwords(slovo=str, delete=bool, chat_id=int):
    if delete:
        sql.execute(f"DELETE FROM banwords_{chat_id} WHERE banword = ?", (slovo, ))
        database.commit()
    else:
        sql.execute(f"SELECT * FROM banwords_{chat_id} WHERE banword = ?", (slovo, ))
        fetch = sql.fetchone()
        if fetch == None:
            sql.execute(f"INSERT INTO banwords_{chat_id} VALUES (?)", (slovo,))
            database.commit()

async def get_filter(chat_id=int):
    sql.execute("SELECT filter FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    return await get_logic(fetch[0])

async def set_filter(chat_id=int, value=int):
    sql.execute("UPDATE chats SET filter = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_antiflood(chat_id=int):
    sql.execute("SELECT antiflood FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    return await get_logic(fetch[0])

async def set_antiflood(chat_id=int, value=int):
    sql.execute("UPDATE chats SET antiflood = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_spam(user_id=int, chat_id=int):
    sql.execute(f"SELECT date_string FROM messages_{chat_id}  WHERE user_id = ? ORDER BY date_string DESC LIMIT 3", (user_id,))
    fetch = sql.fetchall()
    
    if len(fetch) < 3:
        return False
        
    list_messages = []
    for i in fetch:
        list_messages.append(datetime.fromisoformat(i[0]))

    if list_messages[0] - list_messages[2] < timedelta(seconds=2): return True
    else: return False

async def set_welcome(chat_id=int, text=int):
    sql.execute(f"UPDATE chats SET welcome = ? WHERE chat_id = ?", (text, chat_id))
    database.commit()

async def get_welcome(chat_id=int):
    sql.execute("SELECT welcome FROM chats WHERE chat_id = ?", (chat_id, ))
    fetch = sql.fetchone()
    if not fetch: return False
    if str(fetch[0]).lower().strip() == "off": return False
    else: return str(fetch[0])

async def invite_kick(chat_id=int, change=None):
    sql.execute("SELECT invite_kick FROM chats WHERE chat_id = ?", (chat_id, ))
    fetch = sql.fetchone()
    if not change == None:
        if await get_logic(fetch[0]):
            sql.execute("UPDATE chats SET invite_kick = 0 WHERE chat_id = ?", (chat_id, ))
            database.commit()
            return False
        else:
            sql.execute("UPDATE chats SET invite_kick = 1 WHERE chat_id = ?", (chat_id,))
            database.commit()
            return True
    else:
        return await get_logic(fetch[0])

async def leave_kick(chat_id=int, change=None):
    sql.execute("SELECT leave_kick FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    if change == None: return await get_logic(fetch[0])
    if await get_logic(fetch[0]):
        sql.execute("UPDATE chats SET leave_kick = 0 WHERE chat_id = ?", (chat_id,))
        database.commit()
        return False
    else:
        sql.execute("UPDATE chats SET leave_kick = 1 WHERE chat_id = ?", (chat_id,))
        database.commit()
        return True

async def message_stats(user_id=int, chat_id=int):
    try:
        sql.execute(f"SELECT date_string FROM messages_{chat_id} WHERE user_id = ?", (user_id, ))
        fetch_all = sql.fetchall()
        sql.execute(f"SELECT date_string FROM messages_{chat_id} WHERE user_id = ? ORDER BY date_string DESC LIMIT 1", (user_id,))
        fetch_last = sql.fetchone()
        last = fetch_last[0]
        return {
            'count': len(fetch_all),
            'last': last
        }
    except: return {
        'count': 0,
        'last': 0
    }

def format_stats_date(date_string):
    if not date_string or date_string == 0:
        return "Нет"
    try:
        return datetime.fromisoformat(str(date_string)).strftime("%d/%m/%Y %I:%M:%S %p")
    except:
        return str(date_string)

async def get_user_ban_count(user_id=int, chat_id=int):
    chats = await get_pull_chats(chat_id)
    if not chats:
        chats = [chat_id]

    count = 0
    for i in chats:
        try:
            sql.execute(f"SELECT COUNT(*) FROM bans_{i} WHERE user_id = ?", (user_id,))
            fetch = sql.fetchone()
            if fetch:
                count += fetch[0]
        except:
            pass
    return count

async def get_pull_ban_status(user_id=int, chat_id=int):
    chats = await get_pull_chats(chat_id)
    if not chats:
        return False

    for i in chats:
        try:
            sql.execute(f"SELECT 1 FROM bans_{i} WHERE user_id = ?", (user_id,))
            if sql.fetchone():
                return True
        except:
            pass
    return False

async def get_full_stats_message(user_id=int, chat_id=int, actual_owner_id=None):
    user_id = int(user_id)
    role = await get_role(user_id, chat_id)
    if actual_owner_id is not None and int(actual_owner_id) == user_id and role < 7:
        role = 7
    custom_role = None if role >= 7 else await get_custom_role_name(user_id, chat_id)
    warns = await get_warns(user_id, chat_id)
    ud = await get_user_data(user_id)
    messages = await message_stats(user_id, chat_id)
    mute = await get_mute(user_id, chat_id)
    nick = await get_nick(user_id, chat_id)

    roles = {
        0: "Пользователь",
        1: "Модератор",
        2: "Старший Модератор",
        3: "Администратор",
        4: "Старший Администратор",
        5: "Зам.спец администратора",
        6: "Менеджер бота",
        7: "Владелец беседы",
        9: "Зам.Спец.Рук.",
        10: "Основ.Зам.Спец.Рук.",
        50: "Специальный руководитель"
    }

    return (
        f"Информация о [id{user_id}|пользователе]\n"
        f"Роль: {custom_role if custom_role else roles.get(role, 'Пользователь')}\n"
        f"Блокировок: {await get_user_ban_count(user_id, chat_id)}\n"
        f"Общая блокировка в чатах: {'Да' if await get_pull_ban_status(user_id, chat_id) else 'Нет'}\n"
        f"Общая блокировка в беседах игроков: {'Да' if ud['global_ban'] else 'Нет'}\n"
        f"Активные предупреждения: {warns}\n"
        f"Блокировка чата: {'Да' if mute else 'Нет'}\n"
        f"Ник: {nick if nick else 'Нет'}\n"
        f"Всего сообщений: {messages['count']}\n"
        f"Последнее сообщение: {format_stats_date(messages['last'])}"
    )

async def set_pull(chat_id=int, value=int):
    sql.execute("UPDATE chats SET in_pull = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_all_peerids():
    sql.execute("SELECT peer_id FROM chats")
    fetch = sql.fetchall()
    peer_ids = []
    for i in fetch:
        peer_ids.append(i[0])

    return peer_ids

async def add_punishment(chat_id=int, user_id=int):
    cd = str(datetime.now()).split('.')
    date_string = cd[0]
    sql.execute(f"INSERT INTO punishments_{chat_id} VALUES (?, ?)", (user_id, date_string))
    database.commit()

async def get_sliv(user_id=int, chat_id=int):
    sql.execute(f"SELECT date FROM punishments_{chat_id}  WHERE user_id = ? ORDER BY date DESC LIMIT 3", (user_id,))
    fetch = sql.fetchall()
    
    if len(fetch) < 3:
        return False

    list_messages = []
    for i in fetch:
        list_messages.append(datetime.fromisoformat(i[0]))

    if list_messages[0] - list_messages[2] < timedelta(seconds=6):
        return True
    else:
        return False

async def staff_zov(chat_id=int):
    sql.execute(f"SElECT user_id FROM permissions_{chat_id}")
    fetch = sql.fetchall()
    staff_zov_str = []
    for i in fetch:
        staff_zov_str.append(f"[id{i[0]}|⚜️]")

    return ''.join(staff_zov_str)

async def delete_message(group_id=int, peer_id=int, cmid=int):
    try: await bot.api.messages.delete(group_id=group_id, peer_id=peer_id, delete_for_all=True, cmids=cmid)
    except: pass

async def set_onwer(user=int, chat=int):
    sql.execute("UPDATE chats SET owner_id = ? WHERE chat_id = ?", (user, chat))
    sql.execute(f"DELETE FROM permissions_{chat} WHERE user_id = ?", (user,))
    sql.execute("DELETE FROM user_roles WHERE chat_id = ? AND user_id = ?", (chat, user))
    database.commit()

async def get_mute_warns(chat_id):
    try:
        sql.execute("SELECT mute_warns FROM chats WHERE chat_id = ?", (chat_id,))
        fetch = sql.fetchone()
        return fetch[0] if fetch else 0
    except: return 0

async def set_mute_warns(chat_id, value):
    sql.execute("UPDATE chats SET mute_warns = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_protect(chat_id):
    try:
        sql.execute("SELECT protect FROM chats WHERE chat_id = ?", (chat_id,))
        fetch = sql.fetchone()
        return fetch[0] if fetch else 0
    except: return 0

async def set_protect(chat_id, value):
    sql.execute("UPDATE chats SET protect = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_minigames(chat_id):
    try:
        sql.execute("SELECT minigames FROM chats WHERE chat_id = ?", (chat_id,))
        fetch = sql.fetchone()
        return fetch[0] if fetch else 1
    except: return 1

async def set_minigames(chat_id, value):
    sql.execute("UPDATE chats SET minigames = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_gsync(chat_id):
    try:
        sql.execute("SELECT gsync FROM chats WHERE chat_id = ?", (chat_id,))
        fetch = sql.fetchone()
        return fetch[0] if fetch else 0
    except: return 0

async def set_gsync(chat_id, value):
    sql.execute("UPDATE chats SET gsync = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

@bot.on.chat_message(rules.ChatActionRule("chat_kick_user"))
async def user_leave(message: Message) -> None:
    user_id = message.from_id
    chat_id = message.chat_id
    if not await check_chat(chat_id): return True
    if not message.action.member_id == message.from_id: return True
    if await leave_kick(chat_id):
        try: await bot.api.messages.remove_chat_user(chat_id, user_id)
        except: pass
        await message.answer(f"@id{user_id} (Пользователь) вышел(-а) из беседы", disable_mentions=1)
    else:
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("Исключить", {"command": "kick", "user": user_id, "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
        )
        await message.answer(f"@id{user_id} (Пользователь) вышел(-а) из беседы", disable_mentions=1, keyboard=keyboard)

@bot.on.chat_message(rules.ChatActionRule("chat_invite_user_by_link"))
async def user_joined_link(message: Message) -> None:
    user_id = message.from_id
    chat_id = message.chat_id
    if not await check_chat(chat_id): return True

    ud_link = await get_user_data(user_id)
    if ud_link['global_ban'] and user_id > 0:
        try:
            await bot.api.messages.remove_chat_user(chat_id, user_id)
        except:
            pass
        gban_info = await get_global_ban_info(user_id)
        info_u = await bot.api.users.get(user_ids=user_id)
        uname = f"{info_u[0].first_name} {info_u[0].last_name}" if info_u else f"id{user_id}"
        keyboard_gban = (
            Keyboard(inline=True)
            .add(Callback("Разблокировать", {"command": "ungbanpl", "user": user_id}), color=KeyboardButtonColor.POSITIVE)
        )
        if gban_info:
            await message.answer(
                f"[id{user_id}|{uname}], находится в общей блокировке!\n\n"
                f"Информация о блокировке:\n"
                f"[id{gban_info['moder_id']}|Модератор] | {gban_info['reason']} | {gban_info['date']}",
                disable_mentions=1, keyboard=keyboard_gban
            )
        else:
            await message.answer(f"⛔ [id{user_id}|{uname}] имеет глобальный бан и был исключён из беседы!", disable_mentions=1, keyboard=keyboard_gban)
        return True

    checkban_str = await checkban(user_id, chat_id)
    if checkban_str:
        try: await bot.api.messages.remove_chat_user(chat_id, user_id)
        except: pass
        info = await bot.api.users.get(user_id)
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("Снять бан", {"command": "unban", "user": user_id, "chatId": chat_id}),color=KeyboardButtonColor.POSITIVE)
        )
        await message.answer(f"@id{user_id} ({info[0].first_name}) заблокирован(-а) в этой беседе!\n\nИнформация о блокировке:\n@id{checkban_str['moder']} (Модератор) | {checkban_str['reason']} | {checkban_str['date']}",disable_mentions=1, keyboard=keyboard)
        return True

    welcome = await get_welcome(chat_id)
    if welcome:
        info = await bot.api.users.get(user_id)
        welcome = welcome.replace('%u', f'@id{user_id}')
        welcome = welcome.replace('%n', f'@id{user_id} ({info[0].first_name})')
        welcome = welcome.replace('%i', f'@id{user_id}')
        uinfo = await bot.api.users.get(user_id)
        welcome = welcome.replace('%p', f'@id{user_id} ({uinfo[0].first_name})')
        await message.answer(welcome)

@bot.on.chat_message(rules.ChatActionRule("chat_invite_user"))
async def user_joined(message: Message) -> None:
    invited_user = message.action.member_id
    user_id = message.from_id
    chat_id = message.chat_id
    peer_id = message.peer_id

    if invited_user == -224437676:
        if not await check_chat(chat_id):
            try:
                members = await bot.api.messages.get_conversation_members(peer_id=peer_id)
                members_data = json.loads(members.json())
                owner = ACTIVATION_OWNER_ID
                for item in members_data.get('items', []):
                    if item.get('is_owner'):
                        owner = int(item['member_id'])
                        break
                await new_chat(chat_id, peer_id, owner, ACTIVATION_OWNER_ID)
                await message.answer(
                    f"Бот успешно активирован!\n"
                    f"[id{ACTIVATION_OWNER_ID}|Специальный руководитель] имеет полные права в этой беседе.\n"
                    f"Напишите /help для списка команд."
                )
            except Exception:
                await message.answer(
                    "Бот добавлен в беседу!\n"
                    "Выдайте боту звезду и напишите /start для активации."
                )
        else:
            await message.answer("Бот уже активирован в этой беседе!")
        return True

    if not await check_chat(chat_id): return True

    ud_check = await get_user_data(invited_user)
    if ud_check['global_ban'] and invited_user > 0:
        try:
            await bot.api.messages.remove_chat_user(chat_id, invited_user)
        except:
            pass
        gban_info = await get_global_ban_info(invited_user)
        info_u = await bot.api.users.get(user_ids=invited_user)
        uname = f"{info_u[0].first_name} {info_u[0].last_name}" if info_u else f"id{invited_user}"
        keyboard_gban = (
            Keyboard(inline=True)
            .add(Callback("Разблокировать", {"command": "ungbanpl", "user": invited_user}), color=KeyboardButtonColor.POSITIVE)
        )
        if gban_info:
            await message.answer(
                f"[id{invited_user}|{uname}], находится в общей блокировке!\n\n"
                f"Информация о блокировке:\n"
                f"[id{gban_info['moder_id']}|Модератор] | {gban_info['reason']} | {gban_info['date']}",
                disable_mentions=1, keyboard=keyboard_gban
            )
        else:
            await message.answer(f"⛔ [id{invited_user}|{uname}] имеет глобальный бан и был исключён из беседы!", disable_mentions=1, keyboard=keyboard_gban)
        return True

    elif user_id == invited_user:
        checkban_str = await checkban(invited_user, chat_id)
        if checkban_str:
            try:await bot.api.messages.remove_chat_user(chat_id, invited_user)
            except:pass
            info = await bot.api.users.get(invited_user)
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять бан", {"command": "unban", "user": invited_user, "chatId": chat_id}),color=KeyboardButtonColor.POSITIVE)
            )
            await message.answer(f"@id{invited_user} ({info[0].first_name}) заблокирован(-а) в этой беседе!\n\nИнформация о блокировке:\n@id{checkban_str['moder']} (Модератор) | {checkban_str['reason']} | {checkban_str['date']}",disable_mentions=1, keyboard=keyboard)
            return True

        welcome = await get_welcome(chat_id)
        if welcome:
            info = await bot.api.users.get(invited_user)
            invited_name = info[0].first_name if info else "Пользователь"
            welcome = welcome.replace('%u', f'@id{invited_user}')
            welcome = welcome.replace('%n', f'@id{invited_user} ({invited_name})')
            welcome = welcome.replace('%i', f'@id{user_id}')
            uinfo = await bot.api.users.get(user_id)
            inviter_name = uinfo[0].first_name if uinfo else "Пользователь"
            welcome = welcome.replace('%p', f'@id{user_id} ({inviter_name})')
            await message.answer(welcome)
    else:
        if await get_role(user_id, chat_id) < 1 and await invite_kick(chat_id):
            try: await bot.api.messages.remove_chat_user(chat_id, invited_user)
            except: pass
            return True

        checkban_str = await checkban(invited_user, chat_id)
        if checkban_str:
            try: await bot.api.messages.remove_chat_user(chat_id, invited_user)
            except: pass
            info = await bot.api.users.get(invited_user)
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять бан", {"command": "unban", "user": invited_user, "chatId": chat_id}),
                     color=KeyboardButtonColor.POSITIVE)
            )
            await message.answer(f"@id{invited_user} ({info[0].first_name}) заблокирован(-а) в этой беседе!\n\nИнформация о блокировке:\n@id{checkban_str['moder']} (Модератор) | {checkban_str['reason']} | {checkban_str['date']}", disable_mentions=1, keyboard=keyboard)
            return True

        welcome = await get_welcome(chat_id)
        if welcome:
            info = await bot.api.users.get(invited_user)
            invited_name = info[0].first_name if info else "Пользователь"
            uinfo = await bot.api.users.get(user_id)
            inviter_name = uinfo[0].first_name if uinfo else "Пользователь"
            welcome = welcome.replace('%u', f'@id{invited_user}')
            welcome = welcome.replace('%n', f'@id{invited_user} ({invited_name})')
            welcome = welcome.replace('%i', f'@id{user_id}')
            welcome = welcome.replace('%p', f'@id{user_id} ({inviter_name})')
            await message.answer(welcome)

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=GroupTypes.MessageEvent)
async def handlers(message: GroupTypes.MessageEvent):
    payload = message.object.payload
    command = str(payload.get("command")).lower()
    user_id = message.object.user_id
    chat_id = payload.get("chatId")
    if command == "delete_msg":
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        return True

    if command == "unpred_btn":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True
        
        target_user = payload.get("user")
        ud = await get_user_data(target_user)
        if ud['preds'] <= 0:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "У пользователя нет предов!"})
            )
            return True
            
        new_preds = ud['preds'] - 1
        await update_user_data(target_user, 'preds', new_preds)
        
        moder_name = await get_user_name(user_id, chat_id)
        target_name = await get_user_name(target_user, chat_id)
        
        moder_link = f"[id{user_id}|{moder_name}]"
        target_link = f"[id{target_user}|{target_name}]"
        
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"✅ {moder_link} снял предупреждение {target_link} ({new_preds}/2).",
            random_id=0,
            disable_mentions=1
        )
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        return True

    if command == "edit_field":
        if await get_role(user_id, chat_id) < 3:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True
            
        field = payload.get("field")
        target_user = payload.get("user")
        
        field_names = {
            "age": "Возраст",
            "has_pc": "Доступ к ПК",
            "discord": "Discord",
            "forum": "Forum",
            "points": "Баллы",
            "last_appointment": "Дата повышения"
        }
        
        command_map = {
            "age": "setage",
            "has_pc": "setpc",
            "discord": "setdiscord",
            "forum": "setforum",
            "points": "setpoints",
            "last_appointment": "setlast"
        }
        
        target_val = "20" if field == "age" else "1" if field == "has_pc" else "user#1234" if field == "discord" else "link" if field == "forum" else "100" if field == "points" else "2024-01-01"
        
        user_states[user_id] = {
            "field": field,
            "target_user": target_user,
            "chat_id": chat_id
        }
        
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"📝 Введите новое значение для поля «{field_names.get(field)}» для пользователя @id{target_user}.\n\n"
                    f"Чтобы отменить, введите «отмена».",
            random_id=0
        )

    if command == "form_add_q":
        if await get_role(user_id, chat_id) < 5:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
        user_states[user_id] = {"action": "form_add_q", "chat_id": chat_id}
        await bot.api.messages.send(peer_id=message.object.peer_id, message="📝 Введите новый вопрос для анкеты:", random_id=0)

    if command == "form_clear_q":
        if await get_role(user_id, chat_id) < 5:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
        sql.execute("UPDATE forms SET questions = ? WHERE chat_id = ?", ("[]", chat_id))
        database.commit()
        await bot.api.messages.send(peer_id=message.object.peer_id, message="🗑 Список вопросов очищен.", random_id=0)

    if command == "form_set_target":
        if await get_role(user_id, chat_id) < 5:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
        sql.execute("UPDATE forms SET target_peer_id = ? WHERE chat_id = ?", (message.object.peer_id, chat_id))
        database.commit()
        await bot.api.messages.send(peer_id=message.object.peer_id, message="🎯 Этот чат установлен как целевой для приема заявок.", random_id=0)

    if command == "form_set_title":
        if await get_role(user_id, chat_id) < 5:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
        user_states[user_id] = {"action": "form_set_title", "chat_id": chat_id}
        await bot.api.messages.send(peer_id=message.object.peer_id, message="📝 Введите новое название для анкеты:", random_id=0)

    if command == "form_toggle":
        if await get_role(user_id, chat_id) < 5:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
        val = payload.get("val")
        sql.execute("UPDATE forms SET is_open = ? WHERE chat_id = ?", (val, chat_id))
        database.commit()
        status = "открыт" if val else "закрыт"
        await bot.api.messages.send(peer_id=message.object.peer_id, message=f"✅ Прием заявок {status}!", random_id=0)

    if command == "view_app_btn":
        app_id = payload.get("id")
        sql.execute("SELECT user_id, answers, date, status FROM applications WHERE app_id = ?", (app_id,))
        app = sql.fetchone()
        if app:
            u_id, answers_json, date, status = app
            answers = json.loads(answers_json)
            sql.execute("SELECT questions FROM forms WHERE chat_id = ?", (chat_id,))
            questions = json.loads(sql.fetchone()[0])
            name = await get_user_name(u_id, chat_id)
            msg = f"📄 Заявка #{app_id} от [id{u_id}|{name}]\n📅 Дата: {date}\n📊 Статус: {status}\n\n"
            for i, q in enumerate(questions):
                ans = answers[i] if i < len(answers) else "Нет ответа"
                msg += f"❓ {q}\n— {ans}\n\n"
            
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("✅ Одобрить", {"command": "app_action", "id": app_id, "status": "approved", "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("❌ Отклонить", {"command": "app_action", "id": app_id, "status": "rejected", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
            )
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=keyboard, random_id=0)

    if command == "app_action":
        if await get_role(user_id, chat_id) < 3:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
        
        app_id = payload.get("id")
        new_status = payload.get("status")
        
        sql.execute("SELECT user_id, chat_id FROM applications WHERE app_id = ?", (app_id,))
        app_data = sql.fetchone()
        if not app_data:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Заявка не найдена!"})
            )
            
        target_uid, target_chat_id = app_data
        sql.execute("UPDATE applications SET status = ? WHERE app_id = ?", (new_status, app_id))
        database.commit()
        
        status_text = "одобрена" if new_status == "approved" else "отклонена"

        try:
            await bot.api.messages.send(
                user_id=target_uid,
                message=f"🔔 Ваша заявка #{app_id} была {status_text} администратором!",
                random_id=0
            )
        except: pass
        
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"✅ Статус заявки #{app_id} изменен на: {status_text}. Пользователь уведомлен.",
            random_id=0
        )
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        
    if command == "nicksminus":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True
        page = payload.get("page")
        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nicksMinus", "page": page - 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nicksPlus", "page": page - 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        nicks_str = '\n'.join(await nlist(chat_id, page-1))
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"Пользователи с ником [{page-1} страница]:\n{nicks_str}\n\nПользователи без ников: «/nonick»", disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "nicksplus":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")

        nicks = await nlist(chat_id, page + 1)
        if len(nicks) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nicksMinus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nicksPlus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        nicks_str = '\n'.join(nicks)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,message=f"Пользователи с ником [{page + 1} страница]:\n{nicks_str}\n\nПользователи без ников: «/nonick»",disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "nonicks":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        nonicks = await nonick(chat_id, 1)
        nonick_list = '\n'.join(nonicks)
        if nonick_list == "": nonick_list = "Пользователи без ников отсутствуют!"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nonickMinus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("С никами", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nonickPlus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(peer_id=2000000000+chat_id, message=f"Пользователи без ников [1]:\n{nonick_list}\n\nПользователи с никами: «/nlist»", disable_mentions=1, random_id=0 ,keyboard=keyboard)

    if command == "nicks":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        nicks = await nlist(chat_id, 1)
        nick_list = '\n'.join(nicks)
        if nick_list == "": nick_list = "Ники отсутствуют!"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nicksMinus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nicksPlus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(peer_id=2000000000+chat_id, message=f"Пользователи с ником [1 страница]:\n{nick_list}\n\nПользователи без ников: «/nonick»",
                            disable_mentions=1, keyboard=keyboard, random_id=0)

    if command == "nonickminus":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        page = payload.get("page")
        if page < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        nonicks = await nonick(chat_id, 1)
        nonick_list = '\n'.join(nonicks)
        if nonick_list == "": nonick_list = "Пользователи без ников отсутствуют!"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nonickMinus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("С никами", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nonickPlus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"Пользователи без ников [{page-1}]:\n{nonick_list}\n\nПользователи с никами: «/nlist»", disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "nonickplus":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True
        page = payload.get("page")
        nonicks = await nonick(chat_id, page+1)
        if len(nonicks) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        nonicks_str = '\n'.join(nonicks)
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,
                                    message=f"Пользователи без ников [{page + 1}]:\n{nonicks_str}\n\nПользователи с никами: «/nlist»",
                                    disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "clear":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        await clear(user, chat_id, message.group_id, 2000000000+chat_id)
        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000+chat_id, conversation_message_ids=message.object.conversation_message_id, group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x, conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) очистил(-а) сообщения", disable_mentions=1, random_id=0)

    if command == "unwarn":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        if await equals_roles(user_id, user, chat_id) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Вы не можете снять пред данному пользователю!"})
            )
            return True

        await unwarn(chat_id, user)
        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,conversation_message_ids=message.object.conversation_message_id,group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x, conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) снял(-а) предупреждение @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1, random_id=0)

    if command == 'stats':
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,conversation_message_id=message.object.conversation_message_id, keyboard=None)
        actual_owner_id = await sync_chat_owner_from_vk(chat_id, 2000000000 + chat_id, message.group_id)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=await get_full_stats_message(user, chat_id, actual_owner_id), disable_mentions=1, random_id=0)

    if command == "activewarns":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        warns = await gwarn(user, chat_id)
        string_info = str
        if not warns: string_info = "Активных предупреждений нет!"
        else: string_info = f"@id{warns['moder']} (Модератор) | {warns['reason']} | {warns['count']}/3 | {warns['time']}"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("История предупреждений", {"command": "warnhistory", "user": user, "chatId": chat_id}),
                 color=KeyboardButtonColor.PRIMARY)
        )

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}), информация о активных предупреждениях @id{user} (пользователя):\n{string_info}", disable_mentions=1, keyboard=keyboard, random_id=0)

    if command == "warnhistory":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")

        warnhistory_mass = await warnhistory(user, chat_id)
        if not warnhistory_mass:wh_string = "Предупреждений не было!"
        else:wh_string = '\n'.join(warnhistory_mass)

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"Информация о всех предупреждениях @id{user} ({await get_user_name(user, chat_id)})\nКоличество предупреждений пользователя: {await get_warns(user, chat_id)}\n\nИнформация о последних 10 предупреждений пользователя:\n{wh_string}",disable_mentions=1, random_id=0)

    if command == "unmute":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")

        if await get_role(user_id, chat_id) <= await get_role(user, chat_id):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        await unmute(user, chat_id)
        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,
                                    message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) размутил(-а) @id{user} ({await get_user_name(user, chat_id)})",
                                    disable_mentions=1, random_id=0)

    if command == "unban":
        if await get_role(user_id, chat_id) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        if await equals_roles(user_id, user, chat_id) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps(
                    {"type": "show_snackbar", "text": "Вы не можете снять бан данному пользователю!"})
            )
            return True

        await unban(user, chat_id)
        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,
                                    message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) разблокировал(-а) @id{user} ({await get_user_name(user, chat_id)})",
                                    disable_mentions=1, random_id=0)

    if command == "ungbanpl":
        if await get_priority(user_id, chat_id) < 80:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        target_user = payload.get("user")
        gb_info = await get_global_ban_info(target_user)
        if not gb_info:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "У пользователя нет глобального бана!"})
            )
            return True

        await remove_global_ban(target_user)

        x_gban = await bot.api.messages.get_by_conversation_message_id(
            peer_id=message.object.peer_id,
            conversation_message_ids=message.object.conversation_message_id,
            group_id=message.group_id
        )
        x_gban = json.loads(x_gban.json())['items'][0]['text']
        await bot.api.messages.edit(
            peer_id=message.object.peer_id, message=x_gban,
            conversation_message_id=message.object.conversation_message_id, keyboard=None
        )

        info_ungban = await bot.api.users.get(user_ids=target_user)
        full_name_ungban = f"{info_ungban[0].first_name} {info_ungban[0].last_name}" if info_ungban else f"id{target_user}"
        moder_name_ungban = await get_user_name(user_id, chat_id)

        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"✅ Глобальный бан снят с [id{target_user}|{full_name_ungban}]\nСнял(-а): [id{user_id}|{moder_name_ungban}]",
            disable_mentions=1, random_id=0
        )

    if command == "kick":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        user = payload.get("user")
        if await equals_roles(user_id, user, chat_id) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps(
                    {"type": "show_snackbar", "text": "Вы не можете кикнуть данного пользователя!"})
            )
            return True

        try: await bot.api.messages.remove_chat_user(chat_id, user)
        except: pass

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,
                                    message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) кикнул(-а) @id{user} ({await get_user_name(user, chat_id)})",
                                    disable_mentions=1, random_id=0)

    if command == "alt":
        if await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        commands_levels = {
            1: [
                '\nКоманды модераторов:',
                '/setnick — snick, nick, addnick, ник, сетник, аддник',
                '/removenick —  removenick, clearnick, cnick, рник, удалитьник, снятьник',
                '/getnick — gnick, гник, гетник',
                '/getacc — acc, гетакк, аккаунт, account',
                '/nlist — ники, всеники, nlist, nickslist, nicklist, nicks',
                '/nonick — nonicks, nonicklist, nolist, nnlist, безников, ноникс',
                '/kick — кик, исключить',
                '/warn — пред, варн, pred, предупреждение',
                '/unwarn — унварн, анварн, снятьпред, минуспред',
                '/getwarn — gwarn, getwarns, гетварн, гварн',
                '/warnhistory — historywarns, whistory, историяварнов, историяпредов',
                '/warnlist — warns, wlist, варны, варнлист',
                '/staff — стафф',
                '/reg — registration, regdate, рег, регистрация, датарегистрации',
                '/mute — мут, мьют, муте, addmute',
                '/unmute — снятьмут, анмут, унмут, снятьмут',
                '/alt — альт, альтернативные',
                '/getmute -- gmute, гмут, гетмут, чекмут',
                '/mutelist -- mutes, муты, мутлист',
                '/clear -- чистка, очистить, очистка',
                '/getban -- чекбан, гетбан, checkban',
                '/delete -- удалить'
            ],
            2: [
                '\nКоманды старших модераторов:',
                '/ban — бан, блокировка',
                '/unban -- унбан, снятьбан',
                '/addmoder -- moder',
                '/removerole -- rrole, снятьроль',
                '/zov - зов, вызов',
                '/online - ozov, озов',
                '/onlinelist - olist, олист',
                '/banlist - bans, банлист, баны',
                '/inactive - ilist, inactive',
                '/masskick - mkick'
            ],
            3: [
                '\nКоманды администраторов:',
                '/quiet -- silence, тишина',
                '/skick -- скик, снят',
                '/sban -- сбан',
                '/sunban — сунбан, санбан',
                '/addsenmoder — senmoder',
                '/rnickall -- allrnick, arnick, mrnick',
                '/sremovenick -- srnick',
                '/szov -- serverzov, сзов',
                '/srole -- prole, pullrole'
            ],
            4: [
                '\nКоманды старших администраторов:',
                '/addadmin -- admin',
                '/pullinfo -- pulli',
                '/banwords -- bws',
                '/filter -- none',
                '/sremoverole -- srrole'
            ],
            5: [
                '\nСписок команд владельца беседы',
                '/antiflood -- af',
                '/welcometext -- welcome, wtext',
                '/invite -- none',
                '/leave -- none',
                '/addsenadmin -- senadm, addsenadm, senadmin',
                '/setpull -- pull',
                '/setowner -- owner, владелец'
            ]
        }

        user_role = await get_role(user_id, chat_id)

        commands = []
        for i in commands_levels.keys():
            if i <= user_role:
                for b in commands_levels[i]:
                    commands.append(b)

        level_commands = '\n'.join(commands)

        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=f"Альтернативные команды\n\n{level_commands}",
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)


@bot.on.chat_message()
async def on_chat_message(message: Message):
    bot_identifiers = ['!', '+', '/']

    user_id = message.from_id
    chat_id = message.chat_id
    peer_id = message.peer_id
    arguments = message.text.split(' ')
    arguments_lower = message.text.lower().split(' ')

    if user_id < 0 and await check_chat(chat_id) and await get_protect(chat_id):
        try:
            await bot.api.messages.delete(
                group_id=message.group_id,
                peer_id=message.peer_id,
                delete_for_all=True,
                cmids=message.conversation_message_id
            )
        except: pass
        return True

    if user_id in user_states:
        state = user_states[user_id]
        if message.text.lower() == "отмена":
            del user_states[user_id]
            await message.reply("❌ Действие отменено.")
            return True
            
        action = state.get("action")
        if action == "form_add_q":
            chat_id_form = state["chat_id"]
            sql.execute("SELECT questions FROM forms WHERE chat_id = ?", (chat_id_form,))
            current_qs = json.loads(sql.fetchone()[0])
            current_qs.append(message.text)
            sql.execute("UPDATE forms SET questions = ? WHERE chat_id = ?", (json.dumps(current_qs, ensure_ascii=False), chat_id_form))
            database.commit()
            del user_states[user_id]
            await message.reply(f"✅ Вопрос добавлен! Всего вопросов: {len(current_qs)}")
            return True
            
        if action == "form_set_title":
            chat_id_form = state["chat_id"]
            sql.execute("UPDATE forms SET title = ? WHERE chat_id = ?", (message.text, chat_id_form))
            database.commit()
            del user_states[user_id]
            await message.reply(f"✅ Название анкеты изменено на: {message.text}")
            return True

        field = state.get("field")
        if field:
            target_user = state["target_user"]
            new_value = message.text

            if field == "has_pc":
                if new_value not in ["0", "1"]:
                    await message.reply("⚠️ Для поля ПК введите 1 (есть) или 0 (нет).")
                    return True
                new_value = int(new_value)
            elif field in ["age", "points"]:
                if not new_value.isdigit():
                    await message.reply("⚠️ Введите числовое значение.")
                    return True
                new_value = int(new_value)
                
            await update_user_data(target_user, field, new_value)
            del user_states[user_id]
            
            field_names = {
                "age": "Возраст", "has_pc": "Доступ к ПК", "discord": "Discord",
                "forum": "Forum", "points": "Баллы", "last_appointment": "Дата повышения"
            }
            await message.reply(f"✅ Поле «{field_names.get(field)}» для пользователя @id{target_user} успешно обновлено на: {new_value}")
            return True

    try:
        command_identifier = arguments[0].strip()[0]
        command = arguments_lower[0][1:]
    except:
        command_identifier = " "
        command = " "

    if command_identifier in bot_identifiers:
        command_message_key = (peer_id, getattr(message, "conversation_message_id", 0), command)
        now = time.time()
        for key, created_at in list(processed_command_messages.items()):
            if now - created_at > 300:
                processed_command_messages.pop(key, None)
        if command_message_key in processed_command_messages:
            return True
        processed_command_messages[command_message_key] = now

        try: test_admin = await bot.api.messages.get_conversation_members(peer_id=message.peer_id)
        except:
            await message.reply("Бот не будет работать без звезды в беседе!", disable_mentions=1)
            return True

        if await check_chat(chat_id):
            await sync_chat_owner_from_vk(chat_id, peer_id, message.group_id)
            if await get_mute(user_id, chat_id) and not await checkMute(chat_id, user_id):
                try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id)
                except: pass
                if await get_mute_warns(chat_id):
                    new_warns = await warn(chat_id, user_id, 0, 'Написание сообщений в муте')
                    await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) получил(-а) варн за написание в муте. Варнов: {new_warns}", disable_mentions=1)
                return True
            elif await check_quit(chat_id) and await get_role(user_id, chat_id) < 1:
                try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id)
                except: pass
                return True
            else:
                for i in await get_banwords(chat_id):
                    if i in message.text.lower() and await get_role(user_id, chat_id) < 1:
                        await mute(user_id, chat_id, 'Бот', 'Написание запрещенных слов', 30)
                        keyboard = (
                            Keyboard(inline=True)
                            .add(Callback("Снять мут", {"command": "unmute", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                        )
                        await message.reply(f"@id{user_id} (Пользователь) получил(-а) мут на 30 минут за написание запрещенного слова!")
                        try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id,delete_for_all=True, cmids=message.conversation_message_id)
                        except: pass
                        return True


        if command in ['start', 'старт', 'активировать']:
            if not await can_activate_bot(user_id):
                await message.reply(ACTIVATION_DENY_TEXT, disable_mentions=1)
                return True
            if await check_chat(chat_id):
                await message.reply("Бот уже активирован!", disable_mentions=1)
                return True
            try:
                owner = user_id
                try:
                    members_data = json.loads(test_admin.json())
                    for item in members_data.get('items', []):
                        if item.get('is_owner'):
                            owner = int(item['member_id'])
                            break
                except Exception:
                    pass
                if not await new_chat(chat_id, peer_id, owner, user_id):
                    await message.reply(ACTIVATION_DENY_TEXT, disable_mentions=1)
                    return True
                await message.reply("Бот успешно запущен!\nДля того, чтобы начать пользоваться им, напишите /help!", disable_mentions=1)
                return True
            except Exception as ex:
                import traceback
                print(f"[START ERROR] {traceback.format_exc()}")
                await message.reply(f"Ошибка при активации: {ex}", disable_mentions=1)
                return True

        if not await check_chat(chat_id):
            await message.reply(ACTIVATION_DENY_TEXT, disable_mentions=1)
            return True

        if command in ['id', 'ид', 'getid', 'гетид', 'получитьид', 'giveid']:
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                user = user_id
            if user < 0:
                await message.reply(f"ID сообщества: {abs(user)}\nСсылка: https://vk.com/club{abs(user)}", disable_mentions=1)
                return True
            await message.reply(f"ID пользователя: {user}\nСсылка: https://vk.com/id{user}", disable_mentions=1)

        if command in ['bottext', 'боттекст', 'написатьботом']:
            if int(user_id) != ACTIVATION_OWNER_ID:
                await message.reply("Эту команду может использовать только Специальный руководитель!", disable_mentions=1)
                return True
            text = await get_string(arguments, 1)
            if not text:
                await message.reply("📝 Использование: /bottext [текст]\nПример: /bottext Привет, беседа!", disable_mentions=1)
                return True
            await bot.api.messages.send(peer_id=message.peer_id, message=text, random_id=0)
            return True

        if message.reply_message and message.reply_message.from_id < 0:
            return True

        if command in ['newrole', 'создатьроль']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!")
                return True
            if len(arguments) < 3 or not arguments[1].isdigit():
                await message.reply("📝 Использование: /newrole [приоритет] [название]")
                return True
            priority = int(arguments[1])
            name = await get_string(arguments, 2)
            sql.execute("INSERT OR REPLACE INTO chat_roles (chat_id, name, priority) VALUES (?, ?, ?)", (chat_id, name, priority))
            database.commit()
            await message.reply(f"✅ Роль «{name}» с приоритетом {priority} создана!")

        if command in ['delrole', 'удалитьроль']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!")
                return True
            name = await get_string(arguments, 1)
            if not name:
                await message.reply("📝 Использование: /delrole [название]")
                return True
            sql.execute("DELETE FROM chat_roles WHERE chat_id = ? AND name = ?", (chat_id, name))
            sql.execute("DELETE FROM user_roles WHERE chat_id = ? AND role_name = ?", (chat_id, name))
            database.commit()
            await message.reply(f"✅ Роль «{name}» удалена!")

        if command in ['role', 'выдатьроль']:
            sender_priority = await get_priority(user_id, chat_id)
            if sender_priority < 100:
                await message.reply("Недостаточно прав!")
                return True
            user = int
            arg_idx = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg_idx = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg_idx = 1
            elif len(arguments) >= 3 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg_idx = 2
            else:
                await message.reply("📝 Использование: /role [пользователь] [название/приоритет]")
                return True

            if await is_chat_owner(user, chat_id):
                await message.reply("❌ Нельзя выдавать или менять роль владельцу беседы!")
                return True
            
            role_input = await get_string(arguments, arg_idx)
            if not role_input:
                await message.reply("📝 Укажите название или приоритет роли!")
                return True
            
            if role_input.isdigit():
                sql.execute("SELECT name, priority FROM chat_roles WHERE chat_id = ? AND priority = ?", (chat_id, int(role_input)))
            else:
                sql.execute("SELECT name, priority FROM chat_roles WHERE chat_id = ? AND name = ?", (chat_id, role_input))
                
            role_data = sql.fetchone()
            if not role_data:
                await message.reply("❌ Такой роли не существует!")
                return True
            
            role_name = role_data[0]
            role_priority = role_data[1]
            
            if role_priority >= sender_priority and sender_priority < 200:
                await message.reply("❌ Вы не можете выдать роль с приоритетом выше или равным вашему!")
                return True
                
            # Снимаем возможную старую "стандартную" роль (модератор/ст.модератор/
            # админ/ст.админ), чтобы не было двух одновременно активных ролей
            # у одного пользователя — иначе get_role будет непредсказуемо
            # выбирать то одну, то другую систему прав.
            sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = ?", (user,))
            sql.execute("INSERT OR REPLACE INTO user_roles (chat_id, user_id, role_name) VALUES (?, ?, ?)", (chat_id, user, role_name))
            database.commit()
            await message.reply(f"✅ Пользователю @id{user} выдана роль «{role_name}» (Приоритет: {role_priority})!")

        if command in ['roles', 'роли']:
            sql.execute("SELECT name, priority FROM chat_roles WHERE chat_id = ?", (chat_id,))
            fetch = sql.fetchall()
            if not fetch:
                await message.reply("В этой беседе нет кастомных ролей.")
                return True
            msg = "🎭 Список ролей в беседе:\n"
            for r in fetch:
                msg += f"— {r[0]} (Приоритет: {r[1]})\n"
            await message.reply(msg)

        if command in ['editcmd', 'редкоманду']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!")
                return True
            if len(arguments) < 3 or not arguments[2].isdigit():
                await message.reply("📝 Использование: /editcmd [команда] [приоритет]")
                return True
            cmd_name = arguments[1].lower().replace('/', '')
            priority = int(arguments[2])
            sql.execute("INSERT OR REPLACE INTO command_perms (chat_id, command, priority) VALUES (?, ?, ?)", (chat_id, cmd_name, priority))
            database.commit()
            await message.reply(f"✅ Для команды «/{cmd_name}» установлен минимальный приоритет: {priority}")

        if command in ['givecmd', 'выдатькоманду']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!")
                return True
            user = int
            arg_idx = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg_idx = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg_idx = 1
            elif len(arguments) >= 3 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg_idx = 2
            else:
                await message.reply("📝 Использование: /givecmd [пользователь] [команда]")
                return True
            cmd_name = arguments[arg_idx].lower().replace('/', '')
            sql.execute("INSERT OR REPLACE INTO user_commands (chat_id, user_id, command) VALUES (?, ?, ?)", (chat_id, user, cmd_name))
            database.commit()
            await message.reply(f"✅ Пользователю @id{user} выдана персональная команда «/{cmd_name}»!")

        if command in ['uncmd', 'забратькоманду']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!")
                return True
            user = int
            arg_idx = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg_idx = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg_idx = 1
            elif len(arguments) >= 3 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg_idx = 2
            else:
                await message.reply("📝 Использование: /uncmd [пользователь] [команда]")
                return True
            cmd_name = arguments[arg_idx].lower().replace('/', '')
            sql.execute("DELETE FROM user_commands WHERE chat_id = ? AND user_id = ? AND command = ?", (chat_id, user, cmd_name))
            database.commit()
            await message.reply(f"✅ У пользователя @id{user} отозвана команда «/{cmd_name}»!")

        if command in ['givecmds', 'списоккоманд']:
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                user = user_id
            sql.execute("SELECT command FROM user_commands WHERE chat_id = ? AND user_id = ?", (chat_id, user))
            fetch = sql.fetchall()
            if not fetch:
                await message.reply("У пользователя нет персональных команд.")
                return True
            cmds = ", ".join([f"/{i[0]}" for i in fetch])
            await message.reply(f"📜 Персональные команды @id{user}:\n{cmds}")

        if command in ['stats', 'стата', 'статистика', 'stata', 'statistic']:
            user = int

            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0: user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else: user = user_id

            if user < 0:
                await message.reply("Нельзя взаимодействовать с сообществом!")
                return True

            info = await bot.api.users.get(user_ids=user)
            if not info:
                await message.reply("Не удалось получить информацию о пользователе. Возможно, страница удалена или заблокирована.")
                return True

            actual_owner_id = await sync_chat_owner_from_vk(chat_id, peer_id, message.group_id)
            await message.reply(await get_full_stats_message(user, chat_id, actual_owner_id), disable_mentions=1)

        if command in ['editstats', 'редстатс']:
            if not await check_perm(user_id, chat_id, command, 3):
                await message.reply("Недостаточно прав!")
                return True
                
            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0: user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else: user = user_id
            
            ud = await get_user_data(user)
            
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⚡ Возраст", {"command": "edit_field", "field": "age", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("💻 ПК", {"command": "edit_field", "field": "has_pc", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .row()
                .add(Callback("📘 Discord", {"command": "edit_field", "field": "discord", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("📕 Forum", {"command": "edit_field", "field": "forum", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .row()
                .add(Callback("💲 Баллы", {"command": "edit_field", "field": "points", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("⤴️ Повышение", {"command": "edit_field", "field": "last_appointment", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            )
            
            await message.reply(f"Редактирование статистики пользователя @id{user}:\n\n"
                                f"⚡ Возраст — {ud['age']} лет\n"
                                f"💻 Доступ к ПК — {'Есть' if ud['has_pc'] else 'Нет'}\n"
                                f"📘 Discord — {ud['discord']}\n"
                                f"📕 Forum — {ud['forum']}\n"
                                f"🆔 VK ID — {user}", 
                                keyboard=keyboard)

        if command in ['setage', 'setpc', 'setdiscord', 'setforum', 'setpoints', 'setlast']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!")
                return True
            
            user = int
            arg_idx = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg_idx = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg_idx = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg_idx = 2
            else:
                user = user_id
                arg_idx = 1
                
            value = await get_string(arguments, arg_idx)
            if not value:
                await message.reply("Укажите значение!")
                return True
                
            field_map = {
                'setage': 'age',
                'setpc': 'has_pc',
                'setdiscord': 'discord',
                'setforum': 'forum',
                'setpoints': 'points',
                'setlast': 'last_appointment'
            }
            
            field = field_map.get(command)
            await update_user_data(user, field, value)
            await message.reply(f"Значение поля {field} для @id{user} успешно обновлено на {value}!")

        if command in ['pred', 'пред']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!")
                return True
            
            user = int
            arg_idx = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg_idx = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg_idx = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg_idx = 2
            else:
                await message.reply("Укажите пользователя!")
                return True
            
            if user < 0:
                await message.reply("Нельзя выдавать предупреждения сообществам!")
                return True

            if await equals_roles(user_id, user, chat_id) == 0:
                await message.reply("Вы не можете выдавать предупреждения этому пользователю!")
                return True

            reason = await get_string(arguments, arg_idx) or "Причина не указана"
            ud = await get_user_data(user)
            new_preds = ud['preds'] + 1
            
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять пред", {"command": "unpred_btn", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("Очистить", {"command": "delete_msg"}), color=KeyboardButtonColor.SECONDARY)
            )

            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"

            if new_preds >= 2:
                await update_user_data(user, 'preds', 0)
                await warn(chat_id, user, user_id, f"[Авто-выговор за 2/2 предов] {reason}")
                await message.answer(f"{moder_link} выдал(-а) предупреждение {target_link}\n"
                                     f"Причина: {reason}\n"
                                     f"Количество предупреждений: 2/2\n\n"
                                     f"❗ Пользователь получил 1 выговор за накопление 2-х предупреждений.", 
                                     disable_mentions=1, keyboard=keyboard)
            else:
                await update_user_data(user, 'preds', new_preds)
                await message.answer(f"{moder_link} выдал(-а) предупреждение {target_link}\n"
                                     f"Причина: {reason}\n"
                                     f"Количество предупреждений: {new_preds}", 
                                     disable_mentions=1, keyboard=keyboard)

        if command in ['unpred', 'снятьпред']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!")
                return True
            
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!")
                return True
            
            ud = await get_user_data(user)
            if ud['preds'] <= 0:
                await message.reply("У пользователя нет предупреждений!")
                return True
                
            new_preds = ud['preds'] - 1
            await update_user_data(user, 'preds', new_preds)
            
            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"
            
            await message.answer(f"✅ {moder_link} снял предупреждение {target_link} ({new_preds}/2).", disable_mentions=1)

        if command in ['clearpreds', 'очиститьпреды']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!")
                return True
            
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!")
                return True
            
            await update_user_data(user, 'preds', 0)
            
            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"
            
            await message.answer(f"🧹 {moder_link} полностью очистил предупреждения {target_link}.", disable_mentions=1)

        if command in ['help', 'помощь', 'хелп', 'команды', 'commands']:
            commands_levels = {
                0: [
                    'Команды пользователей:',
                    '/info -- официальные ресурсы проекта',
                    '/stats -- информация о пользователе',
                    '/getid -- узнать оригинальный ID пользователя в ВК',
                    '/приз -- получить ежедневный бонус',
                    '/баланс -- посмотреть свой баланс',
                    '/дуэль -- вызвать пользователя на дуэль (подтверждается через /yes или /no)',
                    '/передать -- передать монеты другому пользователю',
                    '/топ -- топ самых богатых пользователей',
                    '/положить -- положить деньги в банк',
                    '/снять -- снять деньги с банка',
                    '/благо -- отправить монеты в благотворительность',
                    '/топблаго -- топ отправителей монет в благотворительность',
                    '/buyvip -- купить вип статус',
                    '/промо -- получить бонус',
                    '/открытьдепозит -- открыть депозит (для вип)',
                    '/закрытьдепозит -- закрыть депозит (для вип)',
                    '/правила -- посмотреть правила бота',
                    '/рулетка -- сыграть в рулетку',
                    '/купитьбиз -- купить бизнес',
                    '/бизнес -- посмотреть статистику бизнесов',
                    '/ппрод -- пополнить продукты в бизнесы',
                    '/снятьбиз -- снять деньги с бизнесов',
                    '/топбиз -- топ бизсменов',
                    '/проверка -- проверить сайт на безопастность',
                    '/аукцион'
                ],
                1: [
                    '\nКоманды модераторов:',
                    '/kick -- исключить пользователя из беседы',
                    '/mute -- замутить пользователя',
                    '/unmute -- размутить пользователя',
                    '/warn -- выдать предупреждение пользователю',
                    '/unwarn -- снять предупреждение пользователю',
                    '/getban -- информация о банах пользователя',
                    '/getwarn -- информация о активных предупреждениях пользователя',
                    '/warnhistory -- информация о всех предупреждениях пользователя',
                    '/staff -- пользователи с ролями',
                    '/setnick -- сменить ник у пользователя',
                    '/removenick -- очистить ник у пользователя',
                    '/nlist -- посмотреть ники пользователей',
                    '/nonick -- пользователи без ников',
                    '/getnick -- проверить ник пользователя',
                    '/alt -- узнать альтернативные команды',
                    '/getacc -- узнать пользователя по нику',
                    '/warnlist -- список пользователей с варном',
                    '/clear -- очистить сообщения',
                    '/mutelist -- список пользователей с мутом',
                    '/mutelogs -- логи мутов',
                    '/mclear -- удалить несколько сообщений',
                    '/getbynick -- поиск по части ника'
                ],
                2: [
                    '\nКоманды старших модераторов:',
                    '/ban -- заблокировать пользователя в беседе',
                    '/unban -- разблокировать пользователя в беседе',
                    '/addmoder -- выдать пользователю модератора',
                    '/removerole -- забрать роль у пользователя',
                    '/zov -- упомянуть всех пользователей',
                    '/online -- упомянуть пользователей онлайн',
                    '/onlinelist -- посмотреть пользователей в онлайн',
                    '/banlist -- посмотреть заблокированных',
                    '/inactivelist -- список неактивных пользователей',
                    '/bug -- сообщить о баге разработчику',
                    '/nickhistory -- посмотреть историю ников',
                    '/логадм -- посмотреть логи админ прав',
                    '/invlogs -- посмотреть логи приглашений в чат'
                ],
                3: [
                    '\nСписок команд администраторов:',
                    '/quiet -- включить/выключить режим тишины',
                    '/addsenmoder -- дать пользователю роль старшего модератора',
                    '/rnickall -- очистить все ники в беседе',
                    '/sremovenick -- очистить ник у пользователя в беседах сервера',
                    '/ssetnick -- поставить ник пользователю в беседах сервера',
                    '/skick -- исключить пользователя с бесед сервера',
                    '/логбан -- посмотреть логи блокировок',
                    '/логкик -- посмотреть логи киков'
                ],
                4: [
                    '\nСписок команд старших администраторов:',
                    '/addadmin -- дать пользователю роль администратора',
                    '/serverinfo -- информация о сервере',
                    '/filter -- фильтр запрещенных слов',
                    '/sban -- заблокировать пользователя в беседах сервера',
                    '/sunban -- разбанить пользователя в беседах сервера',
                    '/szov -- вызов участников в беседах сервера',
                    '/srole -- выдать права в беседах сервера',
                    '/sremoverole -- забрать роль у пользователя в сетке бесед'
                ],
                5: [
                    '\nСписок команд зама спец администратора:',
                    '/addsenadmin -- дать пользователю роль старшего администратора',
                    '/sync -- синхронизация с базой данных',
                    '/pin -- закрепить сообщение',
                    '/unpin -- открепить сообщение',
                    '/gsrnick -- очистить ник у пользователя в беседах привязки',
                    '/gssnick -- поставить ник пользователю в беседах привязки',
                    '/gskick -- исключить пользователя с бесед привязки',
                    '/gsban -- заблокировать пользователя в беседах привязки',
                    '/gsunban -- разбанить пользователя в беседах привязки'
                ],
                6: [
                    '\nСписок команд спец администратора:',
                    '/addzsa (/addza) -- выдать права зам. спец администратора',
                    '/server -- привязать беседу к серверу',
                    '/settings -- показать настройки беседы',
                    '/clearwarn -- снять варны пользователям, которые отсутствуют в чате',
                    '/title -- изменить название беседы',
                    '/srroleall -- очистить все роли во всех беседах сервера',
                    '/srnickal (/srnickall) -- очистить все ники во всех беседах сервера'
                ],
                7: [
                    '\nСписок команд владельца беседы:',
                    '/addsa -- выдать права спец администратора',
                    '/antiflood -- режим защиты от спама',
                    '/welcometext -- текст приветствия',
                    '/invite -- система добавления пользователей только модераторами',
                    '/leave -- система исключения пользователей при выходе',
                    '/masskick -- исключить всех участников без ролей',
                    '/demote -- исключить участников без ролей',
                    '/rroleall -- очистить все роли в беседе',
                    '/gsync -- включить глобальную синхронизацию бесед',
                    '/gunsync -- отключить глобальную синхронизацию бесед',
                    '/settingsmute -- включить/выключить выдачу варнов за сообщения в муте',
                    '/защита -- защита от сторонних сообществ',
                    '/миниигры -- включить/выключить мини-игры в чате',
                    '/владелец -- передать права владельца бота в беседе'
                ]
            }

            user_role = await get_role(user_id, chat_id)

            if user_role > 1:
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Альтернативные команды", {"command": "alt", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                )
            else:
                keyboard = None

            commands = []
            for i in commands_levels.keys():
                if i <= user_role:
                    for b in commands_levels[i]:
                        commands.append(b)

            level_commands = '\n'.join(commands)

            await message.reply(f"{level_commands}", disable_mentions=1, keyboard=keyboard)
            return True

        if command in ['info', 'инфо']:
            await message.reply("Официальные ресурсы проекта:\nVK: https://vk.com\nСайт: не указан\nПоддержка: напишите администрации беседы", disable_mentions=1)

        if command in ['правила', 'rules']:
            await message.reply("Правила бота:\n1) Не спамить командами.\n2) Не использовать баги экономики.\n3) Не оскорблять участников.\n4) Решения администрации обязательны к выполнению.", disable_mentions=1)

        if command in ['приз', 'bonus', 'daily']:
            data = await get_economy(user_id)
            if data['last_prize'] == today_string():
                await message.reply("Вы уже получали ежедневный бонус сегодня.", disable_mentions=1)
                return True
            prize = random.randint(500, 2500)
            if await is_vip(user_id):
                prize *= 2
            await update_economy(user_id, 'last_prize', today_string())
            await add_balance(user_id, prize)
            await message.reply(f"Вы получили ежедневный бонус: {prize} монет.", disable_mentions=1)

        if command in ['баланс', 'balance', 'bal']:
            data = await get_economy(user_id)
            vip = await is_vip(user_id)
            business = await get_business(user_id)
            biz_count = business['level']

            if vip:
                try:
                    vip_dt = datetime.fromisoformat(data['vip_until'])
                    delta = vip_dt - datetime.now()
                    total_sec = max(0, int(delta.total_seconds()))
                    vip_days = total_sec // 86400
                    vip_hours = (total_sec % 86400) // 3600
                    vip_mins = (total_sec % 3600) // 60
                    vip_time_str = f"{vip_days}д {vip_hours}ч {vip_mins}м"
                except:
                    vip_time_str = "—"
                vip_status = "VIP"
            else:
                vip_status = "Обычный"
                vip_time_str = None

            transfer_limit = "безлимит" if vip else "ограничен"

            dep_lines = ""
            if data['deposit_amount'] > 0:
                try:
                    dep_opened = datetime.fromisoformat(data['deposit_opened'])
                    dep_days_total = data['deposit_days']
                    dep_percent = data['deposit_percent']
                    dep_end = dep_opened + timedelta(days=dep_days_total)
                    dep_left_sec = max(0, int((dep_end - datetime.now()).total_seconds()))
                    dep_left_d = dep_left_sec // 86400
                    dep_left_h = (dep_left_sec % 86400) // 3600
                    dep_left_m = (dep_left_sec % 3600) // 60
                    dep_lines = (
                        f"\n💵 Депозит:\n{data['deposit_amount']}$ на {dep_days_total} дн. под {dep_percent}%"
                        f"\n⏳ До вывода: {dep_left_d}д {dep_left_h}ч {dep_left_m}м"
                    )
                except:
                    dep_lines = f"\n💵 Депозит: {data['deposit_amount']}$"

            lines = [
                f"💰 У тебя {data['balance']}$",
                f"🏦 Счет в банке: {data['bank']}$",
                f"🏆 Дуэлей выиграно: {data['duel_wins']}",
                f"💔 Дуэлей проиграно: {data['duel_losses']}",
                f"🎉 Всего выиграно:\n{data['total_won']}$",
                f"💸 Всего проиграно:\n{data['total_lost']}$",
                f"🎰 Рулетка выиграно:\n{data['roulette_won']}$",
                f"🎮 Рулетка проиграно:\n{data['roulette_lost']}$",
                f"📤 Отправлено переводами:\n{data['sent_transfers']}$",
                f"📥 Получено переводами:\n{data['received_transfers']}$",
                f"⭐ Статус: {vip_status}",
            ]
            if vip and vip_time_str:
                lines.append(f"⏳ До окончания статуса:\n{vip_time_str}")
            lines.append(f"🏅 У вас {biz_count} бизнес(-ов)")
            lines.append(f"🔄 Лимит переводов: {transfer_limit}")
            await message.reply("\n".join(lines) + dep_lines, disable_mentions=1)

        if command in ['givemoney', 'выдатьмонеты', 'добавитьмонеты']:
            if await get_role(user_id, chat_id) < 9 and int(user_id) != ACTIVATION_OWNER_ID:
                await message.reply("Недостаточно прав! Нужна роль Зам.спец администратора или выше.", disable_mentions=1)
                return True
            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif len(arguments) >= 3 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("📝 Использование: /givemoney [пользователь] [сумма]\nПример: /givemoney @id123 500", disable_mentions=1)
                return True
            amount = parse_money(arguments[arg]) if len(arguments) > arg else False
            if not amount or amount < 1:
                await message.reply("Укажите корректную сумму (больше 0).", disable_mentions=1)
                return True
            if user < 1:
                await message.reply("Укажите корректного пользователя.", disable_mentions=1)
                return True
            await add_balance(user, amount)
            database.commit()
            target_data = await get_economy(user)
            await message.reply(
                f"✅ [id{user}|Пользователю] выдано {amount} монет.\n"
                f"Новый баланс: {target_data['balance']} монет.",
                disable_mentions=1
            )

        if command in ['takemoney', 'забратьмонеты']:
            if await get_role(user_id, chat_id) < 9 and int(user_id) != ACTIVATION_OWNER_ID:
                await message.reply("Недостаточно прав! Нужна роль Зам.спец администратора или выше.", disable_mentions=1)
                return True
            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif len(arguments) >= 3 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("📝 Использование: /takemoney [пользователь] [сумма]\nПример: /takemoney @id123 500", disable_mentions=1)
                return True
            amount = parse_money(arguments[arg]) if len(arguments) > arg else False
            if not amount or amount < 1:
                await message.reply("Укажите корректную сумму (больше 0).", disable_mentions=1)
                return True
            if user < 1:
                await message.reply("Укажите корректного пользователя.", disable_mentions=1)
                return True
            target_data = await get_economy(user)
            new_balance = max(0, target_data['balance'] - amount)
            await update_economy(user, 'balance', new_balance)
            database.commit()
            await message.reply(
                f"✅ У [id{user}|пользователя] снято {amount} монет.\n"
                f"Новый баланс: {new_balance} монет.",
                disable_mentions=1
            )

        if command in ['положить', 'bank', 'депозитбанк']:
            if len(arguments) < 2:
                await message.reply("Использование: /положить [сумма]", disable_mentions=1)
                return True
            amount = parse_money(arguments[1])
            data = await get_economy(user_id)
            if not amount or data['balance'] < amount:
                await message.reply("Недостаточно средств или неверная сумма.", disable_mentions=1)
                return True
            await update_economy(user_id, 'balance', data['balance'] - amount)
            await update_economy(user_id, 'bank', data['bank'] + amount)
            await message.reply(f"Вы положили в банк {amount} монет.", disable_mentions=1)

        if command in ['снять', 'withdraw']:
            if len(arguments) < 2:
                await message.reply("Использование: /снять [сумма]", disable_mentions=1)
                return True
            amount = parse_money(arguments[1])
            data = await get_economy(user_id)
            if not amount or data['bank'] < amount:
                await message.reply("Недостаточно средств в банке или неверная сумма.", disable_mentions=1)
                return True
            await update_economy(user_id, 'bank', data['bank'] - amount)
            await update_economy(user_id, 'balance', data['balance'] + amount)
            await message.reply(f"Вы сняли из банка {amount} монет.", disable_mentions=1)

        if command in ['передать', 'transfer', 'pay']:
            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif len(arguments) >= 3 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Использование: /передать [пользователь] [сумма]", disable_mentions=1)
                return True
            amount = parse_money(arguments[arg]) if len(arguments) > arg else False
            data = await get_economy(user_id)
            if user < 1 or not amount or data['balance'] < amount:
                await message.reply("Передача невозможна: проверьте пользователя, сумму и баланс.", disable_mentions=1)
                return True
            await update_economy(user_id, 'balance', data['balance'] - amount)
            await update_economy(user_id, 'sent_transfers', data['sent_transfers'] + amount)
            await add_balance(user, amount)
            recv_data = await get_economy(user)
            await update_economy(user, 'received_transfers', recv_data['received_transfers'] + amount)
            database.commit()
            await message.reply(f"Вы передали [id{user}|пользователю] {amount} монет.", disable_mentions=1)

        if command in ['дуэль', 'duel']:
            # Раньше дуэль решалась мгновенно, без согласия оппонента: любой
            # мог написать /дуэль @оппонент сумма — и деньги у оппонента
            # списывались/начислялись без его подтверждения (даже если он не
            # хотел рисковать монетами и не видел сообщения). Теперь сначала
            # отправляется предложение, и оппонент должен явно подтвердить
            # его командой /yes (согласиться на рандомайзер) или /no
            # (отказаться) — только после этого проводится случайный розыгрыш.
            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif len(arguments) >= 3 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Использование: /дуэль [пользователь] [ставка]", disable_mentions=1)
                return True
            amount = parse_money(arguments[arg]) if len(arguments) > arg else False
            data = await get_economy(user_id)
            opponent = await get_economy(user)
            if user == user_id or user < 1 or not amount or data['balance'] < amount or opponent['balance'] < amount:
                await message.reply("Дуэль невозможна: проверьте участника, ставку и балансы.", disable_mentions=1)
                return True

            now_ts = time.time()
            existing = await get_pending_duel(chat_id, user)
            if existing and now_ts - existing['created_at'] < DUEL_TIMEOUT_SECONDS:
                await message.reply("У этого пользователя уже есть неотвеченный вызов на дуэль в этой беседе!", disable_mentions=1)
                return True

            await set_pending_duel(chat_id, user, user_id, amount)
            await message.reply(
                f"⚔️ @id{user_id} ({await get_user_name(user_id, chat_id)}) вызывает "
                f"@id{user} ({await get_user_name(user, chat_id)}) на дуэль на ставку {amount} монет!\n\n"
                f"@id{user}, чтобы принять вызов и запустить рандомайзер — напишите /yes.\n"
                f"Чтобы отказаться — напишите /no.\n"
                f"Вызов действует 5 минут.",
                disable_mentions=1
            )
            return True

        if command in ['yes', 'да']:
            pending = await get_pending_duel(chat_id, user_id)
            if not pending:
                return True

            if time.time() - pending['created_at'] > DUEL_TIMEOUT_SECONDS:
                await delete_pending_duel(chat_id, user_id)
                await message.reply("⌛ Вызов на дуэль просрочен.", disable_mentions=1)
                return True

            challenger = pending['challenger']
            amount = pending['amount']
            await delete_pending_duel(chat_id, user_id)

            # Перепроверяем балансы на момент подтверждения — они могли
            # измениться с момента вызова.
            challenger_data = await get_economy(challenger)
            target_data = await get_economy(user_id)
            if challenger_data['balance'] < amount or target_data['balance'] < amount:
                await message.reply("Дуэль отменена: у одного из участников не хватает монет на ставку.", disable_mentions=1)
                return True

            winner = random.choice([challenger, user_id])
            loser = user_id if winner == challenger else challenger
            loser_data = await get_economy(loser)
            winner_data = await get_economy(winner)
            await update_economy(loser, 'balance', loser_data['balance'] - amount)
            await update_economy(winner, 'balance', winner_data['balance'] + amount)
            await update_economy(winner, 'duel_wins', winner_data['duel_wins'] + 1)
            await update_economy(winner, 'total_won', winner_data['total_won'] + amount)
            await update_economy(loser, 'duel_losses', loser_data['duel_losses'] + 1)
            await update_economy(loser, 'total_lost', loser_data['total_lost'] + amount)
            database.commit()
            await message.reply(
                f"⚔️ Дуэль принята! Победитель: [id{winner}|пользователь]. Выигрыш: {amount} монет.",
                disable_mentions=1
            )
            return True

        if command in ['no', 'нет']:
            pending = await get_pending_duel(chat_id, user_id)
            if not pending:
                return True

            await delete_pending_duel(chat_id, user_id)
            await message.reply(
                f"❌ @id{user_id} ({await get_user_name(user_id, chat_id)}) отказался(-ась) от дуэли.",
                disable_mentions=1
            )
            return True

        if command in ['рулетка', 'roulette']:
            if len(arguments) < 2:
                await message.reply("Использование: /рулетка [ставка]", disable_mentions=1)
                return True
            amount = parse_money(arguments[1])
            data = await get_economy(user_id)
            if not amount or data['balance'] < amount:
                await message.reply("Недостаточно средств или неверная ставка.", disable_mentions=1)
                return True
            if random.randint(1, 100) <= 48:
                await update_economy(user_id, 'balance', data['balance'] + amount)
                await update_economy(user_id, 'roulette_won', data['roulette_won'] + amount)
                await update_economy(user_id, 'total_won', data['total_won'] + amount)
                database.commit()
                await message.reply(f"Вы выиграли {amount} монет.", disable_mentions=1)
            else:
                await update_economy(user_id, 'balance', data['balance'] - amount)
                await update_economy(user_id, 'roulette_lost', data['roulette_lost'] + amount)
                await update_economy(user_id, 'total_lost', data['total_lost'] + amount)
                database.commit()
                await message.reply(f"Вы проиграли {amount} монет.", disable_mentions=1)

        if command in ['топ', 'top']:
            sql.execute("SELECT user_id, balance, bank FROM economy ORDER BY (balance + bank) DESC LIMIT 10")
            rows = sql.fetchall()
            if not rows:
                await message.reply("Топ пока пуст.", disable_mentions=1)
                return True
            text = ["Топ самых богатых пользователей:"]
            for index, row in enumerate(rows, 1):
                text.append(f"{index}) [id{row[0]}|Пользователь] — {row[1] + row[2]} монет")
            await message.reply("\n".join(text), disable_mentions=1)

        if command in ['благо', 'charity']:
            if len(arguments) < 2:
                await message.reply("Использование: /благо [сумма]", disable_mentions=1)
                return True
            amount = parse_money(arguments[1])
            data = await get_economy(user_id)
            if not amount or data['balance'] < amount:
                await message.reply("Недостаточно средств или неверная сумма.", disable_mentions=1)
                return True
            await update_economy(user_id, 'balance', data['balance'] - amount)
            await update_economy(user_id, 'charity', data['charity'] + amount)
            await message.reply(f"Вы отправили {amount} монет в благотворительность.", disable_mentions=1)

        if command in ['топблаго', 'topcharity']:
            sql.execute("SELECT user_id, charity FROM economy WHERE charity > 0 ORDER BY charity DESC LIMIT 10")
            rows = sql.fetchall()
            if not rows:
                await message.reply("Топ благотворительности пока пуст.", disable_mentions=1)
                return True
            text = ["Топ отправителей монет в благотворительность:"]
            for index, row in enumerate(rows, 1):
                text.append(f"{index}) [id{row[0]}|Пользователь] — {row[1]} монет")
            await message.reply("\n".join(text), disable_mentions=1)

        if command in ['buyvip', 'купитьвип']:
            price = 50000
            data = await get_economy(user_id)
            if data['balance'] < price:
                await message.reply(f"VIP стоит {price} монет. У вас недостаточно средств.", disable_mentions=1)
                return True
            vip_until = datetime.now() + timedelta(days=30)
            await update_economy(user_id, 'balance', data['balance'] - price)
            await update_economy(user_id, 'vip_until', vip_until.isoformat())
            await message.reply(f"VIP статус куплен на 30 дней. До: {vip_until.strftime('%d.%m.%Y')}", disable_mentions=1)

        if command in ['промо', 'promo']:
            if len(arguments) < 2:
                await message.reply("Использование: /промо [код]", disable_mentions=1)
                return True
            code = arguments[1].lower()
            sql.execute("SELECT amount, activations FROM promo_codes WHERE code = ?", (code,))
            promo = sql.fetchone()
            if not promo:
                await message.reply("Такой промокод не найден.", disable_mentions=1)
                return True
            sql.execute("SELECT 1 FROM promo_uses WHERE code = ? AND user_id = ?", (code, user_id))
            if sql.fetchone():
                await message.reply("Вы уже активировали этот промокод.", disable_mentions=1)
                return True
            if promo[1] <= 0:
                await message.reply("Активации этого промокода закончились.", disable_mentions=1)
                return True
            await add_balance(user_id, promo[0])
            sql.execute("UPDATE promo_codes SET activations = activations - 1 WHERE code = ?", (code,))
            sql.execute("INSERT INTO promo_uses VALUES (?, ?)", (code, user_id))
            database.commit()
            await message.reply(f"Промокод активирован. Начислено {promo[0]} монет.", disable_mentions=1)

        if command in ['открытьдепозит', 'opendeposit']:
            if not await is_vip(user_id):
                await message.reply("Депозит доступен только VIP пользователям.", disable_mentions=1)
                return True
            if len(arguments) < 2:
                await message.reply("Использование: /открытьдепозит [сумма]", disable_mentions=1)
                return True
            amount = parse_money(arguments[1])
            data = await get_economy(user_id)
            if data['deposit_amount'] > 0:
                await message.reply("У вас уже открыт депозит.", disable_mentions=1)
                return True
            if not amount or data['balance'] < amount:
                await message.reply("Недостаточно средств или неверная сумма.", disable_mentions=1)
                return True
            dep_days = int(arguments[2]) if len(arguments) >= 3 and arguments[2].isdigit() else 10
            dep_percent = int(arguments[3]) if len(arguments) >= 4 and arguments[3].isdigit() else 35
            dep_days = max(1, min(dep_days, 365))
            dep_percent = max(1, min(dep_percent, 100))
            await update_economy(user_id, 'balance', data['balance'] - amount)
            await update_economy(user_id, 'deposit_amount', amount)
            await update_economy(user_id, 'deposit_opened', datetime.now().isoformat())
            await update_economy(user_id, 'deposit_days', dep_days)
            await update_economy(user_id, 'deposit_percent', dep_percent)
            database.commit()
            await message.reply(f"Депозит открыт на сумму {amount} монет на {dep_days} дн. под {dep_percent}%.", disable_mentions=1)

        if command in ['закрытьдепозит', 'closedeposit']:
            data = await get_economy(user_id)
            if data['deposit_amount'] <= 0:
                await message.reply("У вас нет открытого депозита.", disable_mentions=1)
                return True
            try:
                days = max(1, (datetime.now() - datetime.fromisoformat(data['deposit_opened'])).days)
            except:
                days = 1
            profit = int(data['deposit_amount'] * min(days, 30) * 0.01)
            await update_economy(user_id, 'balance', data['balance'] + data['deposit_amount'] + profit)
            await update_economy(user_id, 'deposit_amount', 0)
            await update_economy(user_id, 'deposit_opened', '0')
            await message.reply(f"Депозит закрыт. Возврат: {data['deposit_amount']} монет, прибыль: {profit} монет.", disable_mentions=1)

        if command in ['купитьбиз', 'buybiz']:
            price = 25000
            data = await get_economy(user_id)
            business = await get_business(user_id)
            if business['level'] > 0:
                await message.reply("У вас уже есть бизнес.", disable_mentions=1)
                return True
            if data['balance'] < price:
                await message.reply(f"Бизнес стоит {price} монет. У вас недостаточно средств.", disable_mentions=1)
                return True
            await update_economy(user_id, 'balance', data['balance'] - price)
            await update_business(user_id, 'level', 1)
            await update_business(user_id, 'products', 100)
            await update_business(user_id, 'last_income', datetime.now().isoformat())
            await message.reply("Вы купили бизнес 1 уровня. Начальные продукты: 100.", disable_mentions=1)

        if command in ['бизнес', 'business']:
            business = await get_business(user_id)
            if business['level'] <= 0:
                await message.reply("У вас нет бизнеса. Купить: /купитьбиз", disable_mentions=1)
                return True
            income = 0
            try:
                hours = int((datetime.now() - datetime.fromisoformat(business['last_income'])).total_seconds() // 3600)
                sold = min(business['products'], hours * business['level'] * 5)
                income = sold * business['level'] * 25
                if sold > 0:
                    await update_business(user_id, 'products', business['products'] - sold)
                    await update_business(user_id, 'cash', business['cash'] + income)
                    await update_business(user_id, 'last_income', datetime.now().isoformat())
                    business = await get_business(user_id)
            except:
                pass
            await message.reply(f"Бизнес:\nУровень: {business['level']}\nТовары: {business['products']}\nДеньги в кассе: {business['cash']} монет\nНовый доход: {income} монет", disable_mentions=1)

        if command in ['ппрод', 'products']:
            if len(arguments) < 2:
                await message.reply("Использование: /ппрод [количество]", disable_mentions=1)
                return True
            amount = parse_money(arguments[1])
            data = await get_economy(user_id)
            business = await get_business(user_id)
            price = amount * 10 if amount else 0
            if business['level'] <= 0 or not amount or data['balance'] < price:
                await message.reply("Не удалось пополнить продукты: проверьте бизнес, количество и баланс.", disable_mentions=1)
                return True
            await update_economy(user_id, 'balance', data['balance'] - price)
            await update_business(user_id, 'products', business['products'] + amount)
            await message.reply(f"Вы пополнили бизнес на {amount} товаров за {price} монет.", disable_mentions=1)

        if command in ['снятьбиз', 'takebiz']:
            business = await get_business(user_id)
            if business['cash'] <= 0:
                await message.reply("В кассе бизнеса нет денег.", disable_mentions=1)
                return True
            await add_balance(user_id, business['cash'])
            await update_business(user_id, 'cash', 0)
            await message.reply(f"Вы сняли с бизнеса {business['cash']} монет.", disable_mentions=1)

        if command in ['топбиз', 'topbiz']:
            sql.execute("SELECT user_id, level, cash FROM businesses WHERE level > 0 ORDER BY level DESC, cash DESC LIMIT 10")
            rows = sql.fetchall()
            if not rows:
                await message.reply("Топ бизнесменов пока пуст.", disable_mentions=1)
                return True
            text = ["Топ бизнесменов:"]
            for index, row in enumerate(rows, 1):
                text.append(f"{index}) [id{row[0]}|Пользователь] — уровень {row[1]}, касса {row[2]}")
            await message.reply("\n".join(text), disable_mentions=1)

        if command in ['проверка', 'checksite']:
            if len(arguments) < 2:
                await message.reply("Использование: /проверка [ссылка]", disable_mentions=1)
                return True
            url = arguments[1].lower()
            bad_words = ['phish', 'login-vk', 'vk-login', 'free-money', 'bonus-vk']
            if not (url.startswith('http://') or url.startswith('https://')):
                await message.reply("Ссылка должна начинаться с http:// или https://", disable_mentions=1)
                return True
            verdict = "подозрительный" if any(i in url for i in bad_words) else "видимых угроз не найдено"
            await message.reply(f"Проверка сайта: {verdict}. Не вводите пароли на незнакомых сайтах.", disable_mentions=1)

        if command in ['аукцион', 'auction']:
            await message.reply("Аукцион пока не запущен. Следите за объявлениями администрации.", disable_mentions=1)

        if command in ['getbynick', 'поискник']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            query = await get_string(arguments, 1)
            if not query:
                await message.reply("Использование: /getbynick [часть ника]", disable_mentions=1)
                return True
            sql.execute(f"SELECT user_id, nick FROM nicks_{chat_id} WHERE nick LIKE ? LIMIT 20", (f"%{query}%",))
            rows = sql.fetchall()
            if not rows:
                await message.reply("Совпадений по нику не найдено.", disable_mentions=1)
                return True
            await message.reply("\n".join([f"[id{i[0]}|Пользователь] — {i[1]}" for i in rows]), disable_mentions=1)

        if command in ['mclear', 'мклир']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            count = parse_money(arguments[1]) if len(arguments) >= 2 else False
            if not count or count > 50:
                await message.reply("Использование: /mclear [1-50]", disable_mentions=1)
                return True
            sql.execute(f"SELECT cmid FROM messages_{chat_id} ORDER BY date DESC LIMIT ?", (count,))
            cmids = [i[0] for i in sql.fetchall()]
            try:
                await bot.api.messages.delete(group_id=message.group_id, peer_id=peer_id, delete_for_all=True, cmids=cmids)
            except:
                pass
            await message.reply(f"Удалено сообщений: {len(cmids)}", disable_mentions=1)

        if command in ['mutelogs', 'мутлоги', 'логмут']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            sql.execute(f"SELECT user_id, moder, reason, date_string FROM mutes_{chat_id} ORDER BY date DESC LIMIT 10")
            rows = sql.fetchall()
            if not rows:
                await message.reply("Логов мутов нет.", disable_mentions=1)
                return True
            await message.reply("\n".join([f"[id{i[0]}|Пользователь] | {i[2]} | {i[3]}" for i in rows]), disable_mentions=1)

        if command in ['bug', 'баг']:
            text = await get_string(arguments, 1)
            if not text:
                await message.reply("Использование: /bug [описание бага]", disable_mentions=1)
                return True
            await message.reply("Спасибо, баг записан для администрации.", disable_mentions=1)

        if command in ['nickhistory', 'историяников']:
            if await get_role(user_id, chat_id) < 2:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            await message.reply("История ников начнет собираться после следующих изменений ника.", disable_mentions=1)

        if command in ['логадм', 'adminlogs', 'invlogs', 'логбан', 'banlogs', 'логкик', 'kicklogs']:
            if await get_role(user_id, chat_id) < 2:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            await message.reply("Логи для этой команды пока пусты.", disable_mentions=1)

        if command in ['serverinfo']:
            command = 'pullinfo'

        if command in ['settings', 'настройки']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            sql.execute("SELECT invite_kick, leave_kick, in_pull, silence, filter, antiflood FROM chats WHERE chat_id = ?", (chat_id,))
            settings = sql.fetchone()
            if not settings:
                await message.reply("Беседа не активирована. Сначала используйте /start.", disable_mentions=1)
                return True
            await message.reply(f"Настройки беседы:\nInvite: {settings[0]}\nLeave: {settings[1]}\nСервер: {settings[2]}\nТишина: {settings[3]}\nФильтр: {settings[4]}\nАнтифлуд: {settings[5]}", disable_mentions=1)
            return True

        if command in ['pin', 'закреп']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            if not message.reply_message:
                await message.reply("Ответьте на сообщение, которое нужно закрепить.", disable_mentions=1)
                return True
            try:
                await bot.api.messages.pin(peer_id=peer_id, conversation_message_id=message.reply_message.conversation_message_id)
                await message.reply("Сообщение закреплено.", disable_mentions=1)
            except:
                await message.reply("Не удалось закрепить сообщение. Проверьте права бота.", disable_mentions=1)
            return True

        if command in ['unpin', 'откреп']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            try:
                await bot.api.messages.unpin(peer_id=peer_id)
                await message.reply("Сообщение откреплено.", disable_mentions=1)
            except:
                await message.reply("Не удалось открепить сообщение. Проверьте права бота.", disable_mentions=1)
            return True

        if command in ['title', 'название']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            title = await get_string(arguments, 1)
            if not title:
                await message.reply("Использование: /title [новое название]", disable_mentions=1)
                return True
            try:
                await bot.api.messages.edit_chat(chat_id=chat_id, title=title)
                await message.reply("Название беседы изменено.", disable_mentions=1)
            except:
                await message.reply("Не удалось изменить название. Проверьте права бота.", disable_mentions=1)
            return True

        if command in ['server', 'сервер']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            if len(arguments) < 2 or not arguments[1].isdigit():
                await message.reply("Использование: /server [номер сервера]", disable_mentions=1)
                return True
            await set_pull(chat_id, int(arguments[1]))
            await message.reply(f"Беседа привязана к серверу {arguments[1]}.", disable_mentions=1)
            return True

        if command in ['sync', 'синхронизация']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            database.commit()
            await message.reply("Синхронизация с базой данных выполнена.", disable_mentions=1)
            return True

        if command in ['addsa']:
            if not await can_grant_sa(user_id, chat_id):
                await message.reply("Выдать спец администратора может только владелец беседы!", disable_mentions=1)
                return True
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True
            if await is_chat_owner(user, chat_id):
                await message.reply("Нельзя выдавать или менять роль владельцу беседы!", disable_mentions=1)
                return True
            sql.execute("INSERT OR REPLACE INTO chat_roles (chat_id, name, priority) VALUES (?, ?, ?)", (chat_id, "Спец администратор", 100))
            sql.execute("INSERT OR REPLACE INTO user_roles (chat_id, user_id, role_name) VALUES (?, ?, ?)", (chat_id, user, "Спец администратор"))
            database.commit()
            await message.reply(f"[id{user}|Пользователю] выданы права спец администратора.", disable_mentions=1)
            return True

        if command in ['addzsa', 'addza']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True
            if await is_chat_owner(user, chat_id):
                await message.reply("Нельзя выдавать или менять роль владельцу беседы!", disable_mentions=1)
                return True
            if not await can_grant_sa(user_id, chat_id) and await get_role(user, chat_id) >= 6:
                await message.reply("Вы не можете изменить роль пользователя с правами спец администратора или выше!", disable_mentions=1)
                return True
            sql.execute("INSERT OR REPLACE INTO chat_roles (chat_id, name, priority) VALUES (?, ?, ?)", (chat_id, "Зам.спец администратора", 80))
            sql.execute("INSERT OR REPLACE INTO user_roles (chat_id, user_id, role_name) VALUES (?, ?, ?)", (chat_id, user, "Зам.спец администратора"))
            database.commit()
            await message.reply(f"[id{user}|Пользователю] выданы права зам. спец администратора.", disable_mentions=1)
            return True

        if command in ['addzamspecbota']:
            if int(user_id) != ACTIVATION_OWNER_ID:
                await message.reply("Выдать роль Зам.Спец.Рук. может только Специальный руководитель!", disable_mentions=1)
                return True
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True
            if int(user) == ACTIVATION_OWNER_ID:
                await message.reply("Нельзя изменить роль Специального руководителя!", disable_mentions=1)
                return True
            sql.execute("INSERT OR REPLACE INTO global_managers (user_id, level) VALUES (?, ?)", (user, 3))
            database.commit()
            await message.reply(f"[id{user}|Пользователю] выдана роль Зам.Спец.Рук. с полными правами.", disable_mentions=1)
            return True

        if command in ['addosnvzamspecbota']:
            if int(user_id) != ACTIVATION_OWNER_ID:
                await message.reply("Выдать роль Основ.Зам.Спец.Рук. может только Специальный руководитель!", disable_mentions=1)
                return True
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True
            if int(user) == ACTIVATION_OWNER_ID:
                await message.reply("Нельзя изменить роль Специального руководителя!", disable_mentions=1)
                return True
            sql.execute("INSERT OR REPLACE INTO global_managers (user_id, level) VALUES (?, ?)", (user, 4))
            database.commit()
            await message.reply(f"[id{user}|Пользователю] выдана роль Основ.Зам.Спец.Рук. с полными правами.", disable_mentions=1)
            return True

        if command in ['removezamspecbota']:
            if int(user_id) != ACTIVATION_OWNER_ID:
                await message.reply("Снять роль Зам.Спец.Рук. может только Специальный руководитель!", disable_mentions=1)
                return True
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True
            if int(user) == ACTIVATION_OWNER_ID:
                await message.reply("Нельзя изменить роль Специального руководителя!", disable_mentions=1)
                return True
            sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user,))
            fetch = sql.fetchone()
            if not fetch or fetch[0] != 3:
                await message.reply(f"[id{user}|Пользователь] не имеет роли Зам.Спец.Рук.!", disable_mentions=1)
                return True
            sql.execute("DELETE FROM global_managers WHERE user_id = ?", (user,))
            database.commit()
            await message.reply(f"[id{user}|Пользователю] снята роль Зам.Спец.Рук.", disable_mentions=1)
            return True

        if command in ['removeosnvzamspecbota']:
            if int(user_id) != ACTIVATION_OWNER_ID:
                await message.reply("Снять роль Основ.Зам.Спец.Рук. может только Специальный руководитель!", disable_mentions=1)
                return True
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True
            if int(user) == ACTIVATION_OWNER_ID:
                await message.reply("Нельзя изменить роль Специального руководителя!", disable_mentions=1)
                return True
            sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user,))
            fetch = sql.fetchone()
            if not fetch or fetch[0] != 4:
                await message.reply(f"[id{user}|Пользователь] не имеет роли Основ.Зам.Спец.Рук.!", disable_mentions=1)
                return True
            sql.execute("DELETE FROM global_managers WHERE user_id = ?", (user,))
            database.commit()
            await message.reply(f"[id{user}|Пользователю] снята роль Основ.Зам.Спец.Рук.", disable_mentions=1)
            return True

        if command in ['передатьср', 'transfersr']:
            if int(user_id) != ACTIVATION_OWNER_ID:
                await message.reply("Эту команду может использовать только Специальный руководитель!", disable_mentions=1)
                return True

            user = int
            confirm_arg = 2
            if message.reply_message:
                user = message.reply_message.from_id
                confirm_arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                confirm_arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply(
                    "Укажите пользователя!\n"
                    "Пример: /передатьср @id1 подтвердить",
                    disable_mentions=1
                )
                return True

            if user == user_id:
                await message.reply("Вы не можете передать права самому себе!", disable_mentions=1)
                return True

            if len(arguments) <= confirm_arg:
                await message.reply(
                    "⚠️ Внимание! Эта команда передаёт права Специального руководителя другому пользователю.\n"
                    "Ваша роль будет снята до уровня Пользователя.\n\n"
                    f"Для подтверждения напишите: /передатьср @id{user} подтвердить",
                    disable_mentions=1
                )
                return True

            if arguments_lower[confirm_arg] not in ["подтвердить", "confirm"]:
                await message.reply(
                    "Для подтверждения напишите подтвердить или confirm.\n"
                    f"Пример: /передатьср @id{user} подтвердить",
                    disable_mentions=1
                )
                return True

            global ACTIVATION_OWNER_ID

            # Обновляем config.json — новый Специальный руководитель
            with open("config.json", "r") as _f:
                _cfg = json.load(_f)
            _cfg["owner_id"] = int(user)
            with open("config.json", "w") as _f:
                json.dump(_cfg, _f, ensure_ascii=False, indent=2)

            old_owner = ACTIVATION_OWNER_ID
            ACTIVATION_OWNER_ID = int(user)

            # Убираем нового СР из global_managers (если там был)
            sql.execute("DELETE FROM global_managers WHERE user_id = ?", (int(user),))
            # Старый СР не добавляется в global_managers — он становится обычным пользователем
            sql.execute("DELETE FROM global_managers WHERE user_id = ?", (old_owner,))
            database.commit()

            new_name = await get_user_name(int(user), chat_id)
            old_name = await get_user_name(old_owner, chat_id)
            await message.answer(
                f"👑 Права Специального руководителя переданы!\n\n"
                f"[id{old_owner}|{old_name}] → роль снята до Пользователя\n"
                f"[id{user}|{new_name}] → назначен Специальным руководителем",
                disable_mentions=1
            )
            return True

        if command in ['gsrnick']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете очистить ник данному пользователю!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>", disable_mentions=1)
                return True

            success = 0
            for i in pull:
                try:
                    await rnick(user, i)
                    success += 1
                except:
                    pass

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) убрал(-а) ник @id{user} ({await get_user_name(user, chat_id)}) в беседах привязки\nОбработано бесед: {success}/{len(pull)}", disable_mentions=1)
            return True

        if command in ['gssnick']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете установить ник данному пользователю!", disable_mentions=1)
                return True

            new_nick = await get_string(arguments, arg)
            if not new_nick:
                await message.reply("Укажите ник пользователя!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>", disable_mentions=1)
                return True

            success = 0
            for i in pull:
                try:
                    await setnick(user, i, new_nick)
                    success += 1
                except:
                    pass

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) установил(-а) ник @id{user} ({await get_user_name(user, chat_id)}) в беседах привязки\nНовый ник: {new_nick}\nОбработано бесед: {success}/{len(pull)}", disable_mentions=1)
            return True

        if command in ['gskick']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете исключить данного пользователя!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>", disable_mentions=1)
                return True

            reason = await get_string(arguments, arg)
            success = 0
            for i in pull:
                try:
                    await bot.api.messages.remove_chat_user(i, user)
                    success += 1
                    text = f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) @id{user} ({await get_user_name(user, chat_id)}) в беседах привязки"
                    if reason:
                        text += f"\nПричина: {reason}"
                    await bot.api.messages.send(peer_id=2000000000+i, message=text, disable_mentions=1, random_id=0)
                except:
                    pass

            await message.answer(f"Пользователь @id{user} ({await get_user_name(user, chat_id)}) исключён из бесед привязки.\nОбработано бесед: {success}/{len(pull)}", disable_mentions=1)
            return True

        if command in ['gsban']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете заблокировать данного пользователя!", disable_mentions=1)
                return True

            reason = await get_string(arguments, arg)
            if not reason:
                await message.reply("Укажите причину блокировки!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>", disable_mentions=1)
                return True

            success = 0
            for i in pull:
                try:
                    await ban(user, user_id, i, reason)
                    try:
                        await bot.api.messages.remove_chat_user(i, user)
                    except:
                        pass
                    success += 1
                    await bot.api.messages.send(peer_id=2000000000+i, message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал(-а) @id{user} ({await get_user_name(user, chat_id)}) в беседах привязки\nПричина: {reason}", disable_mentions=1, random_id=0)
                except:
                    pass

            await message.answer(f"Пользователь @id{user} ({await get_user_name(user, chat_id)}) заблокирован в беседах привязки.\nПричина: {reason}\nОбработано бесед: {success}/{len(pull)}", disable_mentions=1)
            return True

        if command in ['gsunban']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>", disable_mentions=1)
                return True

            success = 0
            for i in pull:
                try:
                    await unban(user, i)
                    success += 1
                except:
                    pass

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) разблокировал(-а) @id{user} ({await get_user_name(user, chat_id)}) в беседах привязки\nОбработано бесед: {success}/{len(pull)}", disable_mentions=1)
            return True

        if command in ['clearwarn', 'srroleall', 'srnickall', 'srnickal']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            if command in ['clearwarn']:
                try:
                    users = await bot.api.messages.get_conversation_members(peer_id=peer_id)
                    users = json.loads(users.json())
                    chat_users = []
                    for i in users["profiles"]:
                        chat_users.append(i["id"])

                    sql.execute(f"SELECT user_id FROM warns_{chat_id}")
                    warns_users = sql.fetchall()
                    cleared = 0
                    for i in warns_users:
                        if i[0] not in chat_users:
                            await clear_warns(chat_id, i[0])
                            cleared += 1

                    await message.answer(f"Очищены предупреждения отсутствующих пользователей.\nОчищено пользователей: {cleared}", disable_mentions=1)
                except:
                    await message.reply("Не удалось очистить предупреждения. Проверьте права бота в беседе.", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>", disable_mentions=1)
                return True

            if command in ['srroleall']:
                success = 0
                for i in pull:
                    try:
                        sql.execute(f"DELETE FROM permissions_{i}")
                        sql.execute("DELETE FROM user_roles WHERE chat_id = ?", (i,))
                        success += 1
                    except:
                        pass
                database.commit()
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) очистил(-а) все роли во всех беседах сервера.\nОбработано бесед: {success}/{len(pull)}", disable_mentions=1)
                return True

            if command in ['srnickall', 'srnickal']:
                success = 0
                for i in pull:
                    try:
                        await rnickall(i)
                        success += 1
                    except:
                        pass
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) очистил(-а) все ники во всех беседах сервера.\nОбработано бесед: {success}/{len(pull)}", disable_mentions=1)
                return True

        if command in ['snick', 'setnick', 'nick', 'addnick', 'ник', 'сетник', 'аддник']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!")
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) == 0:
                await message.reply("Вы не можете установить ник данному пользователю!", disable_mentions=1)
                return True

            new_nick = await get_string(arguments, arg)
            if not new_nick:
                await message.reply("Укажите ник пользователя!", disable_mentions=1)
                return True
            else: await setnick(user, chat_id, new_nick)

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) установил новое имя @id{user} (пользователю)!\nНовый ник: {new_nick}", disable_mentions=1)

        if command in ['rnick', 'removenick', 'clearnick', 'cnick', 'рник', 'удалитьник', 'снятьник']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) == 0:
                await message.reply("Вы не можете удалить ник данному пользователю!", disable_mentions=1)
                return True

            await rnick(user, chat_id)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) убрал(-а) ник у @id{user} (пользователя)!", disable_mentions=1)

        if command in ['getacc', 'acc', 'гетакк', 'аккаунт', 'account']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            nick = await get_string(arguments, 1)
            if not nick:
                await message.reply("Укажите ник!", disable_mentions=1)
                return True

            nick_result = await get_acc(chat_id, nick)

            if not nick_result:
                await message.reply(f"Ник «{nick}» никому не принадлежит!", disable_mentions=1)
            else:
                info = await bot.api.users.get(user_ids=nick_result)
                if info:
                    await message.reply(f"Ник «{nick}» принадлежит @id{nick_result} ({info[0].first_name} {info[0].last_name})", disable_mentions=1)
                else:
                    await message.reply(f"Ник «{nick}» принадлежит @id{nick_result}, но не удалось получить информацию о пользователе (возможно, страница удалена).", disable_mentions=1)

        if command in ['getnick', 'gnick', 'гник', 'гетник']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            nick = await get_nick(user, chat_id)
            if not nick: await message.reply(f"У данного @id{user} (пользователя) нет ника!", disable_mentions=1)
            else: await message.reply(f"Ник данного @id{user} (пользователя): {nick}", disable_mentions=1)

        if command in ['никлист', 'ники', 'всеники', 'nlist', 'nickslist', 'nicklist', 'nicks']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            nicks = await nlist(chat_id, 1)
            nick_list = '\n'.join(nicks)
            if nick_list == "": nick_list = "Ники отсутствуют!"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": "nicksMinus", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("⏩", {"command": "nicksPlus", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
            )

            await message.reply(f"Пользователи с ником [1 страница]:\n{nick_list}\n\nПользователи без ников: «/nonick»", disable_mentions=1, keyboard=keyboard)

        if command in ['nonick', 'nonicks', 'nonicklist', 'nolist', 'nnlist', 'безников', 'ноникс']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            nonicks = await nonick(chat_id, 1)
            nonick_list = '\n'.join(nonicks)
            if nonick_list == "": nonick_list = "Пользователи без ников отсутствуют!"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": "nonickMinus", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("С никами", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("⏩", {"command": "nonickPlus", "page": 1, "chatId": chat_id}),
                     color=KeyboardButtonColor.POSITIVE)
            )

            await message.reply(f"Пользователи без ников [1]:\n{nonick_list}\n\nПользователи с никами: «/nlist»", disable_mentions=1, keyboard=keyboard)

        if command in ['kick', 'кик', 'исключить']:
            if not await check_perm(user_id, chat_id, 'kick', 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете исключить данного пользователя!", disable_mentions=1)
                return True

            reason = await get_string(arguments, arg)

            try: await bot.api.messages.remove_chat_user(chat_id, user)
            except:
                await message.reply(f"Не удается исключить данного @id{user} (пользователя)! Необходимо забрать у него звезду.", disable_mentions=1)
                return True

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}), color=KeyboardButtonColor.NEGATIVE)
            )

            if not reason: await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) кикнул(-а) @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1, keyboard=keyboard)
            else: await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) кикнул(-а) @id{user} ({await get_user_name(user, chat_id)})\nПричина: {reason}", disable_mentions=1, keyboard=keyboard)

            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['warn', 'варн']:
            if not await check_perm(user_id, chat_id, 'warn', 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать пред данному пользователю!", disable_mentions=1)
                return True

            reason = await get_string(arguments, arg)
            if not reason:
                await message.reply("Укажите причину предупреждения!")
                return True

            warns = await warn(chat_id, user, user_id, reason)
            if warns < 3:
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Снять варн", {"command": "unwarn", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                    .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}), color=KeyboardButtonColor.NEGATIVE)
                )
                await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) предупреждение @id{user} ({await get_user_name(user, chat_id)})\nПричина: {reason}\nКоличество предупреждений: {warns}", disable_mentions=1, keyboard=keyboard)
            else:
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}),color=KeyboardButtonColor.NEGATIVE)
                )
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) последнее предупреждение @id{user} ({await get_user_name(user, chat_id)}) (3/3)\nПричина: {reason}\n@id{user} (Пользователь) был исключен за большое количество предупреждений!",disable_mentions=1, keyboard=keyboard)
                try: await bot.api.messages.remove_chat_user(user)
                except: pass
                await clear_warns(chat_id, user)

            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['unwarn', 'унварн', 'анварн', 'снятьпред', 'минуспред']:
            if not await check_perm(user_id, chat_id, 'unwarn', 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете снять пред данному пользователю!", disable_mentions=1)
                return True

            if await get_warns(user, chat_id) < 1:
                await message.reply("У пользователя нет предупреждений!")
                return True

            warns = await unwarn(chat_id, user)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) снял(-а) предупреждение @id{user} ({await get_user_name(user, chat_id)})\nКоличество предупреждений: {warns}", disable_mentions=1)

        if command in ['getwarn', 'gwarn', 'getwarns', 'гетварн', 'гварн']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Вы не указали @пользователя!", disable_mentions=1)
                return True

            warns = await gwarn(user, chat_id)
            string_info = str
            if not warns: string_info = "Активных предупреждений нет!"
            else: string_info = f"@id{warns['moder']} (Модератор) | {warns['reason']} | {warns['count']}/3 | {warns['time']}"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("История предупреждений", {"command": "warnhistory", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            )

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), информация о активных предупреждениях @id{user} (пользователя):\n{string_info}", disable_mentions=1, keyboard=keyboard)

        if command in ['warnhistory', 'historywarns', 'whistory', 'историяварнов', 'историяпредов']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            warnhistory_mass = await warnhistory(user, chat_id)
            if not warnhistory_mass: wh_string = "Предупреждений не было!"
            else: wh_string = '\n'.join(warnhistory_mass)

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Активные предупреждения", {"command": "activeWarns", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("Вся информация", {"command": "stats", "user": user, "chatId": chat_id}),color=KeyboardButtonColor.PRIMARY)
            )

            await message.reply(f"Информация о всех предупреждениях @id{user} ({await get_user_name(user, chat_id)})\nКоличество предупреждений пользователя: {await get_warns(user, chat_id)}\n\nИнформация о последних 10 предупреждений пользователя:\n{wh_string}", disable_mentions=1, keyboard=keyboard)

        if command in ['warnlist', 'warns', 'wlist', 'варны', 'варнлист']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            warns = await warnlist(chat_id)
            if warns == False: warns_string = "Пользователей с предупреждениями нет!"
            else: warns_string = '\n'.join(warns)

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), список пользователей с варнами:\n{warns_string}", disable_mentions=1)

        if command in ['staff', 'стафф']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            roles_data = await staff(chat_id)

            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings',group_id=message.group_id)
            x = json.loads(x.json())
            for i in x['items']: owner_id = int(i["chat_settings"]["owner_id"])

            if owner_id < 1: owner = f"— [club{abs(owner_id)}|Сообщество]"
            else: owner = f"— [id{owner_id}|{await get_user_name(owner_id, chat_id)}]"

            res_msg = f"Владелец беседы {owner}\n\n"

            for role_name, users in roles_data.items():
                users_string = "\n".join(users) if users else "Отсутствуют"
                res_msg += f"{role_name}:\n{users_string}\n\n"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Никнеймы", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            )
            
            await message.reply(res_msg.strip(), disable_mentions=1, keyboard=keyboard)

        if command in ['reg', 'registration', 'regdate', 'рег', 'регистрация', 'датарегистрации']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else: user = user_id

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Вся информация", {"command": "stats", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            )
            await message.reply(f"Дата регистрации @id{user} (пользователя): {await get_registration_date(user)}", disable_mentions=1, keyboard=keyboard)

        if command in ['mute', 'мут', 'мьют', 'муте', 'addmute']:
            if not await check_perm(user_id, chat_id, 'mute', 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 2
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 2
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 3
            else:
                await message.reply("Укажите пользователя!")
                return True

            if len(arguments) < 4 and arg == 3:
                await message.reply("Укажите аргументы команды!")
                return True

            if len(arguments) < 3 and arg == 2:
                await message.reply("Укажите аргументы команды!")
                return True

            await checkMute(chat_id, user)

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать мут данному пользователю!", disable_mentions=1)
                return True

            if await get_mute(user, chat_id):
                await message.reply("Пользователь уже замьючен!")
                return True

            reason = await get_string(arguments, arg)
            if not reason:
                await message.reply("Укажите причину предупреждения!")
                return True

            if arg == 3: mute_time = arguments[2]
            else: mute_time = arguments[1]
            try: mute_time = int(mute_time)
            except:
                await message.reply("Укажите время в минутах!")
                return True


            if mute_time < 1 or mute_time > 1000:
                await message.reply("Время не должно превышать 1000, и быть не менее 0!")
                return True

            await mute(user, chat_id, user_id, reason, mute_time)

            do_time = datetime.now() + timedelta(minutes=mute_time)
            mute_time = str(do_time).split('.')[0]


            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять мут", {"command": "unmute", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}), color=KeyboardButtonColor.NEGATIVE)
            )

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) замутил(-а) @id{user} ({await get_user_name(user, chat_id)})\nПричина: {reason}\nМут выдан до: {mute_time}", disable_mentions=1, keyboard=keyboard)
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['unmute', 'снятьмут', 'анмут', 'анмьют', 'унмут']:
            if not await check_perm(user_id, chat_id, 'unmute', 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            await checkMute(chat_id, user)

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете снять мут данному пользователю!", disable_mentions=1)
                return True

            if not await get_mute(user, chat_id):
                await message.reply(f"У @id{user} (пользователя) нет мута!", disable_mentions=1)
                return True

            await unmute(user, chat_id)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) размутил(-а) @id{user} ({await get_user_name(user, chat_id)})")

        if command in ['getmute', 'gmute', 'гмут', 'гетмут', 'чекмут']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            await checkMute(chat_id, user)

            mute_string = str
            gmute = await get_mute(user, chat_id)
            if not gmute: mute_string = "У пользователя нет мута!"
            else:
                do_time = datetime.fromisoformat(gmute['date']) + timedelta(minutes=gmute['time'])
                mute_time = str(do_time).split('.')[0]

                try:
                    int(gmute['moder'])
                    mute_string = f"@id{gmute['moder']} (Модератор) | {gmute['reason']} | {gmute['date']} | До: {mute_time}"
                except: mute_string = f"Бот | {gmute['reason']} | {gmute['date']} | До: {mute_time}"

            await message.reply(f"Информация о муте @id{user} ({await get_user_name(user, chat_id)}):\n\n{mute_string}", disable_mentions=1)

        if command in ['mutelist', 'mutes', 'муты', 'мутлист']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            mutes = await mutelist(chat_id)
            if not mutes: mutes_str = ""
            else:
                mutes_str = '\n'.join(mutes)

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), список пользователей с мутами:\n{mutes_str}", disable_mentions=1)

        if command in ['clear', 'чистка', 'очистить', 'очистка']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете очистить сообщения данного пользователя!", disable_mentions=1)
                return True

            await clear(user, chat_id, message.group_id, message.peer_id)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) очистил(-а) сообщение(-я)!")

        if command in ['alt', 'альт', 'альтернативные']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            commands_levels = {
                1: [
                    '\nКоманды модераторов:',
                    '/setnick — snick, nick, addnick, ник, сетник, аддник',
                    '/removenick —  removenick, clearnick, cnick, рник, удалитьник, снятьник',
                    '/getnick — gnick, гник, гетник',
                    '/getacc — acc, гетакк, аккаунт, account',
                    '/nlist — ники, всеники, nlist, nickslist, nicklist, nicks',
                    '/nonick — nonicks, nonicklist, nolist, nnlist, безников, ноникс',
                    '/kick — кик, исключить',
                    '/warn — пред, варн, pred, предупреждение',
                    '/unwarn — унварн, анварн, снятьпред, минуспред',
                    '/getwarn — gwarn, getwarns, гетварн, гварн',
                    '/warnhistory — historywarns, whistory, историяварнов, историяпредов',
                    '/warnlist — warns, wlist, варны, варнлист',
                    '/staff — стафф',
                    '/reg — registration, regdate, рег, регистрация, датарегистрации',
                    '/mute — мут, мьют, муте, addmute',
                    '/unmute — снятьмут, анмут, унмут, снятьмут',
                    '/alt — альт, альтернативные',
                    '/getmute -- gmute, гмут, гетмут, чекмут',
                    '/mutelist -- mutes, муты, мутлист',
                    '/clear -- чистка, очистить, очистка',
                    '/getban -- чекбан, гетбан, checkban',
                    '/delete -- удалить'
                ],
                2: [
                    '\nКоманды старших модераторов:',
                    '/ban — бан, блокировка',
                    '/unban -- унбан, снятьбан',
                    '/addmoder -- moder',
                    '/removerole -- rrole, снятьроль',
                    '/zov - зов, вызов',
                    '/online - ozov, озов',
                    '/onlinelist - olist, олист',
                    '/banlist - bans, банлист, баны',
                    '/inactive - ilist, inactive',
                    '/masskick - mkick'
                ],
                3: [
                    '\nКоманды администраторов:',
                    '/quiet -- silence, тишина',
                    '/skick -- скик, снят',
                    '/sban -- сбан',
                    '/sunban — сунбан, санбан',
                    '/addsenmoder — senmoder',
                    '/rnickall -- allrnick, arnick, mrnick',
                    '/sremovenick -- srnick',
                    '/szov -- serverzov, сзов',
                    '/srole -- prole, pullrole'
                ],
                4: [
                    '\nКоманды старших администраторов:',
                    '/addadmin -- admin',
                    '/pullinfo -- pulli',
                    '/banwords -- bws',
                    '/filter -- none',
                    '/sremoverole -- srrole'
                ],
                5: [
                    '\nСписок команд владельца беседы',
                    '/antiflood -- af',
                    '/welcometext -- welcome, wtext',
                    '/invite -- none',
                    '/leave -- none',
                    '/addsenadmin -- senadm, addsenadm, senadmin',
                    '/setpull -- pull',
                    '/editowner -- owner, setowner, владелец'
                ]
            }

            user_role = await get_role(user_id, chat_id)

            commands = []
            for i in commands_levels.keys():
                if i <= user_role:
                    for b in commands_levels[i]:
                        commands.append(b)

            level_commands = '\n'.join(commands)

            await message.reply(f"Альтернативные команды\n\n{level_commands}", disable_mentions=1)

        if command in ['getban', 'чекбан', 'гетбан', 'checkban']:
            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if user < 0:
                await message.reply("Нельзя взаимодействовать с сообществом!", disable_mentions=1)
                return True

            info = await bot.api.users.get(user_ids=user)
            full_name = f"{info[0].first_name} {info[0].last_name}" if info else f"id{user}"
            ud = await get_user_data(user)
            global_ban = ud['global_ban']

            pull_ban_detail = None
            pull_chats = await get_pull_chats(chat_id)
            if pull_chats:
                for cid in pull_chats:
                    try:
                        sql.execute(f"SELECT * FROM bans_{cid} WHERE user_id = ?", (user,))
                        pb = sql.fetchone()
                        if pb:
                            moder_name = await get_user_name(pb[1], cid)
                            pull_ban_detail = {'moder': pb[1], 'moder_name': moder_name, 'reason': pb[2], 'date': pb[4]}
                            break
                    except:
                        pass

            sql.execute("SELECT chat_id, peer_id FROM chats")
            all_chats = sql.fetchall()
            local_bans = []
            for (cid, pid) in all_chats:
                try:
                    sql.execute(f"SELECT * FROM bans_{cid} WHERE user_id = ?", (user,))
                    lb = sql.fetchone()
                    if lb:
                        moder_name = await get_user_name(lb[1], cid)
                        chat_title = f"Беседа {cid}"
                        try:
                            convs = await bot.api.messages.get_conversations_by_id(peer_ids=pid)
                            convs_data = json.loads(convs.json())
                            for item in convs_data.get('items', []):
                                cs = item.get('chat_settings') or {}
                                if cs.get('title'):
                                    chat_title = cs['title']
                        except:
                            pass
                        local_bans.append({'chat_title': chat_title, 'moder': lb[1], 'moder_name': moder_name, 'reason': lb[2], 'date': lb[4]})
                except:
                    pass

            gban_info = await get_global_ban_info(user)
            lines = [f"Информация о блокировках", f"[id{user}|{full_name}]", ""]

            if pull_ban_detail:
                lines.append(f"Информация о общей блокировке в беседах: [id{pull_ban_detail['moder']}|{pull_ban_detail['moder_name']}] | {pull_ban_detail['reason']} | {pull_ban_detail['date']}")
            else:
                lines.append("Информация о общей блокировке в беседах: отсутствует")

            lines.append("")

            if gban_info:
                lines.append(f"Информация о блокировке в беседах игроков: [id{gban_info['moder_id']}|Модератор] | {gban_info['reason']} | {gban_info['date']}")
            else:
                lines.append(f"Информация о блокировке в беседах игроков: {'присутствует' if global_ban else 'отсутствует'}")

            if local_bans:
                lines.append(f"Блокировки в беседах: {len(local_bans)} шт.")
                for idx, b in enumerate(local_bans, 1):
                    lines.append(f"{idx}) {b['chat_title']} | [id{b['moder']}|{b['moder_name']}] | {b['reason']} | {b['date']}")
            else:
                lines.append("Блокировки в беседах отсутствуют")

            if gban_info and await check_perm(user_id, chat_id, 'gbanpl', 5):
                keyboard_gban_check = (
                    Keyboard(inline=True)
                    .add(Callback("Разблокировать", {"command": "ungbanpl", "user": user}), color=KeyboardButtonColor.POSITIVE)
                )
                await message.reply("\n".join(lines), disable_mentions=1, keyboard=keyboard_gban_check)
            else:
                await message.reply("\n".join(lines), disable_mentions=1)

        if command in ['gbanpl', 'гбанпл', 'globalbanpl']:
            if not await check_perm(user_id, chat_id, 'gbanpl', 5):
                await message.reply("Недостаточно прав! Требуется: Зам. спец. администратора+", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id; arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id; arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1]); arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if user < 0:
                await message.reply("Нельзя взаимодействовать с сообществом!", disable_mentions=1)
                return True

            if int(user) == ACTIVATION_OWNER_ID:
                await message.reply("Нельзя заблокировать Руководителя бота!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать глобальный бан данному пользователю!", disable_mentions=1)
                return True

            reason = await get_string(arguments, arg)
            if not reason:
                await message.reply("Укажите причину глобального бана!", disable_mentions=1)
                return True

            existing_gban = await get_global_ban_info(user)
            if existing_gban:
                await message.reply("Пользователь уже имеет глобальный бан!", disable_mentions=1)
                return True

            await set_global_ban(user, user_id, reason)

            sql.execute("SELECT chat_id FROM chats")
            all_chats_gban = sql.fetchall()
            kicked_count = 0
            for (cid_g,) in all_chats_gban:
                try:
                    await bot.api.messages.remove_chat_user(cid_g, user)
                    kicked_count += 1
                except:
                    pass

            info_g = await bot.api.users.get(user_ids=user)
            full_name_g = f"{info_g[0].first_name} {info_g[0].last_name}" if info_g else f"id{user}"
            moder_name_g = await get_user_name(user_id, chat_id)

            await message.answer(
                f"[id{user_id}|{moder_name_g}] заблокировал(-а) во всех беседах пользователя(-ю) [id{user}|{full_name_g}]\n"
                f"Причина: {reason}\n"
                f"Исключён из бесед: {kicked_count}",
                disable_mentions=1
            )

        if command in ['ungbanpl', 'unglobalbanpl']:
            if not await check_perm(user_id, chat_id, 'gbanpl', 5):
                await message.reply("Недостаточно прав! Требуется: Зам. спец. администратора+", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0: user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            existing_gban_u = await get_global_ban_info(user)
            if not existing_gban_u:
                await message.reply("У пользователя нет глобального бана!", disable_mentions=1)
                return True

            await remove_global_ban(user)
            info_rg = await bot.api.users.get(user_ids=user)
            full_name_rg = f"{info_rg[0].first_name} {info_rg[0].last_name}" if info_rg else f"id{user}"
            moder_name_rg = await get_user_name(user_id, chat_id)

            await message.answer(
                f"✅ Глобальный бан снят с [id{user}|{full_name_rg}]\n"
                f"Снял(-а): [id{user_id}|{moder_name_rg}]",
                disable_mentions=1
            )

        if command in ['ban', 'бан', 'блокировка']:
            if not await check_perm(user_id, chat_id, 'ban', 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать бан данному пользователю!", disable_mentions=1)
                return True

            reason = await get_string(arguments, arg)
            if not reason:
                await message.reply("Укажите причину бана!")
                return True

            if await checkban(user, chat_id):
                await message.reply("Пользователь уже заблокирован в этой беседе!")
                return True

            await ban(user, user_id, chat_id, reason)

            try: await bot.api.messages.remove_chat_user(chat_id, user)
            except: pass

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять бан", {"command": "unban", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
            )

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал(-а) @id{user} ({await get_user_name(user, chat_id)})\nПричина: {reason}", disable_mentions=1, keyboard=keyboard)
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['unban', 'унбан', 'снятьбан']:
            if not await check_perm(user_id, chat_id, 'unban', 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            getban = await checkban(user, chat_id)
            if not getban:
                await message.reply("Пользователь не заблокирован в этой беседе")
                return True

            if await equals_roles(user_id, getban['moder'], chat_id) < 1:
                await message.reply("Вы не можете снять бан данному пользователю, т.к. его заблокировал человек с уровнем прав выше!", disable_mentions=1)
                return True

            await unban(user, chat_id)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) разблокировал(-а) @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)

        if command in ['addmoder', 'moder']:
            if await get_role(user_id, chat_id) < 2:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать роль данному пользователю!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 1)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права модератора @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['removerole', 'rrole', 'снятьроль']:
            if await get_role(user_id, chat_id) < 2:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать роль данному пользователю!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 0)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) забрал(-а) роль у @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['zov', 'зов', 'вызов']:
            if not await check_perm(user_id, chat_id, 'zov', 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            reason = await get_string(arguments, 1)
            if not reason:
                await message.reply("Укажите причину вызова!")
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online_info", "online"])
            users = json.loads(users.json())
            user_f = []
            gi = 0
            for i in users["profiles"]:
                if not i['id'] == user_id:
                    gi = gi + 1
                    if gi <= 100:
                        user_f.append(f"@id{i['id']} (🖤)")
            zov_users = ''.join(user_f)

            await message.answer(f"🔔 Вы были вызваны @id{user_id} (администратором) беседы\n\n{zov_users}\n\n❗ Причина вызова: {reason}")

        if command in ['ozov', 'online', 'озов']:
            if not await check_perm(user_id, chat_id, 'online', 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            reason = await get_string(arguments, 1)
            if not reason:
                await message.reply("Укажите причину вызова!")
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online_info", "online"])
            users = json.loads(users.json())
            online_users = []
            gi = 0
            for i in users["profiles"]:
                if i["online"] == 1:
                    if not i['id'] == user_id:
                        gi = gi + 1
                        if gi <= 100:
                            online_users.append(f"@id{i['id']} (♦️)")

            online_zov = "".join(online_users)
            await message.answer(f"🔔 Вы были вызваны @id{user_id} (администратором) беседы\n\n{online_zov}\n\n❗ Причина вызова: {reason}")

        if command in ['onlinelist', 'olist', 'олист']:
            if await get_role(user_id, chat_id) < 2:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online", "online_info"])
            users = json.loads(users.json())
            online_users = []
            gi = 0
            for i in users["profiles"]:
                if i["online"] == 1:
                    if not i['id'] == user_id:
                        gi = gi + 1
                        if gi <= 80:
                            if i["online_info"]["is_mobile"] == False:
                                online_users.append(f"@id{i['id']} ({await get_user_name(i['id'], chat_id)}) -- 💻")
                            else:
                                online_users.append(f"@id{i['id']} ({await get_user_name(i['id'], chat_id)}) -- 📱")

            olist_users = "\n".join(online_users)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), cписок пользователей онлайн\n\n{olist_users}\n\nВсего в онлайн: {gi}", disable_mentions=1)

        if command in ['banlist', 'bans', 'банлист', 'баны']:
            if await get_role(user_id, chat_id) < 2:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            bans = await banlist(chat_id)
            bans_do = []
            gi = 0
            for i in bans:
                gi = gi + 1
                if gi <= 10:
                    bans_do.append(i)
            bans_str = "\n".join(bans_do)

            await message.reply(f"Информация о последних 10 блокировках в беседе:\n\n{bans_str}\n\nВсего блокировок: {gi}", disable_mentions=1)

        if command in ['delete', 'удалить']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if not message.reply_message:
                await message.reply("Чтобы удалить сообщение, нужно ответить на него!")
                return True

            cmid = message.reply_message.conversation_message_id
            user = message.reply_message.from_id

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете удалить сообщение данного пользователя!", disable_mentions=1)
                return True

            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=peer_id, delete_for_all=True, cmids=cmid)
            except: pass

            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=peer_id, delete_for_all=True, cmids=message.conversation_message_id)
            except: pass

        if command in ['inactivelist', 'inactive', 'ilist']:
            if await get_role(user_id, chat_id) < 2:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id,fields=["online_info", "online", "last_seen"])
            users = json.loads(users.json())
            unactive_users_day = []
            count_uad = 0
            unactive_users_moon = []
            count_uam = 0
            for i in users["profiles"]:
                try:
                    currency_time = time.time()
                    time_seen = i['last_seen']['time']
                    last_seen_device_list = {1: "📱", 2: "📱", 3: "📱", 4: "📱", 5: "📱", 6: "💻", 7: "💻"}
                    last_seen_device = last_seen_device_list.get(i['last_seen']['platform'])
                    if time_seen <= currency_time - 604800:
                        count_uam = count_uam + 1
                        if count_uam <= 30:
                            info = await bot.api.users.get(i['id'])
                            unactive_users_moon.append(
                                f"{count_uam}) @id{i['id']} ({info[0].first_name} {info[0].last_name}) -- {last_seen_device}")
                    elif time_seen <= currency_time - 86400:
                        count_uad = count_uad + 1
                        if count_uad <= 30:
                            info = await bot.api.users.get(i['id'])
                            unactive_users_day.append(
                                f"{count_uad}) @id{i['id']} ({info[0].first_name} {info[0].last_name}) -- {last_seen_device}")
                except:
                    pass
            uad = "\n".join(unactive_users_day)
            uam = "\n".join(unactive_users_moon)
            await message.reply(f"Список неактивных пользователей [Более недели]\n{uam}\n\nБолее дня\b{uad}", disable_mentions=1)

        if command in ['mkick', 'мкик', 'masskick']:
            if not await check_perm(user_id, chat_id, 'masskick', 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if len(arguments) <= 1:
                await message.reply("Укажите пользователя(-ей)", disable_mentions=1)
                return True
            if len(arguments) >= 30:
                await message.reply("Не более 30 пользователей!", disable_mentions=1)
                return True

            if arguments[1] in ['all', 'все']:
                if not await check_perm(user_id, chat_id, 'masskick_all', 5):
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True

                users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id,
                                                                        fields=["online_info", "online"])
                users = json.loads(users.json())
                user_f = []
                gi = 0
                for i in users["profiles"]:
                    if not i['id'] == user_id and await get_role(i['id'], chat_id) <= 0:
                        await bot.api.messages.remove_chat_user(chat_id, int(i['id']))

                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) пользователей без ролей", disable_mentions=1)
                return True


            do_users = []
            for i in range(len(arguments)):
                if i <= 0:
                    pass
                else:
                    do_users.append(arguments[i])
            users = []
            for i in do_users:
                idp = await getID(i)
                if idp:
                    users.append(idp)
            kick_users_list = []
            for i in users:
                if await equals_roles(user_id, i, chat_id) < 2:
                    await message.answer(f"У @id{i} уровень прав выше!", disable_mentions=1)
                else:
                    try:
                        await bot.api.messages.remove_chat_user(chat_id, i)
                        info = await bot.api.users.get(int(i))
                        kick_users_list.append(f"@id{i} ({info[0].first_name})")
                    except:
                        pass
            kick_users = ", ".join(kick_users_list)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил пользователей: {kick_users}", disable_mentions=1)
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['quiet', 'silence', 'тишина']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            silence = await quiet(chat_id)
            if silence: await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) режим тишины!")
            else: await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) режим тишины!")

        if command in ['skick', 'снят', 'скик']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете исключить данного пользователя!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>")
                return True

            reason = await get_string(arguments, arg)

            for i in pull:
                try:
                    await bot.api.messages.remove_chat_user(i, user)
                    if reason:
                        await bot.api.messages.send(peer_id=2000000000+i, message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) @id{user} ({await get_user_name(user, chat_id)}) в сетке бесед\nПричина: {reason}", disable_mentions=1, random_id=0)
                    else:
                        await bot.api.messages.send(peer_id=2000000000 + i,message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) @id{user} ({await get_user_name(user, chat_id)}) в сетке бесед",disable_mentions=1, random_id=0)
                except:
                    if i == chat_id:
                        try:
                            if reason: await bot.api.messages.send(peer_id=2000000000 + i,message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) @id{user} ({await get_user_name(user, chat_id)}) в сетке бесед\nПричина: {reason}",disable_mentions=1, random_id=0)
                            else:
                                await bot.api.messages.send(peer_id=2000000000 + i,message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) @id{user} ({await get_user_name(user, chat_id)}) в сетке бесед",disable_mentions=1, random_id=0)
                        except: pass
            if not chat_id in pull: await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) @id{user} ({await get_user_name(user, chat_id)}) в сетке бесед",disable_mentions=1)
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['sban', 'сбан']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете заблокировать данного пользователя!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>")
                return True

            reason = await get_string(arguments, arg)
            if not reason:
                await message.reply("Укажите причину блокировки!")
                return True

            for i in pull:
                try:
                    await ban(user, user_id, i, reason)
                    await bot.api.messages.remove_chat_user(i, user)
                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("Снять бан", {"command": "unban", "user": user, "chatId": chat_id}),
                             color=KeyboardButtonColor.POSITIVE)
                    )
                    await bot.api.messages.send(peer_id=2000000000+i, message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал(-а) @id{user} ({await get_user_name(user, chat_id)}) в сетке бесед\nПричина: {reason}", disable_mentions=1, random_id=0, keyboard=keyboard)
                except:
                    if i == chat_id:
                        try:
                            if reason: await bot.api.messages.send(peer_id=2000000000 + i,message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал(-а) @id{user} ({await get_user_name(user, chat_id)}) в сетке бесед\nПричина: {reason}",disable_mentions=1, random_id=0)
                            else:
                                await bot.api.messages.send(peer_id=2000000000 + i,message=f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал(-а) @id{user} ({await get_user_name(user, chat_id)}) в сетке бесед",disable_mentions=1, random_id=0)
                        except: pass

            if not chat_id in pull: await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал(-а) @id{user} ({await get_user_name(user, chat_id)}) в сетке бесед\nПричина: {reason}", disable_mentions=1)

            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['sunban', 'санбан', 'сунбан']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>")
                return True

            for i in pull:
                try: await unban(user, i)
                except: pass

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) разблокировал(-а) @id{user} ({await get_user_name(user, chat_id)}) в сетке бесед")

        if command in ['addsenmoder', 'senmoder']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать роль данному пользователю!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 2)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права старшего модератора @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['rnickall', 'allrnick', 'arnick', 'mrnick']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            await rnickall(chat_id)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) очистил(-а) ники в беседе", disable_mentions=1)

        if command in ['sremovenick', 'srnick']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>")
                return True

            for i in pull:
                try: await rnick(user, i)
                except: pass

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) убрал(-а) ник @id{user} (пользователю) в сетке бесед", disable_mentions=1)

        if command in ['addadmin', 'admin']:
            if await get_role(user_id, chat_id) < 4:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать роль данному пользователю!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 3)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права администратора @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['pullinfo', 'pulli']:
            if await get_role(user_id, chat_id) < 4:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull: pull_str = "Беседа не находится в связке!"
            else: pull_str = f"ID сетки бесед: {await get_pull_id(chat_id)} | Всего бесед в сетке: {len(pull)}"

            await message.reply(f"Информация о сетке бесед\n{pull_str}")

        if command in ['demote']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online_info", "online"])
            users = json.loads(users.json())
            for i in users["profiles"]:
                if not i['id'] == user_id and await get_role(i['id'], chat_id) < 1:
                    try: await bot.api.messages.remove_chat_user(chat_id, i['id'])
                    except: pass

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) всех участников без ролей!", disable_mentions=1)

        if command in ['banwords', 'bws', 'банворды']:
            if await get_role(user_id, chat_id) < 4:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if len(arguments) < 2:
                bwss = await get_banwords(chat_id)
                bwss_str = ', '.join(bwss)
                await message.reply(f"Список запрещенных слов в беседе\n{bwss_str}\n\nУдалить слово: «/{command} delete <слово>»\nДобавить слово: «/{command} <слово>»")
            else:
                if arguments_lower[1] in ['удалить', 'clear', 'delete'] and len(arguments) > 2:
                    slovo = arguments_lower[2]
                    await banwords(slovo, True, chat_id)
                    await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) удалил(-а) слово из списка запрещенных")
                else:
                    slovo = arguments_lower[1]
                    await banwords(slovo, False, chat_id)
                    await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) добавил(-а) слово в список запрещенных")

        if command in ['filter']:
            if await get_role(user_id, chat_id) < 4:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if await get_filter(chat_id):
                await set_filter(chat_id, 0)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) фильтр запрещенных слов", disable_mentions=1)
            else:
                await set_filter(chat_id, 1)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) фильтр запрещенных слов", disable_mentions=1)

        if command in ['sremoverole', 'srrole']:
            if await get_role(user_id, chat_id) < 4:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете снять роль данному пользователю!", disable_mentions=1)
                return True

            pull = await get_pull_chats(chat_id)
            if not pull:
                await message.reply("Сначала укажите сетку бесед, используя /pull <число>")
                return True

            for i in pull:
                try: await roleG(user, i, 0)
                except: pass

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) забрал(-а) роль у @id{user} (пользователя) в сетке бесед", disable_mentions=1)

        if command in ['antiflood', 'af']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if await get_antiflood(chat_id):
                await set_antiflood(chat_id, 0)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) режим антифлуда", disable_mentions=1)
            else:
                await set_antiflood(chat_id, 1)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) режим антифлуда", disable_mentions=1)

        if command in ['welcome', 'welcometext', 'wtext']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if len(arguments) < 2:
                await message.reply(f"Укажите текст приветсвия, либо напишите «off»\n\nАктивный текст: {await get_welcome(chat_id)}\n\n«%u» - заменяется на @id пользователя\n«%n» - заменяется на тег с именем пользователя\n«%i» - заменяется на @id пригласившего\n«%p» - заменяется на тег с именем пригласившего")
                return True

            text = await get_string(arguments, 1)
            await set_welcome(chat_id, text)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) изменил(-а) приветствие в беседе")

        if command in ['invite']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            result = await invite_kick(chat_id, True)
            if result: await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) функцию приглашения модераторами")
            else: await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) функцию приглашения модераторами")

        if command in ['leave']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            result = await leave_kick(chat_id, True)
            if result: await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) функцию исключения при выходе")
            else: await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) функцию исключения при выходе")

        if command in ['addsenadmin', 'addsenadm', 'senadm', 'senadmin']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message: user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать роль данному пользователю!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 4)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права старшего администратора @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)

        if command in ['pull', 'setpull']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if len(arguments) < 2:
                await message.reply("Укажите ID сетки бесед. 0 - удалить привязку")
                return True

            setka = arguments[1]
            try: setka = int(setka)
            except:
                await message.reply("ID сетки должно быть в виде чила")
                return True

            if setka < 0:
                await message.reply("ID сетки не должен быть меньше нуля")
                return True
            if setka > 2000:
                await message.reply("ID сетки не должен быть больше 2000")
                return True

            await set_pull(chat_id, setka)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) изменил(-а) сетку беседы на {setka}")

        if command in ['news']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            reason = await get_string(arguments, 1)
            if not reason:
                await message.reply("Укажите текст!")
                return True

            peer_ids = await get_all_peerids()
            for i in peer_ids:
                try: await bot.api.messages.send(peer_id=i, message=reason, disable_mentions=1, random_id=0)
                except: pass

        if command in ['szov', 'serverzov', 'сзов']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            reason = await get_string(arguments, 1)
            if not reason:
                await message.reply("Укажите причину вызова!")
                return True

            if not await get_pull_chats(chat_id): return await message.reply("Сначала укажите сетку бесед!")

            for i in await get_pull_chats(chat_id):
                users = await bot.api.messages.get_conversation_members(peer_id=2000000000+i, fields=["online_info", "online"])
                users = json.loads(users.json())
                user_f = []
                gi = 0
                for b in users["profiles"]:
                    if not b['id'] == user_id:
                        gi = gi + 1
                        if gi <= 100:
                            user_f.append(f"@id{b['id']} (🖤)")
                zov_users = ''.join(user_f)

                await bot.api.messages.send(peer_id=2000000000+i, message=f"🔔 Вы были вызваны @id{user_id} (администратором) бесед\n\n{zov_users}\n\n❗ Причина вызова: {reason}", random_id=0)

        if command in ['editowner', 'owner', 'setowner', 'владелец']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            confirm_arg = 2
            if message.reply_message:
                user = message.reply_message.from_id
                confirm_arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                confirm_arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if user == user_id: return await message.reply("Вы не можете передать права самому себе!")

            if len(arguments) <= confirm_arg: return await message.reply("Для подтверждения напишите confirm или подтвердить.\nПример: /владелец @id1 confirm")
            if arguments_lower[confirm_arg] not in ["confirm", "подтвердить"]:
                return await message.reply("Для подтверждения напишите confirm или подтвердить.\nПример: /владелец @id1 confirm")

            await set_onwer(user, chat_id)
            await roleG(user_id, chat_id, 4)

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) успешно передал(-a) права владельца бота в беседе пользователю @id{user} ({await get_user_name(user, chat_id)})\n@id{user_id} ({await get_user_name(user_id, chat_id)}) выданы права Старшего Администратора.", disable_mentions=1)

        if command in ['srole', 'prole', 'pullrole']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            arg = 2
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if int(user_id) != ACTIVATION_OWNER_ID:
                if await get_role(user_id, chat_id) <= await get_role(user, chat_id): return await message.reply(
                    "Вы не можете взаимодействовать с данным пользователем!")

            if len(arguments) < arg+1: return await message.reply("Укажите аргументы!")

            if not arguments[arg].isdigit(): return await message.reply("Укажите число!")

            level = int(arguments[arg])
            if level >= await get_role(user_id, chat_id): return await message.reply("Вы не можете выдать роль, которая выше вашей!")

            if level < 0: return await message.reply("Нельзя выдать такую роль!")

            if await get_pull_id(chat_id) == 0: return await message.reply("Сначала укажите сетку бесед, используя /pull")

            chats = await get_pull_chats(chat_id)

            print(chats)

            for i in chats:
                await roleG(user, i, level)

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) уровень прав {level} в сетке бесед пользователю @id{user} ({await get_user_name(user, chat_id)})")

        if command in ['masskick']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online_info", "online"])
            kicked = 0
            for member in users.items:
                uid = member.member_id
                if uid < 0: continue
                if uid == user_id: continue
                if await get_role(uid, chat_id) > 0: continue
                try:
                    await bot.api.messages.remove_chat_user(chat_id=chat_id, user_id=uid)
                    kicked += 1
                except: pass
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) {kicked} участников без ролей", disable_mentions=1)

        if command in ['rroleall']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            sql.execute("DELETE FROM user_roles WHERE chat_id = ?", (chat_id,))
            database.commit()
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) очистил(-а) все роли в беседе", disable_mentions=1)

        if command in ['gsync']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            await set_gsync(chat_id, 1)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) глобальную синхронизацию бесед", disable_mentions=1)

        if command in ['gunsync']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            await set_gsync(chat_id, 0)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) отключил(-а) глобальную синхронизацию бесед", disable_mentions=1)

        if command in ['settingsmute', 'муtесеттингс']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if await get_mute_warns(chat_id):
                await set_mute_warns(chat_id, 0)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) выдачу варнов за сообщения в муте", disable_mentions=1)
            else:
                await set_mute_warns(chat_id, 1)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) выдачу варнов за сообщения в муте", disable_mentions=1)

        if command in ['защита']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if await get_protect(chat_id):
                await set_protect(chat_id, 0)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) защиту от сторонних сообществ", disable_mentions=1)
            else:
                await set_protect(chat_id, 1)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) защиту от сторонних сообществ", disable_mentions=1)

        if command in ['миниигры']:
            if await get_role(user_id, chat_id) < 7:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if await get_minigames(chat_id):
                await set_minigames(chat_id, 0)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) мини-игры в беседе", disable_mentions=1)
            else:
                await set_minigames(chat_id, 1)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) мини-игры в беседе", disable_mentions=1)

        if command in ['setform', 'настройкаанкеты']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!")
                return True
            
            sql.execute("SELECT title, questions, is_open FROM forms WHERE chat_id = ?", (chat_id,))
            form = sql.fetchone()
            
            if not form:
                title = await get_string(arguments, 1) or "Основная анкета"
                sql.execute("INSERT OR IGNORE INTO forms (chat_id, title, questions, is_open, target_peer_id) VALUES (?, ?, ?, ?, ?)",
                            (chat_id, title, json.dumps([], ensure_ascii=False), 0, peer_id))
                database.commit()
                msg = f"✅ Анкета «{title}» инициализирована."
            else:
                msg = f"⚙️ Настройка анкеты «{form[0]}»"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("➕ Добавить вопрос", {"command": "form_add_q", "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("🗑 Очистить вопросы", {"command": "form_clear_q", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("🎯 Целевой чат", {"command": "form_set_target", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("📝 Название", {"command": "form_set_title", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .row()
                .add(Callback("🔓 Открыть", {"command": "form_toggle", "val": 1, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("🔒 Закрыть", {"command": "form_toggle", "val": 0, "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("🧹 Очистить заявки", {"command": "form_clear_apps", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
            )
            
            await message.reply(msg, keyboard=keyboard)

    if command == "form_clear_apps":
        if await get_role(user_id, chat_id) < 5:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
        sql.execute("DELETE FROM applications WHERE chat_id = ?", (chat_id,))
        database.commit()
        await bot.api.messages.send(peer_id=message.object.peer_id, message="🧹 Все заявки этой беседы были удалены.", random_id=0)

        if command in ['getapps', 'заявки']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!")
                return True
            
            sql.execute("SELECT app_id, user_id, date FROM applications WHERE chat_id = ? ORDER BY app_id DESC LIMIT 5", (chat_id,))
            apps = sql.fetchall()
            if not apps:
                await message.reply("Заявок пока нет.")
                return True
            
            msg = "📥 Последние 5 заявок:\n\n"
            keyboard = Keyboard(inline=True)
            for app in apps:
                user_name = await get_user_name(app[1], chat_id)
                msg += f"🆔 {app[0]} | {app[2]} | [id{app[1]}|{user_name}]\n"
                keyboard.add(Callback(f"🔎 {app[0]}", {"command": "view_app_btn", "id": app[0], "chatId": chat_id}))
            
            await message.reply(msg, keyboard=keyboard)




    else:
        if user_id < 1: return True
        if await check_chat(chat_id):
            if await get_mute(user_id, chat_id) and not await checkMute(chat_id, user_id):
                try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id)
                except: pass
                if await get_mute_warns(chat_id):
                    new_warns = await warn(chat_id, user_id, 0, 'Написание сообщений в муте')
                    await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) получил(-а) варн за написание в муте. Варнов: {new_warns}", disable_mentions=1)
            elif await check_quit(chat_id) and await get_role(user_id, chat_id) < 1:
                try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id)
                except: pass
                print(await get_role(user_id, chat_id) < 1)
            else:
                if await get_filter(chat_id):
                    bws = await get_banwords(chat_id)
                    for i in bws:
                        if i in message.text.lower() and await get_role(user_id, chat_id) < 1:
                            await mute(user_id, chat_id, 'Бот', 'Написание запрещенных слов', 30)
                            keyboard = (
                                Keyboard(inline=True)
                                .add(Callback("Снять мут", {"command": "unmute", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                            )
                            await message.reply(f"@id{user_id} (Пользователь) получил(-а) мут на 30 минут за написание запрещенного слова!", disable_mentions=1, keyboard=keyboard)
                            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id,delete_for_all=True, cmids=message.conversation_message_id)
                            except: pass
                            return True

            await new_message(user_id, message.message_id, message.conversation_message_id, chat_id)
            if await get_spam(user_id, chat_id) and await get_role(user_id, chat_id) < 1:
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Снять мут", {"command": "unmute", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                )
                await message.reply(f"@id{user_id} (Пользователь) получил(-а) мут на 30 минут за спам!", disable_mentions=1, keyboard=keyboard)
                await mute(user_id, chat_id, 'Bot', 'Спам', 30)
                try:await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id,delete_for_all=True, cmids=message.conversation_message_id)
                except: pass



 #в config.json токен бота


@bot.on.private_message()
async def private_handler(message: Message):
    user_id = message.from_id
    text = message.text.strip()

    if int(user_id) == ACTIVATION_OWNER_ID and text.lower() in ['/стоп', '/выключить', '/shutdown', '/стопбот', 'стоп бот']:
        await message.answer("🔴 Бот выключается...")
        sys.exit(0)

    if user_id in form_states:
        state = form_states[user_id]
        
        if text.lower() == "отмена":
            del form_states[user_id]
            await message.answer("❌ Заполнение анкеты отменено.")
            return

        state['answers'].append(text)
        state['current_step'] += 1
        
        if state['current_step'] < len(state['questions']):
            next_q = state['questions'][state['current_step']]
            await message.answer(f"❓ Вопрос {state['current_step'] + 1}/{len(state['questions'])}:\n{next_q}")
        else:
            chat_id = state['chat_id']
            sql.execute("SELECT title, target_peer_id FROM forms WHERE chat_id = ?", (chat_id,))
            form_info = sql.fetchone()
            
            if not form_info:
                await message.answer("❌ Ошибка: анкета более не существует.")
                del form_states[user_id]
                return
                
            title, target_peer_id = form_info
            
            result_msg = f"📩 Новая заявка: {title}\n"
            
            try:
                info = await bot.api.users.get(user_id)
                sender_name = info[0].first_name if info else "Пользователь"
            except:
                sender_name = "Пользователь"
                
            result_msg += f"👤 Отправитель: [id{user_id}|{sender_name}]\n\n"
            
            for i, q in enumerate(state['questions']):
                result_msg += f"🔹 {q}\n— {state['answers'][i]}\n\n"
            
            date_now = str(datetime.now()).split('.')[0]
            sql.execute("INSERT INTO applications (chat_id, user_id, answers, date) VALUES (?, ?, ?, ?)", 
                        (chat_id, user_id, json.dumps(state['answers'], ensure_ascii=False), date_now))
            database.commit()
            
            try:
                await bot.api.messages.send(peer_id=target_peer_id, message=result_msg, random_id=0)
                await message.answer("✅ Ваша заявка успешно отправлена! Ожидайте рассмотрения.")
            except Exception as e:
                print(f"Error sending application: {e}")
                await message.answer("⚠️ Заявка сохранена, но не удалось уведомить администрацию. Они увидят её в списке.")
            
            del form_states[user_id]
        return

    if text.lower().startswith("подать"):
        parts = text.split()
        if len(parts) < 2:
            await message.answer("📝 Чтобы подать заявку, введите: подать [ID чата]\n"
                                 "ID чата можно узнать в самой беседе.")
            return
            
        try:
            chat_id = int(parts[1])
        except:
            await message.answer("❌ Неверный формат ID чата.")
            return
            
        sql.execute("SELECT title, questions, is_open FROM forms WHERE chat_id = ?", (chat_id,))
        form = sql.fetchone()
        
        if not form:
            await message.answer("❌ В этом чате не настроены анкеты.")
            return
            
        title, questions_json, is_open = form
        if not is_open:
            await message.answer(f"❌ Прием заявок на «{title}» в данный момент закрыт.")
            return
            
        questions = json.loads(questions_json)
        if not questions:
            await message.answer("❌ В анкете нет вопросов. Обратитесь к администратору.")
            return
            
        form_states[user_id] = {
            'chat_id': chat_id,
            'questions': questions,
            'answers': [],
            'current_step': 0
        }
        
        await message.answer(f"📝 Начинаем заполнение анкеты: {title}\n"
                             f"Всего вопросов: {len(questions)}\n"
                             f"Напишите «отмена» для выхода.\n\n"
                             f"❓ Вопрос 1:\n{questions[0]}")
        return

    await message.answer("👋 Привет! Чтобы подать заявку в одну из бесед, напишите:\n"
                         "👉 подать [ID чата]")

# bot.run_forever() слушает LongPoll VK и блокирует процесс навсегда — но
# если соединение "слетает" (обрыв сети, ошибка VK API, таймаут и т.п.) и
# исключение вылетает наружу, раньше это убивало весь процесс: бот
# перестаёт отвечать во ВСЕХ беседах, где он был добавлен, пока кто-то не
# перезапустит его руками. Все данные (активированные беседы, роли, баланс,
# дуэли и т.д.) хранятся в database.db и от перезапуска не страдают, поэтому
# достаточно не дать процессу упасть насовсем — заново запускать
# run_forever() и продолжать работу в тех же чатах автоматически.
import time as _time

_reconnect_delay = 5
while True:
    try:
        bot.run_forever()
        break  # штатное завершение (например, по сигналу) — не переподключаемся
    except Exception as e:
        print(f"[reconnect] Бот слетел, переподключение через {_reconnect_delay}с: {e}", file=sys.stderr)
        _time.sleep(_reconnect_delay)
        _reconnect_delay = min(_reconnect_delay * 2, 60)
