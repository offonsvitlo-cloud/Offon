import hashlib
import hmac
import json
from html import escape as html_escape
import os
import re
import sys
import time
import traceback
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from flask import Flask, g, has_request_context, jsonify, request, send_from_directory

KYIV_TZ = ZoneInfo('Europe/Kyiv')
DTEK_URL = 'https://www.dtek-dnem.com.ua/ua/shutdowns'
DTEK_AJAX_URL = 'https://www.dtek-dnem.com.ua/ua/ajax'
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
)

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / 'dist'
DATA_DIR = BASE_DIR / 'dtek_data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

STORE_PATH = DATA_DIR / 'store.json'
CACHE_PATH = DATA_DIR / 'dtek_cache.json'
LOG_PATH = DATA_DIR / 'app.log'


def bot_configs() -> list[dict]:
    """Основной бот и (необязательно) резервный. Оба работают одновременно, данные общие."""
    out = []
    t1 = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if t1:
        out.append({'key': 'main', 'token': t1, 'username': os.environ.get('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')})
    t2 = os.environ.get('TELEGRAM_BOT2_TOKEN', '').strip()
    if t2:
        out.append({'key': 'reserve', 'token': t2, 'username': os.environ.get('TELEGRAM_BOT2_USERNAME', '').strip().lstrip('@')})
    return out


def bot_by_key(key: str | None) -> dict:
    cfgs = bot_configs()
    for c in cfgs:
        if c['key'] == key:
            return c
    return cfgs[0] if cfgs else {'key': 'main', 'token': '', 'username': os.environ.get('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')}


def current_bot() -> dict:
    """Бот, через который пользователь сейчас открыл Mini App (определяется по подписи initData)."""
    key = getattr(g, 'bot_key', None) if has_request_context() else None
    return bot_by_key(key)


_CHAT_PREF: dict[str, str] = {}  # chat_id -> каким ботом пользователю писать в первую очередь


def log_event(msg: object) -> None:
    """Пишет в stderr (error.log PythonAnywhere) и в dtek_data/app.log (последние 200 строк).
    Токен бота из текста вырезается."""
    text = str(msg)
    for cfg in bot_configs():
        text = text.replace(cfg['token'], '<token>')
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC  {text[:600]}"
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
    try:
        old = LOG_PATH.read_text(encoding='utf-8').splitlines()[-199:] if LOG_PATH.exists() else []
        LOG_PATH.write_text('\n'.join(old + [line]) + '\n', encoding='utf-8')
    except Exception:
        pass

DEFAULT_GROUPS = [
    'GPV1.1', 'GPV1.2', 'GPV2.1', 'GPV2.2',
    'GPV3.1', 'GPV3.2', 'GPV4.1', 'GPV4.2',
    'GPV5.1', 'GPV5.2', 'GPV6.1', 'GPV6.2',
]
DEFAULT_GROUP_NAMES = {g: f"Черга {g.replace('GPV', '')}" for g in DEFAULT_GROUPS}

app = Flask(__name__, static_folder=str(DIST_DIR), static_url_path='')


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_store() -> dict:
    if STORE_PATH.exists():
        try:
            return json.loads(STORE_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        'subscribers': {},
        'alerts': [],
        'sentKeys': {},
        'channelConfig': {
            'enabled': True,
            'notifyBeforeOffMin': 15,
            'notifyBeforeOnMin': 15,
            'notifyScheduleChange': True,
            'queues': list(DEFAULT_GROUPS),
        },
    }


ALERT_TTL_SEC = 2 * 86400


def _alert_age_sec(evt: dict) -> float:
    try:
        created = datetime.fromisoformat(str(evt.get('createdAt', '')).replace('Z', '+00:00'))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created).total_seconds()
    except Exception:
        return 0.0


def save_store(state: dict) -> None:
    cutoff = time.time() - 48 * 3600
    state['sentKeys'] = {
        k: v for k, v in state.get('sentKeys', {}).items() if v >= cutoff
    }
    # события и сообщения автоматически удаляются через 2 суток
    state['alerts'] = [a for a in state.get('alerts', []) if _alert_age_sec(a) <= ALERT_TTL_SEC]
    tmp = STORE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(STORE_PATH)


def extract_json_var(html: str, prefix: str):
    marker = f'{prefix} = '
    idx = html.find(marker)
    if idx == -1:
        return None
    pos = idx + len(marker)
    while pos < len(html) and html[pos] not in '{[':
        pos += 1
    if pos >= len(html):
        return None

    open_ch = html[pos]
    close_ch = '}' if open_ch == '{' else ']'
    depth = 0
    in_str = False
    esc = False
    for i in range(pos, len(html)):
        ch = html[i]
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return json.loads(html[pos : i + 1])
    return None


class DtekClient:
    def __init__(self) -> None:
        self.streets: dict[str, list[str]] = {}
        self.snapshot: dict = {
            'today': int(time.time()),
            'update': '',
            'fetchedAt': '',
            'source': 'cached',
            'groups': list(DEFAULT_GROUPS),
            'days': {},
            'groupNames': dict(DEFAULT_GROUP_NAMES),
        }
        self.session = requests.Session()
        self.csrf_token = ''
        self.last_error: str | None = None
        self._load_cache()

    def _load_cache(self) -> None:
        if CACHE_PATH.exists():
            try:
                raw = json.loads(CACHE_PATH.read_text(encoding='utf-8'))
                self.streets = raw.get('streets', {})
                if raw.get('snapshot'):
                    self.snapshot = raw['snapshot']
                    self.snapshot['source'] = 'cached'
            except Exception:
                pass

    def _save_cache(self) -> None:
        CACHE_PATH.write_text(
            json.dumps(
                {'streets': self.streets, 'snapshot': self.snapshot},
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )

    def refresh(self) -> tuple[dict, list[dict]]:
        proxy_json = os.environ.get('DTEK_PROXY_JSON_URL', '').strip()
        proxy_base = os.environ.get('DTEK_PROXY_BASE_URL', '').strip().rstrip('/')
        try:
            target_url = proxy_json or (f'{proxy_base}/api/dtek/snapshot' if proxy_base else '')
            if target_url:
                resp = self.session.get(target_url, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data.get('streets'), dict) and data['streets']:
                    self.streets = data['streets']
                prev_days = self.snapshot.get('days', {})
                next_snap = data.get('snapshot', data)
                diffs = compute_diffs(prev_days, next_snap.get('days', {}))
                today_sec = int(next_snap.get('today') or time.time())
                merged_hist = merge_history_days(
                    self.snapshot.get('historyDays') or prev_days,
                    next_snap.get('historyDays') or next_snap.get('days', {}),
                    today_sec,
                )
                next_snap['historyDays'] = merged_hist
                self.snapshot = next_snap
                self.snapshot['source'] = 'live'
                self._save_cache()
                self.last_error = None
                return self.snapshot, diffs

            resp = self.session.get(
                DTEK_URL,
                headers={'User-Agent': USER_AGENT, 'Accept-Language': 'uk-UA,uk;q=0.9'},
                timeout=15,
            )
            resp.raise_for_status()
            html = resp.text

            m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
            if m:
                self.csrf_token = m.group(1)

            streets = extract_json_var(html, 'DisconSchedule.streets')
            if isinstance(streets, dict) and streets:
                self.streets = streets

            preset = extract_json_var(html, 'DisconSchedule.preset') or {}
            fact = extract_json_var(html, 'DisconSchedule.fact') or {}
            next_days = fact.get('data') or {}
            if not next_days:
                raise RuntimeError('DisconSchedule.fact is empty')

            prev_days = self.snapshot.get('days', {})
            diffs = compute_diffs(prev_days, next_days)

            group_names = dict(DEFAULT_GROUP_NAMES)
            group_names.update(preset.get('sch_names') or {})
            groups = sorted(
                {g for day_map in next_days.values() for g in day_map.keys()}
                | set(DEFAULT_GROUPS)
            )
            today_sec = int(fact.get('today') or time.time())
            merged_hist = merge_history_days(
                self.snapshot.get('historyDays') or prev_days,
                next_days,
                today_sec,
            )

            self.snapshot = {
                'today': today_sec,
                'update': fact.get('update', ''),
                'fetchedAt': now_iso(),
                'source': 'live',
                'groups': groups,
                'days': next_days,
                'historyDays': merged_hist,
                'groupNames': group_names,
            }
            self._save_cache()
            self.last_error = None
            return self.snapshot, diffs
        except Exception as exc:
            self.last_error = str(exc)
            return self.snapshot, []

    def lookup_houses(self, city: str, street: str) -> dict:
        proxy_base = os.environ.get('DTEK_PROXY_BASE_URL', '').strip().rstrip('/')
        if proxy_base:
            try:
                resp = self.session.get(
                    f'{proxy_base}/api/dtek/houses',
                    params={'city': city, 'street': street},
                    timeout=15,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                return {'houses': {}, 'error': f'Ошибка прокси ДТЕК: {exc}'}

        if not self.csrf_token:
            self.refresh()
        try:
            payload = {
                'method': 'getHomeNum',
                'data[0][name]': 'city',
                'data[0][value]': city,
                'data[1][name]': 'street',
                'data[1][value]': street,
            }
            if self.snapshot.get('update'):
                payload['data[2][name]'] = 'updateFact'
                payload['data[2][value]'] = self.snapshot['update']

            resp = self.session.post(
                DTEK_AJAX_URL,
                data=payload,
                headers={
                    'User-Agent': USER_AGENT,
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRF-Token': self.csrf_token,
                    'Referer': DTEK_URL,
                    'Origin': 'https://www.dtek-dnem.com.ua',
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            known = set(self.snapshot.get('groups', DEFAULT_GROUPS))
            houses = {}
            for hk, info in (data.get('data') or {}).items():
                queues = info.get('sub_type_reason') or []
                sched_q = next((q for q in queues if q in known), None)
                if not sched_q:
                    sched_q = next((q for q in queues if re.match(r'^GPV[1-6]\.[12]$', q)), None)
                houses[hk] = {
                    'queue': sched_q,
                    'queues': queues,
                    'subType': info.get('sub_type', ''),
                    'startDate': info.get('start_date', ''),
                    'endDate': info.get('end_date', ''),
                    'type': info.get('type', ''),
                }
            return {'houses': houses}
        except Exception as exc:
            err_text = str(exc)
            if '403 Forbidden' in err_text or 'ProxyError' in err_text or 'Tunnel connection failed' in err_text:
                return {
                    'houses': {},
                    'proxyBlocked': True,
                    'error': 'Бесплатный тариф PythonAnywhere блокирует прямой поиск дома на сайте ДТЕК. Выберите свою очередь (1.1–6.2) вручную ниже.',
                }
            return {'houses': {}, 'error': err_text}


def merge_history_days(prev_hist: dict | None, next_days: dict | None, today_sec: int) -> dict:
    cutoff = today_sec - 14 * 86400
    merged = {}
    for k, v in (prev_hist or {}).items():
        try:
            if int(k) >= cutoff:
                merged[str(k)] = v
        except Exception:
            pass
    for k, v in (next_days or {}).items():
        merged[str(k)] = v
    return merged


def state_to_half_hours(state: str | None) -> tuple[str, str]:
    mapping = {
        'yes': ('on', 'on'),
        'no': ('off', 'off'),
        'first': ('off', 'on'),
        'second': ('on', 'off'),
        'maybe': ('maybe', 'maybe'),
        'mfirst': ('maybe', 'on'),
        'msecond': ('on', 'maybe'),
    }
    return mapping.get(state or 'yes', ('on', 'on'))


def compute_diffs(prev_days: dict, next_days: dict) -> list[dict]:
    if not prev_days:
        return []
    diffs = []
    for day_ts, next_map in next_days.items():
        prev_map = prev_days.get(day_ts)
        if not prev_map:
            continue
        for queue, next_hours in next_map.items():
            prev_hours = prev_map.get(queue)
            if not prev_hours:
                continue
            changed = []
            for h in range(1, 25):
                k = str(h)
                old_s = prev_hours.get(k, 'yes')
                new_s = next_hours.get(k, 'yes')
                if old_s != new_s:
                    changed.append({
                        'hour': h,
                        'timeRange': f'{h - 1:02d}:00–{h:02d}:00',
                        'oldState': old_s,
                        'newState': new_s,
                    })
            if changed:
                dt = datetime.fromtimestamp(int(day_ts), tz=KYIV_TZ)
                diffs.append({
                    'dayTimestamp': day_ts,
                    'dayLabel': dt.strftime('%d.%m'),
                    'queue': queue,
                    'changes': changed,
                })
    return diffs


def analyze_queue_now(snapshot: dict, queue: str) -> dict:
    now_kyiv = datetime.now(KYIV_TZ)
    now_min = now_kyiv.hour * 60 + now_kyiv.minute
    days = snapshot.get('days', {})
    sorted_keys = sorted(days.keys(), key=lambda x: int(x))
    # «Сегодня» берём по реальной дате в Киеве, а не из snapshot['today']:
    # если данные ДТЕК давно не обновлялись, чужой день не должен
    # превращаться в сегодняшний и слать ложные уведомления.
    today_key = None
    for k in sorted_keys:
        try:
            if datetime.fromtimestamp(int(k), KYIV_TZ).date() == now_kyiv.date():
                today_key = k
                break
        except Exception:
            continue
    if today_key is None:
        return {'status': 'unknown', 'minutesUntilChange': None, 'nextTarget': None, 'nextLabel': None, 'stale': True}

    def expand(day_key: str | None) -> list[tuple[int, str]]:
        if not day_key or day_key not in days:
            return []
        hmap = days[day_key].get(queue, {})
        res = []
        for h in range(1, 25):
            a, b = state_to_half_hours(hmap.get(str(h), 'yes'))
            res.append(((h - 1) * 60, a))
            res.append(((h - 1) * 60 + 30, b))
        return res

    today_slots = expand(today_key)
    if not today_slots:
        return {'status': 'on', 'minutesUntilChange': None, 'nextTarget': None, 'nextLabel': None}

    tomorrow_key = next((k for k in sorted_keys if int(k) > int(today_key)), None)
    tomorrow_slots = [(1440 + m, st) for m, st in expand(tomorrow_key)]
    combined = today_slots + tomorrow_slots

    cur_idx = min(47, now_min // 30)
    cur_status = today_slots[cur_idx][1]
    cur_is_off = cur_status == 'off'

    next_min = None
    next_target = None
    for idx in range(cur_idx + 1, len(combined)):
        slot_min, slot_st = combined[idx]
        if (slot_st == 'off') != cur_is_off:
            next_min = slot_min
            next_target = 'off' if slot_st == 'off' else 'on'
            break

    if next_min is None:
        return {'status': cur_status, 'minutesUntilChange': None, 'nextTarget': None, 'nextLabel': None}

    rem = max(0, next_min - now_min)
    disp_min = next_min % 1440
    next_label = f'{disp_min // 60:02d}:{disp_min % 60:02d}'
    return {
        'status': cur_status,
        'minutesUntilChange': rem,
        'nextTarget': next_target,
        'nextLabel': next_label,
        'nextAbsMin': next_min,
        'dateStr': now_kyiv.strftime('%d.%m.%Y'),
    }


def _send_via(cfg: dict, chat_id: str, html: str, with_app_button: bool) -> bool:
    payload: dict = {
        'chat_id': chat_id,
        'text': html,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    app_url = os.environ.get('APP_URL', '').strip()
    is_channel_or_group = chat_id.startswith(('-', '@'))
    if with_app_button:
        if is_channel_or_group:
            bot_user = cfg.get('username', '')
            link = f'https://t.me/{bot_user}' if bot_user else (app_url if app_url.startswith('https://') else '')
            if link:
                payload['reply_markup'] = {
                    'inline_keyboard': [[{'text': 'Настроить свой адрес в боте', 'url': link}]]
                }
        elif app_url.startswith('https://'):
            payload['reply_markup'] = {
                'inline_keyboard': [[{'text': 'Відкрити Mini App', 'web_app': {'url': app_url}}]]
            }
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{cfg['token']}/sendMessage",
            json=payload,
            timeout=10,
        )
        ok = r.ok and r.json().get('ok', False)
        if not ok:
            log_event(f"tg_send FAILED bot={cfg['key']} chat={chat_id} HTTP {r.status_code}: {r.text[:200]}")
        return ok
    except Exception as exc:
        log_event(f"tg_send ERROR bot={cfg['key']} chat={chat_id}: {exc!r}")
        return False


def tg_send(chat_id: str, html: str, with_app_button: bool = True, bot_key: str | None = None) -> bool:
    """Отправка сообщения. Сначала ботом, которым пользователь пользовался (или принявшим запрос),
    при неудаче резервным: пользователь, который не запускал основного бота, всё равно получит сообщение."""
    cfgs = bot_configs()
    if not cfgs or not chat_id:
        return False
    chat_id = str(chat_id)
    pref = bot_key or (getattr(g, 'bot_key', None) if has_request_context() else None) or _CHAT_PREF.get(chat_id)
    for cfg in sorted(cfgs, key=lambda c: 0 if c['key'] == pref else 1):
        if _send_via(cfg, chat_id, html, with_app_button):
            _CHAT_PREF[chat_id] = cfg['key']
            return True
    return False


def parse_tg_init_data(init_data: str) -> dict | None:
    """Проверяет подпись Telegram (любым из настроенных ботов) и возвращает все поля initData;
    ключ _bot говорит, через какого бота открыт Mini App."""
    if not init_data:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        user_raw = parsed.get('user')
        if not user_raw:
            return None
        cfgs = bot_configs()
        tg_hash = parsed.pop('hash', None)
        matched = None
        if cfgs:
            # Подпись обязательна: без неё любой мог бы выдать себя за чужой tg:ID
            if not tg_hash:
                return None
            check_str = '\n'.join(f'{k}={v}' for k, v in sorted(parsed.items()))
            for cfg in cfgs:
                secret = hmac.new(b'WebAppData', cfg['token'].encode(), hashlib.sha256).digest()
                calc = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
                if hmac.compare_digest(calc, tg_hash):
                    matched = cfg['key']
                    break
            if not matched:
                return None
        parsed['user'] = json.loads(user_raw)
        parsed['_bot'] = matched or 'main'
        return parsed
    except Exception:
        return None


def verify_tg_init_data(init_data: str) -> dict | None:
    parsed = parse_tg_init_data(init_data)
    return parsed['user'] if parsed else None


def get_start_param() -> str:
    parsed = parse_tg_init_data(request.headers.get('x-telegram-init-data', ''))
    return str((parsed or {}).get('start_param') or '')


def resolve_sub_id() -> tuple[str, dict | None]:
    init_data = request.headers.get('x-telegram-init-data', '')
    if init_data:
        parsed = parse_tg_init_data(init_data)
        u = parsed['user'] if parsed else None
        if u and u.get('id'):
            g.bot_key = parsed.get('_bot') or 'main'
            _CHAT_PREF[str(u['id'])] = g.bot_key
            return f"tg:{u['id']}", u
    cid = request.headers.get('x-subscriber-id', '').strip()
    if cid and re.match(r'^[a-zA-Z0-9:_-]{4,64}$', cid):
        return (cid if cid.startswith(('web:', 'tg:')) else f'web:{cid}'), None
    return 'web:default', None


def get_or_create_sub(state: dict, sub_id: str, tg_user: dict | None = None) -> dict:
    subs = state.setdefault('subscribers', {})
    if sub_id not in subs:
        subs[sub_id] = {
            'id': sub_id,
            'telegramChatId': str(tg_user['id']) if tg_user else None,
            'telegramUsername': tg_user.get('username') if tg_user else None,
            'firstName': tg_user.get('first_name') if tg_user else None,
            'linkCode': uuid.uuid4().hex[:6].upper(),
            'quietHoursEnabled': False,
            'quietStart': '23:00',
            'quietEnd': '07:00',
            'language': 'ru',
            'addresses': [],
            'extraAddressSlots': 0,
            'adsDisabled': False,
            'rewardSlotExpiresAt': None,
            'rewardUsed': False,
            'paidCharges': [],
            'updatedAt': now_iso(),
        }
        save_store(state)
    sub = subs[sub_id]
    changed, _removed, _reasons = enforce_expiry(sub)
    if changed:
        save_store(state)
    return sub


FREE_ADDRESSES = 2
REWARD_HOURS = 24  # на сколько часов даётся 3-й адрес за REWARD_ADS_GOAL просмотренных реклам
REWARD_ADS_GOAL = 10  # сколько реклам нужно посмотреть за сутки (счётчик обнуляется в 00:00 по Киеву)
REWARD_AD_MIN_INTERVAL = 12  # минимум секунд между засчитанными просмотрами (защита от накрутки)
REWARD_MAX_STACK_HOURS = 7 * 24  # бонус можно копить не дальше чем на неделю вперёд
PRO_ADDRESSES_BONUS = 3  # Pro: 2 бесплатных + 3 = 5 адресов
PRO_GRACE_HOURS = 24  # запас после даты окончания подписки (на задержку продления)
PRO_PERIOD_SECONDS = 2592000  # Telegram допускает только 30 дней
REF_BONUS_HOURS = 72  # за каждого приглашённого: +1 адрес на 3 дня
REF_BONUS_MAX_DAYS = 30  # максимум накопленного реферального бонуса


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, str(default)) or default)))
    except ValueError:
        return default


def slot_stars_price() -> int:
    """Цена постоянного слота в звёздах (по умолчанию 50, для теста SLOT_STARS_PRICE=1)."""
    return _env_int('SLOT_STARS_PRICE', 50, 1, 100000)


def pro_stars_price() -> int:
    """Цена Pro за 30 дней (по умолчанию 100, для теста PRO_STARS_PRICE=1)."""
    return _env_int('PRO_STARS_PRICE', 100, 1, 10000)


def kyiv_day() -> str:
    return datetime.now(KYIV_TZ).strftime('%Y-%m-%d')


def _ts(iso_value) -> float:
    if not iso_value:
        return 0.0
    try:
        return datetime.fromisoformat(str(iso_value)).timestamp()
    except Exception:
        return 0.0


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def is_reward_active(sub: dict) -> bool:
    return _ts(sub.get('rewardSlotExpiresAt')) > time.time()


def is_pro_active(sub: dict) -> bool:
    return _ts(sub.get('proUntil')) + PRO_GRACE_HOURS * 3600 > time.time()


def is_ref_bonus_active(sub: dict) -> bool:
    return _ts(sub.get('refBonusUntil')) > time.time()


def get_max_addresses(sub: dict) -> int:
    paid = max(0, int(sub.get('extraAddressSlots') or 0))
    return (
        FREE_ADDRESSES
        + paid
        + (PRO_ADDRESSES_BONUS if is_pro_active(sub) else 0)
        + (1 if is_ref_bonus_active(sub) else 0)
        + (1 if is_reward_active(sub) else 0)
    )


def public_sub(sub: dict) -> dict:
    """То, что видит фронтенд: Pro и реферальный бонус «вшиваются» в extraAddressSlots,
    а у Pro отключается реклама. Бонус за рекламу фронтенд считает сам по rewardSlotExpiresAt."""
    out = dict(sub)
    pro = is_pro_active(sub)
    out['extraAddressSlots'] = (
        max(0, int(sub.get('extraAddressSlots') or 0))
        + (PRO_ADDRESSES_BONUS if pro else 0)
        + (1 if is_ref_bonus_active(sub) else 0)
    )
    if pro:
        out['adsDisabled'] = True
    out['proActive'] = pro
    out['rewardAdCount'] = int(sub.get('rewardAdCount') or 0) if sub.get('rewardAdDay') == kyiv_day() else 0
    out['rewardAdsGoal'] = REWARD_ADS_GOAL
    out['rewardHours'] = REWARD_HOURS
    out['reminders'] = [r for r in (sub.get('reminders') or []) if not r.get('sent')]
    out['reminderLimit'] = None if pro else REMINDER_LIMIT_FREE
    out.pop('referrals', None)
    return out


def enforce_expiry(sub: dict) -> tuple[bool, list[str], list[str]]:
    """Сбрасывает закончившиеся бонусы (реклама, Pro, друг) и удаляет адреса сверх лимита.
    Возвращает (изменилось ли, названия удалённых адресов, причины: reward/pro/ref)."""
    reasons: list[str] = []
    if sub.get('rewardSlotExpiresAt') and not is_reward_active(sub):
        sub['rewardSlotExpiresAt'] = None
        reasons.append('reward')
    if sub.get('refBonusUntil') and not is_ref_bonus_active(sub):
        sub['refBonusUntil'] = None
        reasons.append('ref')
    if sub.get('proUntil') and not is_pro_active(sub) and not sub.get('proLapsed'):
        sub['proLapsed'] = True
        reasons.append('pro')
    if not reasons:
        return False, [], []
    removed: list[str] = []
    limit = get_max_addresses(sub)
    addrs = sub.get('addresses') or []
    if len(addrs) > limit:
        removed = [str(a.get('label') or 'адрес') for a in addrs[limit:]]
        sub['addresses'] = addrs[:limit]
    sub['updatedAt'] = now_iso()
    return True, removed, reasons


def apply_referral(state: dict, new_sub: dict, code: str) -> bool:
    """Новый пользователь пришёл по реферальной ссылке: приглашающему +1 адрес на 3 дня."""
    if not code or new_sub.get('referredBy') or not str(new_sub.get('id', '')).startswith('tg:'):
        return False
    ref = next((x for x in state.get('subscribers', {}).values() if x.get('refCode') == code), None)
    if not ref or ref.get('id') == new_sub.get('id'):
        return False
    refs = ref.setdefault('referrals', [])
    if new_sub['id'] in refs:
        return False
    refs.append(new_sub['id'])
    new_sub['referredBy'] = ref['id']
    now = time.time()
    until = min(max(now, _ts(ref.get('refBonusUntil'))) + REF_BONUS_HOURS * 3600, now + REF_BONUS_MAX_DAYS * 86400)
    ref['refBonusUntil'] = _iso(until)
    ref['updatedAt'] = now_iso()
    log_event(f"referral: {ref['id']} <- {new_sub['id']}, bonus until {ref['refBonusUntil']}")
    if ref.get('telegramChatId'):
        when = datetime.fromtimestamp(until, KYIV_TZ).strftime('%d.%m %H:%M')
        tg_send(ref['telegramChatId'], f'🎉 По вашей ссылке пришёл новый пользователь!\nДоступен +1 адрес до <b>{when}</b>.', False)
    return True


dtek = DtekClient()
dtek.refresh()


def snapshot_for_client(snap: dict) -> dict:
    """Для фронтенда «сегодня» = день, соответствующий реальной дате в Киеве.
    Тогда вкладка «Сьогодні/Завтра» верна, даже если данные ДТЕК давно не обновлялись,
    а «Завтра» появляется сразу, когда график на следующий день есть в данных."""
    try:
        today = datetime.now(KYIV_TZ).date()
        days = snap.get('days') or {}
        for k in days:
            if datetime.fromtimestamp(int(k), KYIV_TZ).date() == today:
                out = dict(snap)
                out['today'] = int(k)
                # «Завтра» показываем всегда: если графика ещё нет, вкладка пустая и приложение пишет «ещё не опубликован»
                tomorrow_date = today + timedelta(days=1)
                has_tomorrow = any(datetime.fromtimestamp(int(x), KYIV_TZ).date() == tomorrow_date for x in days)
                if not has_tomorrow:
                    tm = datetime(tomorrow_date.year, tomorrow_date.month, tomorrow_date.day, tzinfo=KYIV_TZ)
                    out['days'] = dict(days)
                    out['days'][str(int(tm.timestamp()))] = {}
                return out
    except Exception:
        pass
    return snap


@app.route('/api/health')
def api_health():
    return jsonify({'ok': True, 'citiesCount': len(dtek.streets), 'dtekError': dtek.last_error})


def resolve_channel_id() -> str:
    raw = os.environ.get('TELEGRAM_CHANNEL_ID', '').strip()
    if not raw or raw.startswith(('http://', 'https://')):
        return '-1004316754773'
    return raw


def alerts_for_sub(state: dict, sub: dict) -> list[dict]:
    """Только события, связанные с адресами этого пользователя (и не старше 2 суток)."""
    addrs = sub.get('addresses', []) or []
    ids = {a.get('id') for a in addrs}
    queues = {a.get('queue') for a in addrs if a.get('queue')}
    out = []
    for evt in state.get('alerts', []):
        if _alert_age_sec(evt) > ALERT_TTL_SEC:
            continue
        if evt.get('type') == 'channel_broadcast':
            continue
        if evt.get('subscriberId'):
            if evt['subscriberId'] == sub.get('id'):
                out.append(evt)
            continue
        if evt.get('addressId'):
            if evt['addressId'] in ids:
                out.append(evt)
            continue
        q = evt.get('queue')
        if q and q != 'ALL' and q in queues:
            out.append(evt)
    return out


@app.route('/api/bootstrap')
def api_bootstrap():
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    is_new = sub_id not in state.get('subscribers', {})
    sub = get_or_create_sub(state, sub_id, tg_user)
    if is_new and tg_user:
        start_param = get_start_param()
        if start_param.startswith('ref_') and apply_referral(state, sub, start_param[4:]):
            save_store(state)
    token = current_bot()['token']
    channel_id = resolve_channel_id()
    subs_all = list(state.get('subscribers', {}).values())
    return jsonify({
        'subscriber': public_sub(sub),
        'snapshot': snapshot_for_client(dtek.snapshot),
        'alerts': alerts_for_sub(state, sub)[:40],
        'botStatus': {
            'configured': bool(token),
            'botUsername': current_bot()['username'] or None,
            'botsConfigured': len(bot_configs()),
            'channelId': channel_id or None,
            'pollingActive': False,
            'totalSubscribers': len(subs_all),
            'linkedTelegramUsers': sum(1 for s in subs_all if s.get('telegramChatId')),
            'lastPollAt': now_iso(),
            'lastError': dtek.last_error,
            'appUrl': os.environ.get('APP_URL') or None,
        },
        'channelConfig': state.get('channelConfig'),
        'citiesCount': len(dtek.streets),
        'monetization': {
            'freeAddressLimit': 2,
            'extraSlotStars': 50,
            'monetagZoneId': os.environ.get('MONETAG_ZONE_ID', '').strip() or None,
            'monetagSdkUrl': os.environ.get('MONETAG_SDK_URL', '').strip() or 'https://libtl.com/sdk.js',
        },
    })


@app.route('/api/dtek/refresh', methods=['POST'])
def api_dtek_refresh():
    snap, diffs = dtek.refresh()
    if diffs:
        run_notification_tick(diffs=diffs)
    return jsonify({'snapshot': snapshot_for_client(snap), 'changes': diffs, 'citiesCount': len(dtek.streets)})


@app.route('/api/dtek/cities')
def api_dtek_cities():
    q = request.args.get('q', '').strip().lower()
    cities = list(dtek.streets.keys())
    if not q:
        return jsonify({'cities': cities[:35]})
    starts = [c for c in cities if c.lower().startswith(q)]
    contains = [c for c in cities if not c.lower().startswith(q) and q in c.lower()]
    return jsonify({'cities': (starts + contains)[:30]})


@app.route('/api/dtek/streets')
def api_dtek_streets():
    city = request.args.get('city', '')
    q = request.args.get('q', '').strip().lower()
    lst = dtek.streets.get(city, [])
    if not q:
        return jsonify({'streets': lst[:40]})
    starts = [s for s in lst if s.lower().startswith(q)]
    contains = [s for s in lst if not s.lower().startswith(q) and q in s.lower()]
    return jsonify({'streets': (starts + contains)[:40]})


@app.route('/api/dtek/houses')
def api_dtek_houses():
    city = request.args.get('city', '').strip()
    street = request.args.get('street', '').strip()
    if not city or not street:
        return jsonify({'houses': {}, 'error': 'city and street required'}), 400
    return jsonify(dtek.lookup_houses(city, street))


@app.route('/api/subscriber/addresses', methods=['POST'])
def api_add_address():
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    sub = get_or_create_sub(state, sub_id, tg_user)
    max_addrs = get_max_addresses(sub)
    if len(sub.get('addresses', [])) >= max_addrs:
        return jsonify({
            'error': 'Достигнут лимит бесплатных адресов',
            'code': 'LIMIT_REACHED',
            'maxAddresses': max_addrs,
        }), 402
    body = request.get_json(force=True) or {}
    if not body.get('city') or not body.get('queue'):
        return jsonify({'error': 'city and queue required'}), 400

    q = body['queue']
    addr = {
        'id': f'addr_{uuid.uuid4().hex[:8]}',
        'label': (body.get('label') or 'Мой адрес').strip(),
        'city': body['city'].strip(),
        'street': (body.get('street') or '—').strip(),
        'houseNum': (body.get('houseNum') or '—').strip(),
        'queue': q,
        'queueDisplay': body.get('queueDisplay') or dtek.snapshot['groupNames'].get(q, q),
        'enabled': bool(body.get('enabled', True)),
        'notifyBeforeOffMin': int(body.get('notifyBeforeOffMin', 15)),
        'notifyBeforeOnMin': int(body.get('notifyBeforeOnMin', 15)),
        'notifyScheduleChange': bool(body.get('notifyScheduleChange', True)),
        'notifyEmergency': bool(body.get('notifyEmergency', True)),
        'emergencyInfo': body.get('emergencyInfo'),
        'createdAt': now_iso(),
    }
    sub['addresses'].append(addr)
    sub['updatedAt'] = now_iso()
    save_store(state)
    return jsonify({'address': addr, 'subscriber': public_sub(sub)}), 201


@app.route('/api/subscriber/addresses/<addr_id>', methods=['PATCH', 'DELETE'])
def api_modify_address(addr_id: str):
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    sub = get_or_create_sub(state, sub_id, tg_user)
    if request.method == 'DELETE':
        before = len(sub['addresses'])
        sub['addresses'] = [a for a in sub['addresses'] if a['id'] != addr_id]
        if len(sub['addresses']) == before:
            return jsonify({'error': 'Not found'}), 404
        sub['updatedAt'] = now_iso()
        save_store(state)
        return jsonify({'ok': True, 'subscriber': public_sub(sub)})

    body = request.get_json(force=True) or {}
    target = next((a for a in sub['addresses'] if a['id'] == addr_id), None)
    if not target:
        return jsonify({'error': 'Not found'}), 404
    for key in (
        'label', 'enabled', 'notifyBeforeOffMin', 'notifyBeforeOnMin',
        'notifyScheduleChange', 'notifyEmergency', 'queue', 'queueDisplay', 'emergencyInfo',
    ):
        if key in body:
            target[key] = body[key]
    sub['updatedAt'] = now_iso()
    save_store(state)
    return jsonify({'address': target, 'subscriber': public_sub(sub)})


@app.route('/api/subscriber/addresses/apply-timing-all', methods=['POST'])
def api_apply_timing_all():
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    sub = get_or_create_sub(state, sub_id, tg_user)
    body = request.get_json(force=True) or {}
    for addr in sub['addresses']:
        if 'notifyBeforeOffMin' in body:
            addr['notifyBeforeOffMin'] = int(body['notifyBeforeOffMin'])
        if 'notifyBeforeOnMin' in body:
            addr['notifyBeforeOnMin'] = int(body['notifyBeforeOnMin'])
        if 'notifyScheduleChange' in body:
            addr['notifyScheduleChange'] = bool(body['notifyScheduleChange'])
    sub['updatedAt'] = now_iso()
    save_store(state)
    return jsonify({'subscriber': public_sub(sub)})


@app.route('/api/subscriber/settings', methods=['PATCH'])
def api_subscriber_settings():
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    sub = get_or_create_sub(state, sub_id, tg_user)
    body = request.get_json(force=True) or {}
    for key in ('quietHoursEnabled', 'quietStart', 'quietEnd', 'language'):
        if key in body:
            sub[key] = body[key]
    sub['updatedAt'] = now_iso()
    save_store(state)
    return jsonify({'subscriber': public_sub(sub)})


@app.route('/api/channel/config', methods=['GET', 'PATCH'])
def api_channel_config():
    state = load_store()
    if request.method == 'PATCH':
        body = request.get_json(force=True) or {}
        state['channelConfig'].update(body)
        save_store(state)
    return jsonify({'channelConfig': state['channelConfig']})


@app.route('/api/channel/digest', methods=['POST'])
def api_channel_digest():
    state = load_store()
    cfg = state['channelConfig']
    lines = [
        f"⚡ <b>Сводка ДТЕК ({datetime.now(KYIV_TZ).strftime('%d.%m %H:%M')})</b>",
        f"Обновлено на сайте: {dtek.snapshot.get('update') or 'актуально'}",
        '',
    ]
    for q in cfg.get('queues', DEFAULT_GROUPS):
        st = analyze_queue_now(dtek.snapshot, q)
        icon = '🟢' if st['status'] == 'on' else '🔴' if st['status'] == 'off' else '🟡'
        q_name = dtek.snapshot['groupNames'].get(q, q)
        nxt = (
            f"→ {'выкл' if st['nextTarget'] == 'off' else 'вкл'} {st['nextLabel']}"
            if st['nextLabel']
            else 'без откл.'
        )
        lines.append(f'{icon} <b>{q_name}</b>: {nxt}')
    html = '\n'.join(lines)
    sent = tg_send(resolve_channel_id(), html, True)
    evt = {
        'id': f'evt_{uuid.uuid4().hex[:8]}',
        'type': 'channel_broadcast',
        'queue': 'ALL',
        'title': 'Сводка отправлена в канал' if sent else 'Сводка сформирована',
        'message': 'Опубликовано в Telegram-канал' if sent else 'Готово к копированию',
        'createdAt': now_iso(),
        'deliveredTelegram': False,
        'deliveredChannel': sent,
    }
    state.setdefault('alerts', []).insert(0, evt)
    state['alerts'] = state['alerts'][:200]
    save_store(state)
    return jsonify({'sent': sent, 'preview': html, 'event': evt})


@app.route('/api/alerts/test', methods=['POST'])
def api_test_alert():
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    sub = get_or_create_sub(state, sub_id, tg_user)
    addr = sub['addresses'][0] if sub['addresses'] else None
    label = addr['label'] if addr else 'Тест'
    q_disp = (addr.get('queueDisplay') or addr.get('queue', '')) if addr else 'Черга 1.1'
    sent = False
    if sub.get('telegramChatId'):
        sent = tg_send(
            sub['telegramChatId'],
            f'🔔 <b>Тестовое уведомление ДТЕК</b>\nАдрес: <b>{label}</b> ({q_disp})',
        )
    evt = {
        'id': f'evt_{uuid.uuid4().hex[:8]}',
        'type': 'bot_message',
        'subscriberId': sub.get('id'),
        'queue': addr['queue'] if addr else 'GPV1.1',
        'addressId': addr['id'] if addr else None,
        'addressLabel': label,
        'title': f'Тест уведомления · {label}',
        'message': f'Отправлено ({q_disp})',
        'createdAt': now_iso(),
        'deliveredTelegram': sent,
        'deliveredChannel': False,
    }
    state.setdefault('alerts', []).insert(0, evt)
    state['alerts'] = state['alerts'][:200]
    save_store(state)
    return jsonify({'event': evt, 'deliveredTelegram': sent})


REMINDER_LIMIT_FREE = 3  # одновременно активных напоминаний в бесплатной версии
REMINDER_LIMIT_PRO = 500  # в Pro без ограничений (потолок только для защиты сервера)
HOUR_WORDS = {
    'ru': {'yes': 'свет есть', 'no': 'отключение', 'first': 'отключение первые 30 мин', 'second': 'отключение вторые 30 мин',
           'maybe': 'возможно отключение', 'mfirst': 'возможно отключение первые 30 мин', 'msecond': 'возможно отключение вторые 30 мин'},
    'uk': {'yes': 'світло є', 'no': 'відключення', 'first': 'відключення перші 30 хв', 'second': 'відключення другі 30 хв',
           'maybe': 'можливе відключення', 'mfirst': 'можливе відключення перші 30 хв', 'msecond': 'можливе відключення другі 30 хв'},
}


@app.route('/api/subscriber/reminders', methods=['POST'])
def api_add_reminder():
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    sub = get_or_create_sub(state, sub_id, tg_user)
    body = request.get_json(silent=True) or {}
    addr = next((a for a in sub.get('addresses', []) if a.get('id') == body.get('addressId')), None)
    if not addr:
        return jsonify({'error': 'Address not found', 'code': 'NO_ADDRESS'}), 404
    try:
        day_key = int(str(body.get('dayKey')))
        hour = int(body.get('hour'))
        lead = int(body.get('leadMin', 15))
    except (TypeError, ValueError):
        return jsonify({'error': 'Bad request', 'code': 'BAD_REQUEST'}), 400
    if not (1 <= hour <= 24) or not (0 <= lead <= 180):
        return jsonify({'error': 'Bad request', 'code': 'BAD_REQUEST'}), 400
    note = str(body.get('note') or '').strip()[:120]
    start_at = day_key + (hour - 1) * 3600
    if start_at <= time.time():
        return jsonify({'error': 'This time has already passed', 'code': 'PAST'}), 400
    rems = sub.setdefault('reminders', [])
    rems[:] = [r for r in rems if not r.get('sent')]
    existing = next((r for r in rems if r.get('addressId') == addr['id'] and r.get('dayKey') == str(day_key)
                     and r.get('hour') == hour), None)
    if existing:
        existing['leadMin'] = lead
        existing['fireAt'] = start_at - lead * 60
        existing['note'] = note
    else:
        pro = is_pro_active(sub)
        limit = REMINDER_LIMIT_PRO if pro else REMINDER_LIMIT_FREE
        if len(rems) >= limit:
            return jsonify({'error': 'Reminder limit reached', 'code': 'LIMIT_REACHED',
                            'limit': None if pro else limit}), 403
        rems.append({
            'id': f'rem_{uuid.uuid4().hex[:8]}',
            'addressId': addr['id'],
            'dayKey': str(day_key),
            'hour': hour,
            'leadMin': lead,
            'startAt': start_at,
            'fireAt': start_at - lead * 60,
            'note': note,
            'createdAt': now_iso(),
            'sent': False,
        })
    save_store(state)
    return jsonify({'subscriber': public_sub(sub)})


@app.route('/api/subscriber/reminders/<rid>', methods=['DELETE'])
def api_delete_reminder(rid):
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    sub = get_or_create_sub(state, sub_id, tg_user)
    sub['reminders'] = [r for r in (sub.get('reminders') or []) if r.get('id') != rid]
    save_store(state)
    return jsonify({'subscriber': public_sub(sub)})


def process_reminders(state: dict) -> int:
    """Отправляет напоминания, поставленные пользователем на конкретный час графика."""
    now_ts = time.time()
    sent_count = 0
    names = dtek.snapshot.get('groupNames', {})
    for sub in state.get('subscribers', {}).values():
        rems = sub.get('reminders') or []
        if not rems:
            continue
        lang = 'uk' if sub.get('language') == 'uk' else 'ru'
        keep = []
        for r in rems:
            if r.get('sent'):
                continue
            fire_at = float(r.get('fireAt', 0))
            start_at = float(r.get('startAt', fire_at))
            if now_ts < fire_at:
                keep.append(r)
                continue
            if now_ts > start_at + 1800:
                continue  # пропущено: тик не успел, не шлём устаревшее
            addr = next((a for a in sub.get('addresses', []) if a.get('id') == r.get('addressId')), None)
            if not addr:
                continue
            q = addr.get('queue', '')
            q_name = names.get(q, addr.get('queueDisplay') or q)
            hour = int(r.get('hour', 1))
            hh = f'{(hour - 1) % 24:02d}:00'
            value = ((dtek.snapshot.get('days', {}).get(str(r.get('dayKey')), {}) or {}).get(q, {}) or {}).get(str(hour))
            state_txt = HOUR_WORDS[lang].get(value, '') if value else ''
            rem = int(round((start_at - now_ts) / 60))
            if rem > 0:
                when = f'через {rem} мин' if lang == 'ru' else f'за {rem} хв'
            else:
                when = 'уже началось' if lang == 'ru' else 'вже почалося'
            head = 'Напоминание' if lang == 'ru' else 'Нагадування'
            adr = 'Адрес' if lang == 'ru' else 'Адреса'
            title = f'⏰ {head} · {hh} ({when})'
            lines = f"<b>{title}</b>\n{adr}: <b>{addr['label']}</b> ({q_name})"
            note = str(r.get('note') or '').strip()
            if note:
                lines += f'\n📝 {html_escape(note)}'
            if state_txt:
                lines += f'\n{hh}: {state_txt}'
            ok = False
            if sub.get('telegramChatId'):
                ok = tg_send(sub['telegramChatId'], lines)
                if ok:
                    sent_count += 1
            append_alert(state, {
                'type': 'reminder',
                'subscriberId': sub['id'],
                'queue': q,
                'addressId': addr['id'],
                'addressLabel': addr['label'],
                'title': f"{title} · {addr['label']}",
                'message': ' · '.join(x for x in (note, f'{hh}: {state_txt}' if state_txt else q_name) if x),
                'deliveredTelegram': ok,
                'deliveredChannel': False,
            })
        sub['reminders'] = keep
    return sent_count


def append_alert(state: dict, evt: dict) -> None:
    evt.setdefault('id', f'evt_{uuid.uuid4().hex[:8]}')
    evt.setdefault('createdAt', now_iso())
    state.setdefault('alerts', []).insert(0, evt)
    state['alerts'] = state['alerts'][:200]


STATE_WORDS = {
    'ru': {'yes': 'свет есть', 'no': 'отключение', 'other': 'возможное отключение'},
    'uk': {'yes': 'світло є', 'no': 'відключення', 'other': 'можливе відключення'},
}


def _state_word(state_value: str, lang: str) -> str:
    words = STATE_WORDS.get(lang, STATE_WORDS['ru'])
    return words.get(state_value, words['other'] if state_value not in ('yes', 'no') else words[state_value])


def relevant_changes(diff: dict) -> list[dict]:
    """Только то, что ещё впереди: часы, которые уже прошли, пользователю не интересны."""
    now = datetime.now(KYIV_TZ)
    try:
        day = datetime.fromtimestamp(int(diff['dayTimestamp']), KYIV_TZ).date()
    except Exception:
        return diff.get('changes', [])
    if day < now.date():
        return []
    if day > now.date():
        return diff.get('changes', [])
    now_min = now.hour * 60 + now.minute
    return [c for c in diff.get('changes', []) if c['hour'] * 60 > now_min]


def format_changes(diff: dict, changes: list[dict], lang: str) -> str:
    """Склеивает соседние часы с одинаковым изменением: «14:00–16:00: свет есть → отключение»."""
    rows: list[list] = []
    for c in sorted(changes, key=lambda x: x['hour']):
        if rows and rows[-1][2] == c['oldState'] and rows[-1][3] == c['newState'] and rows[-1][1] == c['hour'] - 1:
            rows[-1][1] = c['hour']
        else:
            rows.append([c['hour'], c['hour'], c['oldState'], c['newState']])
    lines = []
    for first, last, old_s, new_s in rows[:8]:
        icon = '✅' if new_s == 'yes' else ('🔴' if new_s == 'no' else '🟡')
        lines.append(f"{icon} {first - 1:02d}:00–{last:02d}:00: {_state_word(old_s, lang)} → {_state_word(new_s, lang)}")
    if len(rows) > 8:
        lines.append('…')
    return '\n'.join(lines)


def diff_day_title(diff: dict, lang: str) -> str:
    try:
        dt = datetime.fromtimestamp(int(diff['dayTimestamp']), KYIV_TZ)
        delta = (dt.date() - datetime.now(KYIV_TZ).date()).days
    except Exception:
        return diff.get('dayLabel', '')
    names = {'ru': {0: 'сегодня', 1: 'завтра'}, 'uk': {0: 'сьогодні', 1: 'завтра'}}.get(lang, {})
    return f"{names.get(delta, '')} {dt.strftime('%d.%m')}".strip()


def _fmt_slot(i: int) -> str:
    m = i * 30
    return f'{m // 60:02d}:{m % 60:02d}'


def day_ranges(hours_map: dict, wanted: str) -> list[tuple[int, int]]:
    """Интервалы (в получасах) со статусом wanted ('off' или 'maybe') за сутки."""
    slots: list[str] = []
    for h in range(1, 25):
        a, b = state_to_half_hours((hours_map or {}).get(str(h), 'yes'))
        slots += [a, b]
    ranges: list[tuple[int, int]] = []
    start = None
    for i, st in enumerate(slots + ['on']):
        if st == wanted and start is None:
            start = i
        elif st != wanted and start is not None:
            ranges.append((start, i))
            start = None
    return ranges


def describe_day(hours_map: dict, lang: str = 'ru') -> str:
    uk = lang == 'uk'
    off = day_ranges(hours_map, 'off')
    maybe = day_ranges(hours_map, 'maybe')
    if not off and not maybe:
        return 'відключень за графіком немає ✅' if uk else 'отключений по графику нет ✅'
    parts = []
    if off:
        hrs = sum(b - a for a, b in off) / 2
        parts.append(', '.join(f'{_fmt_slot(a)}–{_fmt_slot(b)}' for a, b in off) + (f' ({hrs:g} год без світла)' if uk else f' ({hrs:g} ч без света)'))
    if maybe:
        parts.append(('можливо: ' if uk else 'возможно: ') + ', '.join(f'{_fmt_slot(a)}–{_fmt_slot(b)}' for a, b in maybe))
    return '; '.join(parts)


def announce_new_days(state: dict, channel_id: str | None) -> int:
    """Когда в данных ДТЕК появляется график на завтра, один раз сообщаем об этом подписчикам."""
    days = dtek.snapshot.get('days', {})
    today = datetime.now(KYIV_TZ).date()
    future = []
    for k in sorted(days.keys(), key=lambda x: int(x)):
        try:
            if datetime.fromtimestamp(int(k), KYIV_TZ).date() > today:
                future.append(k)
        except Exception:
            continue
    announced = state.get('announcedDays')
    if announced is None:
        state['announcedDays'] = list(future)  # первый запуск: не рассылаем уже существующее
        return 0
    sent = 0
    for k in future:
        if k in announced:
            continue
        announced.append(k)
        label = datetime.fromtimestamp(int(k), KYIV_TZ).strftime('%d.%m')
        names = dtek.snapshot.get('groupNames', {})
        for sub in state.get('subscribers', {}).values():
            chat = sub.get('telegramChatId')
            if not chat:
                continue
            lang = 'uk' if sub.get('language') == 'uk' else 'ru'
            by_queue: dict[str, list[str]] = {}
            for addr in sub.get('addresses', []):
                if not addr.get('enabled') or not addr.get('notifyScheduleChange'):
                    continue
                if days[k].get(addr.get('queue')) is None:
                    continue
                by_queue.setdefault(addr['queue'], []).append(str(addr.get('label', 'Адрес')))
            lines = [f"• <b>{', '.join(labels)}</b> · {names.get(q, q)}: {describe_day(days[k][q], lang)}" for q, labels in by_queue.items()]
            if lines:
                head = f'Опубліковано графік на завтра · {label}' if lang == 'uk' else f'Опубликован график на завтра · {label}'
                if tg_send(chat, f'📅 <b>{head}</b>\n' + '\n'.join(lines), True):
                    sent += 1
        cfg = state.get('channelConfig') or {}
        if channel_id and cfg.get('enabled'):
            ch_lines = [f"• {names.get(q, q)}: {describe_day(days[k][q])}" for q in cfg.get('queues', []) if q in days[k]]
            if ch_lines and tg_send(channel_id, f'📅 <b>График ДТЕК на завтра · {label}</b>\n' + '\n'.join(ch_lines)):
                sent += 1
        log_event(f'tomorrow schedule announced for {label}')
    state['announcedDays'] = announced[-10:]
    return sent


def _norm(value) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip().casefold()


def _outage_sig(info: dict | None) -> tuple:
    info = info or {}
    return (info.get('subType', ''), info.get('startDate', ''), info.get('endDate', ''))


def outage_watchlist(state: dict) -> list[dict]:
    """Какие улицы нужно проверять на сайте ДТЕК (там блокируют бесплатный PythonAnywhere,
    поэтому проверку делает GitHub Action)."""
    seen: dict[tuple, dict] = {}
    for sub in state.get('subscribers', {}).values():
        for addr in sub.get('addresses', []):
            if not addr.get('enabled') or not addr.get('notifyEmergency', True):
                continue
            street, house = _norm(addr.get('street')), _norm(addr.get('houseNum'))
            if street in ('', '—', '-') or house in ('', '—', '-'):
                continue
            seen.setdefault((_norm(addr.get('city')), street), {'city': addr['city'], 'street': addr['street']})
    return list(seen.values())[:60]


def process_outages(state: dict, results: list[dict]) -> int:
    """Сравнивает текущее состояние адреса на сайте ДТЕК с прошлым и шлёт уведомления:
    новое аварийное/внеплановое отключение, изменение времени восстановления, окончание."""
    by_street = {(_norm(r.get('city')), _norm(r.get('street'))): (r.get('houses') or {}) for r in results if isinstance(r, dict)}
    sent = 0
    for sub in state.get('subscribers', {}).values():
        lang = 'uk' if sub.get('language') == 'uk' else 'ru'
        for addr in sub.get('addresses', []):
            if not addr.get('enabled') or not addr.get('notifyEmergency', True):
                continue
            houses = by_street.get((_norm(addr.get('city')), _norm(addr.get('street'))))
            if houses is None:
                continue
            raw = houses.get(str(addr.get('houseNum')))
            if raw is None:
                continue  # дом не найден в ответе: ничего не меняем
            cur = {'subType': raw.get('subType', ''), 'startDate': raw.get('startDate', ''), 'endDate': raw.get('endDate', ''), 'type': raw.get('type', '')} if raw.get('subType') else None
            prev = addr.get('emergencyInfo') if isinstance(addr.get('emergencyInfo'), dict) and addr['emergencyInfo'].get('subType') else None
            first_seen = not addr.get('outageSeen')
            addr['outageSeen'] = True
            if _outage_sig(cur) == _outage_sig(prev):
                addr['emergencyInfo'] = cur
                continue
            addr['emergencyInfo'] = cur
            if first_seen:
                continue  # первое наблюдение: просто запоминаем, без уведомления
            label = addr.get('label', 'Адрес')
            if cur and (not prev or prev.get('subType') != cur.get('subType') or prev.get('startDate') != cur.get('startDate')):
                kind = cur['subType'].casefold()
                icon = '🚨' if 'аварій' in kind or 'аварий' in kind or 'екстрен' in kind or 'экстрен' in kind else '⚠️'
                period = f"{cur['startDate']} — {cur['endDate']}" if cur.get('startDate') else ''
                head = 'Відключення за адресою' if lang == 'uk' else 'Отключение по адресу'
                text = f"{icon} <b>{head}: {label}</b>\n{cur['subType']}" + (f"\n🕒 {period}" if period else '')
                title = f"{head}: {label}"
            elif cur and prev:
                head = 'Змінився час відновлення' if lang == 'uk' else 'Изменилось время восстановления'
                text = f"⏱ <b>{head}: {label}</b>\n{cur['subType']}\n🕒 {cur.get('startDate', '')} — {cur.get('endDate', '')}"
                title = f"{head}: {label}"
            else:
                head = 'Відключення за адресою завершено' if lang == 'uk' else 'Отключение по адресу завершено'
                text = f"✅ <b>{head}: {label}</b>" + (f"\n{prev['subType']}" if prev else '')
                title = f"{head}: {label}"
            ok = False
            if sub.get('telegramChatId'):
                ok = tg_send(sub['telegramChatId'], text, True)
                if ok:
                    sent += 1
            append_alert(state, {
                'type': 'emergency',
                'queue': addr.get('queue', ''),
                'addressId': addr.get('id'),
                'addressLabel': label,
                'title': title,
                'message': re.sub(r'<[^>]+>', '', text),
                'deliveredTelegram': ok,
                'deliveredChannel': False,
            })
            log_event(f"outage change sub={sub['id']} addr={addr.get('id')}: {_outage_sig(prev)} -> {_outage_sig(cur)}")
    return sent


def run_notification_tick(diffs: list[dict] | None = None) -> int:
    state = load_store()
    sent_count = 0
    channel_id = resolve_channel_id()

    for sub in state.get('subscribers', {}).values():
        changed, removed, reasons = enforce_expiry(sub)
        if changed and sub.get('telegramChatId'):
            titles = {'reward': 'Бонус за рекламу закончился', 'pro': 'Подписка Pro закончилась', 'ref': 'Бонус за приглашённого друга закончился'}
            text = '⏳ <b>' + '; '.join(titles[r] for r in reasons) + '.</b>'
            if removed:
                text += f"\nАдрес отключён: {', '.join(removed)}."
            text += f"\nПостоянный слот — {slot_stars_price()} ⭐, Pro — {pro_stars_price()} ⭐/мес в Mini App."
            tg_send(sub['telegramChatId'], text, True)

    if diffs:
        for diff in diffs:
            q = diff['queue']
            q_name = dtek.snapshot['groupNames'].get(q, q)
            changes = relevant_changes(diff)
            if not changes:
                continue
            summary = format_changes(diff, changes, 'ru')
            for sub in state.get('subscribers', {}).values():
                matching = [a for a in sub.get('addresses', []) if a.get('enabled') and a.get('notifyScheduleChange') and a.get('queue') == q]
                if not matching:
                    continue
                lang = 'uk' if sub.get('language') == 'uk' else 'ru'
                text_changes = format_changes(diff, changes, lang)
                head = 'Зміна графіка ДТЕК' if lang == 'uk' else 'Изменение графика ДТЕК'
                adr = 'Адреси' if lang == 'uk' else 'Адреса'
                ok = False
                if sub.get('telegramChatId'):
                    ok = tg_send(
                        sub['telegramChatId'],
                        f"🔄 <b>{head} · {q_name} · {diff_day_title(diff, lang)}</b>\n{adr}: {', '.join(m['label'] for m in matching)}\n{text_changes}",
                    )
                    if ok:
                        sent_count += 1
                append_alert(state, {
                    'type': 'schedule_changed',
                    'subscriberId': sub['id'],
                    'queue': q,
                    'addressId': matching[0]['id'],
                    'addressLabel': ', '.join(m['label'] for m in matching),
                    'title': f'{head} · {q_name}',
                    'message': text_changes,
                    'deliveredTelegram': ok,
                    'deliveredChannel': False,
                })
            if channel_id and state['channelConfig'].get('enabled') and q in state['channelConfig'].get('queues', []):
                ch_ok = tg_send(channel_id, f"📢 <b>Обновлён график ДТЕК · {q_name} · {diff_day_title(diff, 'ru')}</b>\n{summary}")
                if ch_ok:
                    sent_count += 1
                append_alert(state, {
                    'type': 'channel_broadcast',
                    'queue': q,
                    'title': f'Обновлён график в канале · {q_name}',
                    'message': summary,
                    'deliveredTelegram': False,
                    'deliveredChannel': ch_ok,
                })

    sent_count += announce_new_days(state, channel_id)
    sent_count += process_reminders(state)

    for sub in state.get('subscribers', {}).values():
        for addr in sub.get('addresses', []):
            if not addr.get('enabled'):
                continue
            info = analyze_queue_now(dtek.snapshot, addr['queue'])
            rem = info.get('minutesUntilChange')
            target = info.get('nextTarget')
            if rem is None or not target:
                continue
            lead = int(addr.get('notifyBeforeOffMin', 15) if target == 'off' else addr.get('notifyBeforeOnMin', 15))
            if lead > 0 and 0 < rem <= lead:
                dedupe = f"sub:{sub['id']}:{addr['id']}:{info['dateStr']}:{target}:{info['nextAbsMin']}"
                if dedupe in state.get('sentKeys', {}):
                    continue
                state.setdefault('sentKeys', {})[dedupe] = time.time()
                icon = '🔴' if target == 'off' else '🟢'
                title = f"{icon} {'Отключение' if target == 'off' else 'Включение'} в {info['nextLabel']} (через {rem} мин)"
                ok = False
                if sub.get('telegramChatId'):
                    ok = tg_send(sub['telegramChatId'], f"<b>{title}</b>\nАдрес: <b>{addr['label']}</b> ({addr.get('queueDisplay') or addr.get('queue', '')})")
                    if ok:
                        sent_count += 1
                append_alert(state, {
                    'type': 'power_off_soon' if target == 'off' else 'power_on_soon',
                    'subscriberId': sub['id'],
                    'queue': addr['queue'],
                    'addressId': addr['id'],
                    'addressLabel': addr['label'],
                    'title': f"{title} · {addr['label']}",
                    'message': f"{addr['label']} ({addr.get('queueDisplay') or addr.get('queue', '')})",
                    'deliveredTelegram': ok,
                    'deliveredChannel': False,
                })

    if channel_id and state['channelConfig'].get('enabled'):
        cfg = state['channelConfig']
        for q in cfg.get('queues', []):
            info = analyze_queue_now(dtek.snapshot, q)
            rem = info.get('minutesUntilChange')
            target = info.get('nextTarget')
            if rem is None or not target:
                continue
            lead = int(cfg.get('notifyBeforeOffMin', 15) if target == 'off' else cfg.get('notifyBeforeOnMin', 15))
            if lead > 0 and 0 < rem <= lead:
                dedupe = f"ch:{q}:{info['dateStr']}:{target}:{info['nextAbsMin']}"
                if dedupe in state.get('sentKeys', {}):
                    continue
                state.setdefault('sentKeys', {})[dedupe] = time.time()
                q_name = dtek.snapshot['groupNames'].get(q, q)
                icon = '🔴' if target == 'off' else '🟢'
                if tg_send(channel_id, f"{icon} <b>{q_name}: {'отключение' if target == 'off' else 'включение'} в {info['nextLabel']}</b> (через {rem} мин)"):
                    sent_count += 1

    # чистим старые ключи дедупликации, чтобы store.json не разрастался
    sent_keys = state.get('sentKeys', {})
    cutoff = time.time() - 3 * 86400
    for key in [k for k, ts in sent_keys.items() if isinstance(ts, (int, float)) and ts < cutoff]:
        del sent_keys[key]

    save_store(state)
    return sent_count


@app.route('/api/cron/tick', methods=['GET', 'POST'])
def api_cron_tick():
    expected = os.environ.get('CRON_SECRET', '').strip()
    provided = (request.args.get('secret') or request.headers.get('x-cron-secret', '')).strip()
    if expected and provided != expected:
        return jsonify({'error': 'Unauthorized'}), 401
    _, diffs = dtek.refresh()
    sent = run_notification_tick(diffs=diffs)
    global _last_logged_dtek_error
    if dtek.last_error != _last_logged_dtek_error:
        _last_logged_dtek_error = dtek.last_error
        log_event(f'DTEK refresh error: {dtek.last_error}' if dtek.last_error else 'DTEK refresh ok again')
    if sent or diffs:
        log_event(f'tick: notificationsSent={sent} diffs={len(diffs)}')
    return jsonify({'ok': True, 'notificationsSent': sent, 'diffs': len(diffs)})


_last_logged_dtek_error = None


@app.route('/api/dtek/push-snapshot', methods=['POST'])
def api_dtek_push_snapshot():
    expected = os.environ.get('CRON_SECRET', '').strip()
    provided = (request.args.get('secret') or request.headers.get('x-cron-secret', '')).strip()
    if expected and provided != expected:
        return jsonify({'error': 'Unauthorized'}), 401
    body = request.get_json(silent=True) or {}
    next_snap = body.get('snapshot') or (body if 'days' in body else None)
    if not isinstance(next_snap, dict) or not next_snap.get('days'):
        return jsonify({'error': 'snapshot.days required'}), 400
    if isinstance(body.get('streets'), dict) and body['streets']:
        dtek.streets = body['streets']
    prev_days = dtek.snapshot.get('days', {})
    diffs = compute_diffs(prev_days, next_snap.get('days', {}))
    today_sec = int(next_snap.get('today') or time.time())
    next_snap['historyDays'] = merge_history_days(
        dtek.snapshot.get('historyDays') or prev_days,
        next_snap.get('historyDays') or next_snap.get('days', {}),
        today_sec,
    )
    next_snap['source'] = 'live'
    next_snap['fetchedAt'] = now_iso()
    dtek.snapshot = next_snap
    dtek.last_error = None
    dtek._save_cache()
    sent = run_notification_tick(diffs=diffs)
    return jsonify({'ok': True, 'notificationsSent': sent, 'diffs': len(diffs)})


@app.route('/api/dtek/watchlist')
def api_dtek_watchlist():
    expected = os.environ.get('CRON_SECRET', '').strip()
    provided = (request.args.get('secret') or request.headers.get('x-cron-secret', '')).strip()
    if expected and provided != expected:
        return jsonify({'error': 'Unauthorized'}), 401
    state = load_store()
    return jsonify({'streets': outage_watchlist(state), 'update': dtek.snapshot.get('update', '')})


@app.route('/api/dtek/push-outages', methods=['POST'])
def api_dtek_push_outages():
    expected = os.environ.get('CRON_SECRET', '').strip()
    provided = (request.args.get('secret') or request.headers.get('x-cron-secret', '')).strip()
    if expected and provided != expected:
        return jsonify({'error': 'Unauthorized'}), 401
    body = request.get_json(silent=True) or {}
    results = body.get('results')
    if not isinstance(results, list):
        return jsonify({'error': 'results list required'}), 400
    state = load_store()
    sent = process_outages(state, results)
    state['outagesCheckedAt'] = now_iso()
    save_store(state)
    return jsonify({'ok': True, 'notificationsSent': sent, 'streets': len(results)})


@app.route('/api/billing/create-slot-invoice', methods=['POST'])
def api_create_slot_invoice():
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    token = current_bot()['token']  # счёт создаёт тот бот, через которого открыто приложение
    if not token:
        return jsonify({'error': 'TELEGRAM_BOT_TOKEN not configured'}), 400
    get_or_create_sub(state, sub_id, tg_user)
    nonce = uuid.uuid4().hex[:8]
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{token}/createInvoiceLink',
            json={
                'title': '+1 адрес в ДТЕК Монітор',
                'description': 'Постоянный дополнительный слот для 3-го и следующих адресов + отключение рекламы',
                'payload': f'slot:{sub_id}:{nonce}',
                'provider_token': '',
                'currency': 'XTR',
                'prices': [{'label': '+1 слот адреса', 'amount': slot_stars_price()}],
            },
            timeout=10,
        )
        data = r.json()
        if not data.get('ok') or not data.get('result'):
            log_event(f"create-slot-invoice FAILED sub={sub_id}: {data.get('description')}")
            return jsonify({'error': data.get('description') or 'Failed to create Stars invoice'}), 400
        log_event(f'create-slot-invoice ok sub={sub_id}')
        return jsonify({'invoiceUrl': data['result'], 'stars': slot_stars_price()})
    except Exception as exc:
        log_event(f'create-slot-invoice ERROR sub={sub_id}: {exc!r}')
        return jsonify({'error': 'Не удалось связаться с Telegram, попробуйте ещё раз'}), 500


def _create_stars_link(sub_id: str, kind: str, title: str, description: str, label: str, amount: int, subscription: bool = False):
    """Создаёт ссылку на счёт в Telegram Stars. Возвращает (ответ_flask | None, url | None)."""
    token = current_bot()['token']
    if not token:
        return (jsonify({'error': 'TELEGRAM_BOT_TOKEN not configured'}), 400), None
    body = {
        'title': title[:32],
        'description': description[:255],
        'payload': f'{kind}:{sub_id}:{uuid.uuid4().hex[:8]}',
        'provider_token': '',
        'currency': 'XTR',
        'prices': [{'label': label[:32], 'amount': int(amount)}],
    }
    if subscription:
        body['subscription_period'] = PRO_PERIOD_SECONDS
    try:
        r = requests.post(f'https://api.telegram.org/bot{token}/createInvoiceLink', json=body, timeout=10)
        data = r.json()
        if not data.get('ok') or not data.get('result'):
            log_event(f"create-{kind}-invoice FAILED sub={sub_id}: {data.get('description')}")
            return (jsonify({'error': data.get('description') or 'Failed to create Stars invoice'}), 400), None
        log_event(f'create-{kind}-invoice ok sub={sub_id} amount={amount}')
        return None, data['result']
    except Exception as exc:
        log_event(f'create-{kind}-invoice ERROR sub={sub_id}: {exc!r}')
        return (jsonify({'error': 'Не удалось связаться с Telegram, попробуйте ещё раз'}), 500), None


@app.route('/api/billing/create-pro-invoice', methods=['POST'])
def api_create_pro_invoice():
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    get_or_create_sub(state, sub_id, tg_user)
    err, url = _create_stars_link(
        sub_id, 'pro', 'Pro: 5 адресов без рекламы',
        'Подписка на 30 дней: до 5 адресов и без рекламы. Продлевается автоматически, отменить можно в настройках Telegram.',
        'Pro на 30 дней', pro_stars_price(), subscription=True,
    )
    if err:
        return err
    return jsonify({'invoiceUrl': url, 'stars': pro_stars_price()})


@app.route('/api/billing/create-donate-invoice', methods=['POST'])
def api_create_donate_invoice():
    body = request.get_json(silent=True) or {}
    try:
        stars = int(body.get('stars') or 0)
    except (TypeError, ValueError):
        stars = 0
    if not 5 <= stars <= 2500:
        return jsonify({'error': 'Сумма поддержки: от 5 до 2500 ⭐'}), 400
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    get_or_create_sub(state, sub_id, tg_user)
    err, url = _create_stars_link(
        sub_id, 'donate', 'Поддержать ДТЕК Монітор',
        'Добровольная поддержка развития сервиса. Спасибо!',
        'Поддержка', stars,
    )
    if err:
        return err
    return jsonify({'invoiceUrl': url, 'stars': stars})


@app.route('/api/extras/status')
def api_extras_status():
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    sub = get_or_create_sub(state, sub_id, tg_user)
    if not sub.get('refCode'):
        sub['refCode'] = uuid.uuid4().hex[:8]
        save_store(state)
    bot = current_bot()['username']
    pro_until = sub.get('proUntil')
    return jsonify({
        'pro': {
            'active': is_pro_active(sub),
            'until': pro_until,
            'price': pro_stars_price(),
            'addresses': FREE_ADDRESSES + PRO_ADDRESSES_BONUS,
            'periodDays': PRO_PERIOD_SECONDS // 86400,
        },
        'donate': {'amounts': [10, 50, 100], 'total': int(sub.get('donatedStars') or 0)},
        'referral': {
            'link': f"https://t.me/{bot}?startapp=ref_{sub['refCode']}" if bot else '',
            'count': len(sub.get('referrals') or []),
            'bonusUntil': sub.get('refBonusUntil') if is_ref_bonus_active(sub) else None,
            'bonusDays': REF_BONUS_HOURS // 24,
            'maxDays': REF_BONUS_MAX_DAYS,
        },
        'isTelegram': bool(tg_user),
        'language': sub.get('language') or 'ru',
        'maxAddresses': get_max_addresses(sub),
    })


SHARE_IMAGE = DATA_DIR / 'share_card.jpg'  # скачивается с аватарки канала
SHARE_IMAGE_CUSTOM = DATA_DIR / 'share_card_custom.jpg'  # свою картинку (JPEG) можно положить сюда
SHARE_IMAGE_TTL = 24 * 3600
_share_fetch_failed_at = 0.0
SHARE_TEXT = {
    'uk': ('⚡ <b>offonsvitlo</b>: графік відключень світла ДТЕК\n'
           'Дивіться, коли є світло за вашою адресою, і отримуйте сповіщення в Telegram.', 'Відкрити'),
    'ru': ('⚡ <b>offonsvitlo</b>: график отключений света ДТЭК\n'
           'Смотрите, когда есть свет по вашему адресу, и получайте уведомления в Telegram.', 'Открыть'),
}


def _bot_api(cfg: dict, method: str, payload: dict | None = None) -> dict:
    try:
        r = requests.post(f"https://api.telegram.org/bot{cfg['token']}/{method}", json=payload or {}, timeout=12)
        return r.json()
    except Exception as exc:
        return {'ok': False, 'description': str(exc)}


def _fetch_channel_avatar() -> bool:
    """Скачивает аватарку канала (через Bot API) в dtek_data/share_card.jpg."""
    channel = resolve_channel_id()
    for cfg in bot_configs():
        chat = _bot_api(cfg, 'getChat', {'chat_id': channel})
        photo = (chat.get('result') or {}).get('photo') if chat.get('ok') else None
        file_id = (photo or {}).get('big_file_id')
        if not file_id:
            continue
        info = _bot_api(cfg, 'getFile', {'file_id': file_id})
        path = (info.get('result') or {}).get('file_path') if info.get('ok') else None
        if not path:
            continue
        try:
            r = requests.get(f"https://api.telegram.org/file/bot{cfg['token']}/{path}", timeout=15)
            if r.ok and r.content[:2] == b'\xff\xd8':  # JPEG
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                SHARE_IMAGE.write_bytes(r.content)
                return True
        except Exception:
            pass
    return False


def share_image_file() -> Path | None:
    global _share_fetch_failed_at
    if SHARE_IMAGE_CUSTOM.exists():
        return SHARE_IMAGE_CUSTOM
    fresh = SHARE_IMAGE.exists() and time.time() - SHARE_IMAGE.stat().st_mtime < SHARE_IMAGE_TTL
    if not fresh and time.time() - _share_fetch_failed_at > 600:
        if not _fetch_channel_avatar():
            _share_fetch_failed_at = time.time()
    return SHARE_IMAGE if SHARE_IMAGE.exists() else None


def public_base_url() -> str:
    app_url = os.environ.get('APP_URL', '').strip()
    if app_url.startswith('https://'):
        parsed = urllib.parse.urlparse(app_url)
        return f'{parsed.scheme}://{parsed.netloc}'
    return request.url_root.rstrip('/').replace('http://', 'https://')


@app.route('/share-card.jpg')
def share_card():
    f = SHARE_IMAGE_CUSTOM if SHARE_IMAGE_CUSTOM.exists() else SHARE_IMAGE
    if not f.exists():
        return ('', 404)
    resp = send_from_directory(str(f.parent), f.name, mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


@app.route('/api/share/prepare', methods=['POST'])
def api_share_prepare():
    """Готовит сообщение «картинка + текст + кнопка» для Telegram.WebApp.shareMessage (Bot API 8.0)."""
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    if not tg_user or not tg_user.get('id'):
        return jsonify({'id': None, 'reason': 'NOT_TELEGRAM'})
    sub = get_or_create_sub(state, sub_id, tg_user)
    if not sub.get('refCode'):
        sub['refCode'] = uuid.uuid4().hex[:8]
        save_store(state)
    body = request.get_json(silent=True) or {}
    lang = 'uk' if body.get('lang') == 'uk' else 'ru'
    cfg = next((c for c in bot_configs() if c['key'] == getattr(g, 'bot_key', None)), None) or current_bot()
    bot = cfg.get('username')
    if not bot or not cfg.get('token'):
        return jsonify({'id': None, 'reason': 'NO_BOT'})
    link = f"https://t.me/{bot}?startapp=ref_{sub['refCode']}"
    head, btn = SHARE_TEXT[lang]
    custom = str(body.get('text') or '').strip()
    caption = f'{html_escape(custom[:900])}\n\n{link}' if custom else f'{head}\n\n{link}'
    keyboard = {'inline_keyboard': [[{'text': btn, 'url': link}]]}
    article = {
        'type': 'article', 'id': 'ref', 'title': 'offonsvitlo',
        'input_message_content': {'message_text': caption, 'parse_mode': 'HTML'},
        'reply_markup': keyboard,
    }
    results = []
    img = share_image_file()
    if img:
        url = f"{public_base_url()}/share-card.jpg?v={int(img.stat().st_mtime)}"
        results.append({
            'type': 'photo', 'id': 'ref', 'photo_url': url, 'thumbnail_url': url,
            'caption': caption, 'parse_mode': 'HTML', 'reply_markup': keyboard,
        })
    results.append(article)  # запасной вариант без картинки
    last_error = ''
    for result in results:
        j = _bot_api(cfg, 'savePreparedInlineMessage', {
            'user_id': int(tg_user['id']), 'result': result,
            'allow_user_chats': True, 'allow_bot_chats': False,
            'allow_group_chats': True, 'allow_channel_chats': False,
        })
        if j.get('ok'):
            res = j.get('result') or {}
            return jsonify({'id': res.get('id'), 'expiresAt': res.get('expiration_date'),
                            'withImage': result['type'] == 'photo'})
        last_error = str(j.get('description'))
        log_event(f"savePreparedInlineMessage ({result['type']}) failed: {last_error}")
    return jsonify({'id': None, 'reason': 'TELEGRAM_ERROR', 'detail': last_error})


@app.route('/api/share/send-card', methods=['POST'])
def api_share_send_card():
    """Присылает пользователю в чат с ботом карточку «картинка + текст + кнопка», которую можно переслать друзьям."""
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    if not tg_user or not tg_user.get('id'):
        return jsonify({'ok': False, 'reason': 'NOT_TELEGRAM'}), 400
    sub = get_or_create_sub(state, sub_id, tg_user)
    if not sub.get('refCode'):
        sub['refCode'] = uuid.uuid4().hex[:8]
        save_store(state)
    body = request.get_json(silent=True) or {}
    lang = 'uk' if body.get('lang') == 'uk' else 'ru'
    cfg = next((c for c in bot_configs() if c['key'] == getattr(g, 'bot_key', None)), None) or current_bot()
    bot = cfg.get('username')
    if not bot or not cfg.get('token'):
        return jsonify({'ok': False, 'reason': 'NO_BOT'}), 500
    link = f"https://t.me/{bot}?startapp=ref_{sub['refCode']}"
    head, btn = SHARE_TEXT[lang]
    custom = str(body.get('text') or '').strip()
    if custom:  # график очереди: текст приходит с клиента, экранируем и ограничиваем длину подписи
        caption = f'{html_escape(custom[:900])}\n\n{link}'
    else:
        caption = f'{head}\n\n{link}'
    keyboard = json.dumps({'inline_keyboard': [[{'text': btn, 'url': link}]]})
    chat_id = int(tg_user['id'])
    img = share_image_file()
    if img:
        try:
            with open(img, 'rb') as fh:
                r = requests.post(
                    f"https://api.telegram.org/bot{cfg['token']}/sendPhoto",
                    data={'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML', 'reply_markup': keyboard},
                    files={'photo': ('offonsvitlo.jpg', fh, 'image/jpeg')}, timeout=20,
                )
            if r.ok and r.json().get('ok'):
                return jsonify({'ok': True, 'withImage': True})
            log_event(f'sendPhoto failed: {r.text[:200]}')
        except Exception as exc:
            log_event(f'sendPhoto error: {exc}')
    j = _bot_api(cfg, 'sendMessage', {
        'chat_id': chat_id, 'text': caption, 'parse_mode': 'HTML',
        'reply_markup': json.loads(keyboard), 'disable_web_page_preview': False,
    })
    if j.get('ok'):
        return jsonify({'ok': True, 'withImage': False})
    log_event(f"sendMessage (share card) failed: {j.get('description')}")
    return jsonify({'ok': False, 'reason': 'TELEGRAM_ERROR', 'detail': str(j.get('description'))}), 502


@app.route('/api/billing/reward-ad', methods=['POST'])
def api_reward_ad():
    """Засчитывает один просмотр рекламы. За REWARD_ADS_GOAL просмотров — REWARD_HOURS часов 3-го адреса."""
    state = load_store()
    sub_id, tg_user = resolve_sub_id()
    sub = get_or_create_sub(state, sub_id, tg_user)
    if is_pro_active(sub) or sub.get('adsDisabled'):
        return jsonify({'error': 'Ads are disabled', 'code': 'ADS_DISABLED'}), 403
    now = time.time()
    if now - float(sub.get('rewardAdLastAt') or 0) < REWARD_AD_MIN_INTERVAL:
        return jsonify({'error': 'Too fast', 'code': 'TOO_FAST'}), 429
    sub['rewardAdLastAt'] = now
    today = kyiv_day()
    if sub.get('rewardAdDay') != today:  # новый день: счётчик начинается заново
        sub['rewardAdDay'] = today
        sub['rewardAdCount'] = 0
    count = int(sub.get('rewardAdCount') or 0) + 1
    granted = False
    if count >= REWARD_ADS_GOAL:
        count = 0
        base = max(now, _ts(sub.get('rewardSlotExpiresAt')))
        until = min(base + REWARD_HOURS * 3600, now + REWARD_MAX_STACK_HOURS * 3600)
        sub['rewardSlotExpiresAt'] = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
        granted = True
        log_event(f'reward slot granted sub={sub_id} for {REWARD_HOURS}h (ads goal reached)')
    sub['rewardAdCount'] = count
    sub['updatedAt'] = now_iso()
    save_store(state)
    return jsonify({'subscriber': public_sub(sub), 'granted': granted, 'maxAddresses': get_max_addresses(sub)})


@app.route('/api/billing/unlock-reward-slot', methods=['POST'])
def api_unlock_reward_slot():
    # старый способ (1 реклама = 3 дня) отключён: используйте /api/billing/reward-ad
    return jsonify({'error': 'Use /api/billing/reward-ad', 'code': 'GONE'}), 410


def webhook_authorized() -> bool:
    """Если задан WEBHOOK_SECRET, Telegram должен присылать его в заголовке
    (setWebhook ... &secret_token=ВАШ_СЕКРЕТ). Иначе подделанный запрос мог бы «оплатить» слот."""
    secret = os.environ.get('WEBHOOK_SECRET', '').strip()
    if not secret:
        return True
    return hmac.compare_digest(request.headers.get('X-Telegram-Bot-Api-Secret-Token', ''), secret)


def _handle_telegram_update(bot_key: str):
    g.bot_key = bot_key
    if not webhook_authorized():
        log_event('webhook rejected: wrong or missing secret token')
        return jsonify({'error': 'Forbidden'}), 403
    upd = request.get_json(silent=True) or {}
    pre = upd.get('pre_checkout_query')
    if pre:
        token = bot_by_key(bot_key)['token']
        if token:
            try:
                rr = requests.post(
                    f'https://api.telegram.org/bot{token}/answerPreCheckoutQuery',
                    json={'pre_checkout_query_id': pre['id'], 'ok': True},
                    timeout=8,
                )
                log_event(f"pre_checkout answered ok={rr.ok} payload={pre.get('invoice_payload')}")
            except Exception as exc:
                log_event(f'pre_checkout ERROR: {exc!r}')
        return jsonify({'ok': True})

    msg = upd.get('message')
    if not msg:
        return jsonify({'ok': True})
    chat_id = str(msg['chat']['id'])
    _CHAT_PREF[chat_id] = bot_key
    text = (msg.get('text') or '').strip()
    user = msg.get('from') or {}
    state = load_store()

    sp = msg.get('successful_payment')
    if sp:
        payload = sp.get('invoice_payload') or ''
        charge_id = sp.get('telegram_payment_charge_id') or ''
        amount = int(sp.get('total_amount') or 0)
        # payload = <kind>:<sub_id>:<nonce>, а sub_id сам содержит двоеточие (tg:123456),
        # поэтому sub_id — всё между первым и последним ':'
        parts = payload.split(':')
        kind = parts[0] if parts else ''
        if kind in ('slot', 'pro', 'donate') and len(parts) >= 3:
            target_sub_id = ':'.join(parts[1:-1])
        else:
            kind = 'slot'
            target_sub_id = f"tg:{user.get('id', chat_id)}"
        target_sub = get_or_create_sub(state, target_sub_id, user)
        charges = target_sub.setdefault('paidCharges', [])
        if charge_id and charge_id in charges:
            log_event(f'PAYMENT duplicate ignored kind={kind} sub={target_sub_id} charge={charge_id}')
            return jsonify({'ok': True})
        if charge_id:
            charges.append(charge_id)
            target_sub['paidCharges'] = charges[-50:]
        target_sub['updatedAt'] = now_iso()

        if kind == 'pro':
            exp = sp.get('subscription_expiration_date')
            now = time.time()
            try:
                until = float(exp) if exp else max(now, _ts(target_sub.get('proUntil'))) + PRO_PERIOD_SECONDS
            except (TypeError, ValueError):
                until = max(now, _ts(target_sub.get('proUntil'))) + PRO_PERIOD_SECONDS
            if until > _ts(target_sub.get('proUntil')):
                target_sub['proUntil'] = _iso(until)
            target_sub['proLapsed'] = False
            renewal = bool(target_sub.get('proSince')) and not sp.get('is_first_recurring')
            target_sub.setdefault('proSince', now_iso())
            max_slots = get_max_addresses(target_sub)
            when = datetime.fromtimestamp(_ts(target_sub['proUntil']), KYIV_TZ).strftime('%d.%m.%Y')
            title = f'Pro продлён на {amount} ⭐' if renewal else f'Pro активирован за {amount} ⭐'
            body_tg = (f'⭐ <b>{"Pro продлён" if renewal else "Pro активирован"}</b> до <b>{when}</b>.\n'
                       f'Доступно адресов: <b>{max_slots}</b>, реклама отключена.\n'
                       'Продление автоматическое. Отменить: Telegram → Настройки → Мои звёзды → Подписки.')
            msg_text = f'До {when}. Адресов: {max_slots}, без рекламы.'
        elif kind == 'donate':
            target_sub['donatedStars'] = int(target_sub.get('donatedStars') or 0) + amount
            title = f'Спасибо за поддержку: {amount} ⭐'
            body_tg = f'💛 <b>Спасибо за поддержку!</b> Получено {amount} ⭐. Это помогает развивать сервис.'
            msg_text = f'Всего поддержки: {target_sub["donatedStars"]} ⭐'
        else:
            target_sub['extraAddressSlots'] = int(target_sub.get('extraAddressSlots') or 0) + 1
            target_sub['adsDisabled'] = True
            max_slots = get_max_addresses(target_sub)
            title = f"Оплата {amount or slot_stars_price()} ⭐ подтверждена"
            msg_text = f'Доступно адресов: {max_slots}. Реклама отключена.'
            body_tg = f'⭐ <b>Оплата прошла успешно!</b>\nОткрыт дополнительный слот адреса (теперь доступно: <b>{max_slots}</b>) и отключена реклама в Mini App.'
        append_alert(state, {
            'type': 'bot_message',
            'subscriberId': target_sub_id,
            'queue': 'ALL',
            'title': title,
            'message': msg_text,
            'deliveredTelegram': True,
            'deliveredChannel': False,
        })
        save_store(state)
        log_event(f'PAYMENT credited kind={kind} sub={target_sub_id} charge={charge_id} amount={amount}')
        tg_send(chat_id, body_tg, True)
        return jsonify({'ok': True})

    f_chat = msg.get('forward_from_chat') or (msg.get('forward_origin') or {}).get('chat')
    if f_chat and f_chat.get('type') == 'channel':
        tg_send(
            chat_id,
            f"📢 <b>ID канала:</b> <code>{f_chat['id']}</code>\nНазвание: <b>{f_chat.get('title', 'Канал')}</b>\nДобавьте бота в администраторы этого канала и впишите этот ID в <code>TELEGRAM_CHANNEL_ID</code>.",
            False,
        )
        return jsonify({'ok': True})

    if text.startswith('/link'):
        code = text.replace('/link', '').strip().upper()
        target = next((s for s in state.get('subscribers', {}).values() if s.get('linkCode') == code), None)
        if target:
            target['telegramChatId'] = chat_id
            target['telegramUsername'] = user.get('username')
            save_store(state)
            tg_send(chat_id, f"✅ Профиль привязан! Адресов: <b>{len(target.get('addresses', []))}</b>")
        else:
            tg_send(chat_id, 'Код не найден. Проверьте код в Mini App.')
        return jsonify({'ok': True})

    sub_id = f"tg:{user.get('id', chat_id)}"
    sub = get_or_create_sub(state, sub_id, user)
    if text.startswith('/start'):
        tg_send(
            chat_id,
            '<b>ДТЕК Монітор · Дніпро та область</b>\n\nНажмите кнопку ниже, чтобы добавить один или несколько адресов и настроить за сколько минут предупреждать о свете.',
            True,
        )
    elif text.startswith('/status'):
        if not sub['addresses']:
            tg_send(chat_id, 'У вас пока нет сохранённых адресов. Откройте Mini App.', True)
        else:
            lines = ['<b>Текущий статус по вашим адресам:</b>', '']
            for a in sub['addresses']:
                info = analyze_queue_now(dtek.snapshot, a['queue'])
                icon = '🟢' if info['status'] == 'on' else '🔴' if info['status'] == 'off' else '🟡'
                nxt = f"{'Выкл' if info['nextTarget'] == 'off' else 'Вкл'} в {info['nextLabel']}" if info['nextLabel'] else 'без изменений'
                lines.append(f"{icon} <b>{a['label']}</b> ({a.get('queueDisplay') or a.get('queue', '')}) — {nxt}")
            tg_send(chat_id, '\n'.join(lines), True)
    return jsonify({'ok': True})


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    from werkzeug.exceptions import HTTPException
    if isinstance(exc, HTTPException):
        return exc
    tb = traceback.extract_tb(exc.__traceback__)
    where = f'{tb[-1].filename.split("/")[-1]}:{tb[-1].lineno}' if tb else '?'
    log_event(f'UNHANDLED {request.method} {request.path}: {exc!r} at {where}')
    if request.path.startswith('/api/') or request.path.startswith('/telegram/'):
        return jsonify({'error': 'Internal server error'}), 500
    return 'Internal server error', 500


@app.route('/telegram/webhook', methods=['POST'])
def telegram_webhook():
    return _handle_telegram_update('main')


@app.route('/telegram/webhook2', methods=['POST'])
def telegram_webhook2():
    """Резервный бот: тот же обработчик, но ответы и счета идут через его токен."""
    return _handle_telegram_update('reserve')


@app.route('/api/debug/log')
def api_debug_log():
    expected = os.environ.get('CRON_SECRET', '').strip()
    provided = (request.args.get('secret') or request.headers.get('x-cron-secret', '')).strip()
    if not expected or provided != expected:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        n = max(1, min(200, int(request.args.get('n', 80))))
        lines = LOG_PATH.read_text(encoding='utf-8').splitlines()[-n:] if LOG_PATH.exists() else []
    except Exception:
        lines = []
    body = '\n'.join(lines) if lines else '(лог пуст: ошибок и событий пока не было)'
    return body, 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path: str):
    if path and (DIST_DIR / path).exists():
        return send_from_directory(str(DIST_DIR), path)
    index_file = DIST_DIR / 'index.html'
    if index_file.exists():
        return send_from_directory(str(DIST_DIR), 'index.html')
    return 'Run npm run build and upload dist/ to /home/Onoff/dtek-bot/dist', 404
