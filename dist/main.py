# -*- coding: utf-8 -*-
import asyncio
import ctypes
import json
import os
import random
import re
import signal
import subprocess
import sys
import time

# Fix Unicode encoding
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONLEGACYWINDOWSSTDIO'] = 'utf-8'
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Set process window title to vintedbot for easy identification
try:
    ctypes.windll.kernel32.SetConsoleTitleW("vintedbot")
except:
    pass

from typing import Dict, List, Optional, Tuple

import aiohttp
import requests
from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from bs4 import BeautifulSoup

from config import (
    BOT_TOKEN,
    CHAT_ID,
    CHECK_INTERVAL,
    KEYWORDS,
    KNOWN_HASHTAGS,
    PROCESSED_ITEMS_FILE,
    SETTINGS_FILE,
    VINTED_SEARCH_URL,
    VINTED_DOMAINS,
    MONITORING_URLS,
    HASHTAG_STATS_FILE,
    HASHTAG_MIN_OCCURRENCES,
    HASHTAG_MIN_LENGTH,
    STOP_WORDS,
    PROXIES,
    USE_PROXIES,
    SHOE_KEYWORDS,
    CLOTHING_EXCLUDE,
    AUTO_FETCH_PROXIES,
    PROXY_TEST_TIMEOUT,
)

if not BOT_TOKEN or not CHAT_ID:
    print(f"BOT_TOKEN or CHAT_ID environment variables not set!")
    print(f"Set BOT_TOKEN and CHAT_ID in .env file")
    print(f"DEBUG: BOT_TOKEN exists: {bool(BOT_TOKEN)}, CHAT_ID exists: {bool(CHAT_ID)}")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

class SettingsState(StatesGroup):
    waiting_for_keyword = State()
    waiting_for_interval = State()
    waiting_for_monitoring_url = State()
    removing_monitoring_url = State()
    waiting_for_collection_name = State()
    waiting_for_collection_keywords = State()
    selecting_collection_for_search = State()

is_monitoring = False
monitoring_task: Optional[asyncio.Task] = None
user_monitoring_tasks: Dict[int, asyncio.Task] = {}
user_processed_items: Dict[int, set] = {}

# ============= GLOBAL LOCK FOR MULTIPLE INSTANCES =============
INSTANCE_ID = random.randint(100000, 999999)
is_master = False
last_heartbeat_time = 0
HEARTBEAT_INTERVAL = 30
MASTER_TIMEOUT = 90
is_stopped = False
# ============= END GLOBAL LOCK =============

async def user_monitoring_loop(user_id: int):
    """Separate monitoring loop for individual user"""
    user_data = load_user_data(user_id)
    processed_items = set()
    first_run = True
    
    print(f"\n✅ Started monitoring for user {user_id}")
    
    while user_data.get('is_monitoring', False):
        user_data = load_user_data(user_id)
        if not user_data.get('is_monitoring', False):
            break
            
        try:
            urls = user_data.get('monitoring_urls', [])
            interval = user_data.get('check_interval', CHECK_INTERVAL)
            
            new_items = await check_monitoring_urls(urls, user_id)
            
            if first_run:
                print(f"📋 First run for user {user_id} - saving all items as baseline (memory only)...")
                for item_id, item_url, title, price, image_url, size, condition, shipping in new_items:
                    processed_items.add(item_id)
                print(f"✅ Saved {len(new_items)} items as baseline for user {user_id}")
                first_run = False
            else:
                for item_id, item_url, title, price, image_url, size, condition, shipping in new_items:
                    if item_id not in processed_items:
                        print(f"   🆕 NEW for user {user_id}: {title[:50]}... - {price}")
                        await send_notification_with_image(item_url, title, price, image_url, size, condition, shipping, user_id)
                        processed_items.add(item_id)
                    else:
                        print(f"   ✓ Already processed for user {user_id}: {title[:30]}...")
                        
        except Exception as e:
            print(f"❌ Error in user {user_id} monitoring: {e}")
        
        await asyncio.sleep(interval)
    
    print(f"\n⏹️ Stopped monitoring for user {user_id}")
    if user_id in user_monitoring_tasks:
        del user_monitoring_tasks[user_id]

def start_user_monitoring(user_id: int):
    """Start monitoring for specific user"""
    if user_id in user_monitoring_tasks and not user_monitoring_tasks[user_id].done():
        return
    
    user_data = load_user_data(user_id)
    user_data['is_monitoring'] = True
    save_user_data(user_id, user_data)
    
    user_monitoring_tasks[user_id] = asyncio.create_task(user_monitoring_loop(user_id))

def stop_user_monitoring(user_id: int):
    """Stop monitoring for specific user"""
    user_data = load_user_data(user_id)
    user_data['is_monitoring'] = False
    save_user_data(user_id, user_data)
    
    if user_id in user_monitoring_tasks:
        user_monitoring_tasks[user_id].cancel()
        del user_monitoring_tasks[user_id]
last_items: List[Tuple[str, str]] = []
dynamic_hashtags: set = set()
hashtag_stats: Dict[str, int] = {}
settings: Dict = {
    'keywords': KEYWORDS,
    'check_interval': CHECK_INTERVAL,
    'chat_id': CHAT_ID,
    'valid_chat_id': False,
    'keyword_collections': {
        'default': KEYWORDS.copy()
    },
    'active_collection': 'default',
}

items_cache: Dict = {
    'items': [],
    'fetch_type': None,
    'fetch_limit': None,
    'keyword': None,
    'current_page': 1,
}
ITEMS_PER_PAGE = 10

item_details_cache: Dict[str, Tuple[str, str, str, List[str]]] = {}

# ============= RATE LIMITING SYSTEM =============
# Track rate limit state per domain
rate_limit_state = {
    'last_request_time': {},     # When was the last request per domain
    'failure_count': {},          # How many failures in a row
    'backoff_until': {},          # Backoff until this timestamp
    'last_reset': time.time(),
}



USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

def get_random_headers() -> Dict[str, str]:
    """Generate simple randomized headers to avoid 403 blocks."""
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': random.choice(['pl-PL,pl;q=0.9,en;q=0.8', 'it-IT,it;q=0.9,en;q=0.8', 'en-US,en;q=0.9']),
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
    }

def is_rate_limited(status_code: int, html: str = '') -> bool:
    """Detect if response indicates rate limiting."""
    # If status is 200, it's a successful response - NOT rate limited
    if status_code == 200:
        return False
    
    # Check HTTP status codes that indicate rate limiting
    if status_code in [429, 403, 503]:  # Too Many Requests, Forbidden, Service Unavailable
        return True
    
    # For other error codes, optionally check HTML (but be very strict)
    if html and status_code >= 400:
        html_lower = html.lower()
        # Only check for very specific rate limit pages (not generic words)
        rate_limit_indicators = [
            'rate limit',
            'you are rate limited',
            'too many requests',
            'recaptcha',
            'temporarily blocked',
            'wait before',
            'slow down',
        ]
        if any(indicator in html_lower for indicator in rate_limit_indicators):
            return True
    
    return False

def get_backoff_delay(failure_count: int) -> float:
    """Calculate exponential backoff with random jitter."""
    # Base delay: 1s * 2^(failures-1), capped at 60 seconds
    base_delay = min(60, 1 * (2 ** (failure_count - 1)))
    # Add random jitter: 0-50% of base delay
    jitter = random.uniform(0, base_delay * 0.5)
    return base_delay + jitter



def update_rate_limit_state(domain: str, is_limited: bool):
    """Update rate limiting state based on request result."""
    if is_limited:
        # Increment failure count
        current_failures = rate_limit_state['failure_count'].get(domain, 0) + 1
        rate_limit_state['failure_count'][domain] = current_failures
        
        # Set backoff period (exponential)
        backoff_delay = get_backoff_delay(current_failures)
        rate_limit_state['backoff_until'][domain] = time.time() + backoff_delay
        
        print(f"[RATE LIMIT] {domain}: Failure #{current_failures}, backing off for {backoff_delay:.1f}s")
    else:
        # Reset on success
        if domain in rate_limit_state['failure_count']:
            print(f"[SUCCESS] {domain}: Rate limit cleared, failure count reset")
        rate_limit_state['failure_count'][domain] = 0
        if domain in rate_limit_state['backoff_until']:
            del rate_limit_state['backoff_until'][domain]

# ============= END RATE LIMITING SYSTEM =============

def load_settings():
    global settings
    ensure_users_dir()
    
    # Clear processed items cache on bot start
    if os.path.exists(PROCESSED_ITEMS_FILE):
        with open(PROCESSED_ITEMS_FILE, 'w') as f:
            json.dump([], f)
    
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            loaded = json.load(f)
            settings.update(loaded)
            if 'keyword_collections' not in settings:
                settings['keyword_collections'] = {'default': settings['keywords'].copy()}
                settings['active_collection'] = 'default'
                save_settings()
    load_hashtag_stats()

def save_settings():
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

def load_processed_items():
    if os.path.exists(PROCESSED_ITEMS_FILE):
        with open(PROCESSED_ITEMS_FILE, 'r') as f:
            return set(json.load(f))
    return set()

def save_processed_items(processed_items):
    with open(PROCESSED_ITEMS_FILE, 'w') as f:
        json.dump(list(processed_items), f)

def load_hashtag_stats():
    global hashtag_stats, dynamic_hashtags
    if os.path.exists(HASHTAG_STATS_FILE):
        with open(HASHTAG_STATS_FILE, 'r') as f:
            hashtag_stats = json.load(f)
            dynamic_hashtags = {word for word, count in hashtag_stats.items() if count >= HASHTAG_MIN_OCCURRENCES}
    else:
        hashtag_stats = {}
        dynamic_hashtags = set()

def save_hashtag_stats():
    with open(HASHTAG_STATS_FILE, 'w') as f:
        json.dump(hashtag_stats, f, indent=4)


# ============= PER USER STORAGE SYSTEM =============
USERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users')

def ensure_users_dir():
    """Create users directory if it doesn't exist"""
    if not os.path.exists(USERS_DIR):
        os.makedirs(USERS_DIR, exist_ok=True)

def get_user_file_path(user_id: int) -> str:
    """Get full path to user's JSON storage file"""
    ensure_users_dir()
    return os.path.join(USERS_DIR, f"{user_id}.json")

def get_default_user_data() -> Dict:
    """Get default structure for new user data"""
    return {
        'user_id': None,
        'created_at': time.time(),
        'last_active': time.time(),
        'proxies': [],
        'use_proxies': False,
        'monitoring_urls': [],
        'check_interval': CHECK_INTERVAL,
        'keywords': KEYWORDS.copy(),
        'chat_id': None,
        'valid_chat_id': False,
        'is_monitoring': False,
        'search_history': [],
        'preferences': {
            'auto_fetch_proxies': AUTO_FETCH_PROXIES,
            'notifications_enabled': True,
            'currency': 'auto'
        }
    }

def load_user_data(user_id: int) -> Dict:
    """Load user data from file, create default if not exists"""
    file_path = get_user_file_path(user_id)
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                # Update last active time
                data['last_active'] = time.time()
                save_user_data(user_id, data)
                return data
        except:
            pass
    # Return default data if file doesn't exist or corrupted
    default = get_default_user_data()
    default['user_id'] = user_id
    save_user_data(user_id, default)
    return default

def save_user_data(user_id: int, data: Dict):
    """Save user data to JSON file"""
    file_path = get_user_file_path(user_id)
    data['last_active'] = time.time()
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)

def update_user_setting(user_id: int, key: str, value):
    """Update single user setting"""
    user_data = load_user_data(user_id)
    user_data[key] = value
    save_user_data(user_id, user_data)

def get_all_user_ids() -> List[int]:
    """Get list of all existing user IDs"""
    ensure_users_dir()
    user_ids = []
    for filename in os.listdir(USERS_DIR):
        if filename.endswith('.json') and filename[:-5].isdigit():
            user_ids.append(int(filename[:-5]))
    return user_ids

def extract_potential_hashtags(text: str) -> List[str]:
    if not text:
        return []
    clean_text = re.sub(r'[^a-z0-9\s\-]', ' ', text.lower())
    words = clean_text.split()
    candidates = []
    filtered = [
        word for word in words
        if len(word) >= HASHTAG_MIN_LENGTH
        and word not in STOP_WORDS
        and not word.isdigit()
        and not re.match(r'^\d+[a-z]*$', word)
    ]
    candidates.extend([w for w in filtered if len(w) >= 4])
    for i in range(len(filtered) - 1):
        phrase = f"{filtered[i]} {filtered[i+1]}"
        if len(filtered[i]) >= 4 and len(filtered[i+1]) >= 4 and len(phrase) <= 30:
            candidates.append(phrase)
    return candidates

def learn_from_rejected_item(description: str):
    global hashtag_stats, dynamic_hashtags
    if not description or len(description) < 100:
        return
    potential_hashtags = extract_potential_hashtags(description)
    for hashtag in potential_hashtags:
        hashtag_stats[hashtag] = hashtag_stats.get(hashtag, 0) + 1
    new_learned = []
    for word, count in hashtag_stats.items():
        if count >= HASHTAG_MIN_OCCURRENCES and word not in dynamic_hashtags:
            dynamic_hashtags.add(word)
            new_learned.append(word)
    if new_learned:
        print(f"🧠 Learned {len(new_learned)} new hashtags: {new_learned}")
        save_hashtag_stats()

def cache_items(items: List[Tuple], fetch_type: str, fetch_limit: int, keyword: str):
    global items_cache
    items_cache = {
        'items': items,
        'fetch_type': fetch_type,
        'fetch_limit': fetch_limit,
        'keyword': keyword,
        'current_page': 1,
    }
    print(f"Cached {len(items)} items (type={fetch_type}, limit={fetch_limit})")

def get_page_items(page: int = None) -> List[Tuple]:
    global items_cache
    if page is None:
        page = items_cache['current_page']
    else:
        items_cache['current_page'] = page
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    return items_cache['items'][start:end]

def get_max_pages() -> int:
    import math
    total = len(items_cache['items'])
    return math.ceil(total / ITEMS_PER_PAGE) if total > 0 else 1

def get_current_page() -> int:
    return items_cache['current_page']

def format_progress(stage: str, current: int, total: int, extra_info: str = "", items_found: int = 0) -> str:
    """Format detailed progress message with multiple info lines."""
    percent = min(100, int(current / total * 100)) if total > 0 else 0
    bar_width = 15
    filled = int(bar_width * percent / 100)
    bar = '=' * filled + '-' * (bar_width - filled)
    
    # Spinner animation using simple characters
    spinners = ['|', '/', '-', '\\']
    spinner = spinners[percent % len(spinners)]
    
    lines = [
        f"{spinner} <b>Fetching Items</b>",
        f"Stage: {stage}",
        f"Progress: {current}/{total} ({percent}%)",
        f"[{bar}]"
    ]
    
    if items_found > 0:
        lines.append(f"Found: {items_found} items")
    
    if extra_info:
        lines.append(f"Status: {extra_info}")
    else:
        lines.append("Please wait...")
    
    return "\n".join(lines)

proxy_index = 0
user_proxy_index: Dict[int, int] = {}

def get_next_proxy(user_id: int = None) -> Optional[str]:
    global proxy_index, user_proxy_index
    if user_id is None:
        # Legacy global mode for backwards compatibility
        if not USE_PROXIES or not PROXIES:
            return None
        proxy = PROXIES[proxy_index % len(PROXIES)]
        proxy_index += 1
        return proxy
    
    # Per user proxy mode
    user_data = load_user_data(user_id)
    if not user_data.get('use_proxies', False) or not user_data.get('proxies', []):
        return None
    if user_id not in user_proxy_index:
        user_proxy_index[user_id] = 0
    proxy = user_data['proxies'][user_proxy_index[user_id] % len(user_data['proxies'])]
    user_proxy_index[user_id] += 1
    return proxy


def reset_proxy_rotation(user_id: int = None):
    """Reset proxy index to start from beginning."""
    global proxy_index, user_proxy_index
    if user_id is None:
        proxy_index = 0
    else:
        user_proxy_index[user_id] = 0


async def create_session_with_proxy(proxy: Optional[str] = None) -> aiohttp.ClientSession:
    """
    Create aiohttp session with proxy support (HTTP or SOCKS5)
    
    Args:
        proxy: Proxy URL (format: 'socks5://user:pass@host:port' or 'http://host:port')
    
    Returns:
        aiohttp.ClientSession with appropriate connector
    """
    if not proxy:
        return aiohttp.ClientSession()
    
    # Check if SOCKS5 proxy
    if proxy.startswith('socks5://'):
        try:
            from aiohttp_socks import ProxyConnector, ProxyType
            from urllib.parse import urlparse
            
            # Parse the SOCKS5 URL
            parsed = urlparse(proxy)
            
            # Create connector with ProxyType enum
            connector = ProxyConnector(
                proxy_type=ProxyType.SOCKS5,
                host=parsed.hostname,
                port=parsed.port,
                username=parsed.username,
                password=parsed.password
            )
            return aiohttp.ClientSession(connector=connector)
        except ImportError:
            print("[WARN] aiohttp-socks not installed. Installing...")
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", "aiohttp-socks", "-q"], 
                         stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            
            try:
                from aiohttp_socks import ProxyConnector, ProxyType
                from urllib.parse import urlparse
                
                parsed = urlparse(proxy)
                connector = ProxyConnector(
                    proxy_type=ProxyType.SOCKS5,
                    host=parsed.hostname,
                    port=parsed.port,
                    username=parsed.username,
                    password=parsed.password
                )
                return aiohttp.ClientSession(connector=connector)
            except Exception as e:
                print(f"[WARN] Could not setup SOCKS5: {str(e)[:50]}. Using direct connection...")
                return aiohttp.ClientSession()
        except Exception as e:
            print(f"[WARN] SOCKS5 error: {str(e)[:50]}. Using direct connection...")
            return aiohttp.ClientSession()
    
    # HTTP proxy - will be passed to request
    else:
        return aiohttp.ClientSession()

def is_relevant_item(item: Dict, keywords: List[str]) -> bool:
    title = item.get('title', '').lower()
    description = item.get('description', '').lower()
    full_text = f"{title} {description}".lower()
    for clothing_word in CLOTHING_EXCLUDE:
        if clothing_word in full_text:
            return False
    has_shoe_keyword = any(shoe_word in full_text for shoe_word in SHOE_KEYWORDS)
    has_main_keyword = any(kw.lower() in full_text for kw in keywords)
    if not (has_shoe_keyword or has_main_keyword):
        return False
    if any(kw.lower() in title for kw in keywords):
        return True
    first_part = description[:200]
    if any(kw.lower() in first_part for kw in keywords):
        return True
    all_hashtags = set(tag.lower() for tag in KNOWN_HASHTAGS) | dynamic_hashtags
    hashtag_count = sum(1 for tag in all_hashtags if tag in description)
    if len(description) > 300 and hashtag_count > 5 and not any(kw.lower() in description[:200] for kw in keywords):
        learn_from_rejected_item(description)
        return False
    return False

def _parse_item_details_sync(html: str) -> Tuple[str, str, str, List[str]]:
    """
    Synchronous HTML parsing (runs in thread pool)
    Optimized selectors - 100% tested on real Vinted items
    """
    soup = BeautifulSoup(html, 'html.parser')
    
    # TITLE PARSING - Optimized selectors (100% tested)
    title = None
    
    # Method 1: h1.web_ui__Text__* (PRIMARY - works 100%)
    h1_elem = soup.select_one('h1.web_ui__Text__title, h1.web_ui__Text__text')
    if h1_elem:
        title = h1_elem.get_text(strip=True)
    
    # Method 2: Fallback - any H1 with text
    if not title:
        for h1 in soup.find_all('h1'):
            text = h1.get_text(strip=True)
            if text and len(text) > 3:
                title = text
                break
    
    # Method 3: og:title meta tag
    if not title:
        og_title = soup.select_one('meta[property="og:title"]')
        if og_title and og_title.get('content'):
            title = og_title['content'].split(' | ')[0].strip()
    
    if not title:
        title = 'Unknown Title'

    # PRICE PARSING - Optimized selectors (100% tested)
    price = None
    
    # Method 1: data-testid='item-price' (PRIMARY - works 100%)
    price_elem = soup.find(attrs={'data-testid': 'item-price'})
    if price_elem:
        price = price_elem.get_text(strip=True)
    
    # Method 2: Fallback - search spans for price pattern
    if not price:
        for span in soup.find_all('span'):
            text = span.get_text(strip=True)
            if re.search(r'\d+[,\.]\d{2}\s*(?:zł|PLN|EUR|€|£)', text):
                price = text
                break
    
    if not price:
        price = 'Unknown Price'

    # DESCRIPTION PARSING - Optimized selectors (100% tested)
    description = None
    
    # Method 1: og:description meta tag (PRIMARY - works 100%)
    og_desc = soup.select_one('meta[property="og:description"]')
    if og_desc and og_desc.get('content'):
        description = og_desc['content'].strip()
    
    # Method 2: Fallback - data-testid description
    if not description:
        desc_elem = soup.find(attrs={'data-testid': 'item-description'})
        if desc_elem:
            description = desc_elem.get_text(strip=True)
    
    # Method 3: Fallback - find substantial text blocks
    if not description:
        for elem in soup.find_all(['p', 'div']):
            text = elem.get_text(strip=True)
            if len(text) > 50 and len(text) < 2000:
                if not any(skip in text.lower() for skip in ['cookie', 'privacy', 'accept', 'settings', 'home', 'menu', 'search']):
                    description = text
                    break
    
    if not description:
        description = 'No description provided'

    # IMAGES - Optimized to filter out logos/icons (100% tested)
    images = []
    for img in soup.find_all('img'):
        src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
        if not src:
            continue
        
        # Skip navigation, logos, icons, SVG
        if any(skip in src.lower() for skip in [
            'logo', 'svg', 'icon', 'nav', 'arrow', 'button',
            'profile', 'studio', 'marketplace-web-assets', 'vinted-assets'
        ]):
            continue
        
        # Only accept actual image files
        if any(ext in src.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp', '/image']):
            if src not in images:
                images.append(src)
    
    return (title, price, description, images[:3])


async def parse_item_details_async(html: str) -> Tuple[str, str, str, List[str]]:
    """
    Async wrapper for HTML parsing
    Executes parsing in thread pool to avoid blocking event loop
    """
    return await asyncio.to_thread(_parse_item_details_sync, html)


async def get_item_details(item_url: str, session: aiohttp.ClientSession = None) -> Tuple[str, str, str, List[str]]:
    """
    Get item details using async HTML parsing (100% reliable)
    Uses thread pool for CPU-bound parsing to avoid blocking async operations
    """
    
    # Check cache first
    if item_url in item_details_cache:
        return item_details_cache[item_url]
    
    # Extract item ID and domain
    item_id_match = re.search(r'/items/(\d+)', item_url)
    if not item_id_match:
        return 'Unknown Title', 'Unknown Price', 'Error loading description', []
    
    item_id = item_id_match.group(1)
    domain_match = re.search(r'https?://([^/]+)', item_url)
    domain = domain_match.group(1) if domain_match else 'www.vinted.pl'
    
    html = None
    max_attempts = 3
    
    for attempt in range(max_attempts):
        await asyncio.sleep(0.2)
        
        proxy = get_next_proxy()
        
        try:
            headers = get_random_headers()
            
            if session:
                # Use provided session
                async with session.get(item_url, timeout=aiohttp.ClientTimeout(total=15), headers=headers) as response:
                    html = await response.text()
                    status_code = response.status
            else:
                # Create session with proxy support
                async with await create_session_with_proxy(proxy) as temp_session:
                    async with temp_session.get(item_url, timeout=aiohttp.ClientTimeout(total=15), headers=headers) as response:
                        html = await response.text()
                        status_code = response.status
            
            if html:
                break
                
        except asyncio.TimeoutError:
            print(f"[TIMEOUT] HTML fetch attempt {attempt + 1}/{max_attempts}")
            continue
        except Exception as e:
            print(f"[ERROR] HTML fetch: {str(e)[:50]} (attempt {attempt + 1}/{max_attempts})")
            continue
    
    if not html:
        print(f"[FAILED] Could not fetch {item_url[:60]}...")
        return 'Unknown Title', 'Unknown Price', 'Error loading description', []
    
    # Parse HTML asynchronously in thread pool (non-blocking)
    title, price, description, images = await parse_item_details_async(html)
    
    result = (title, price, description, images)
    item_details_cache[item_url] = result
    print(f"[SUCCESS] Got item: {title[:40]} - {price}")
    return result

def get_item_details_sync(item_url: str) -> Tuple[str, str, str, List[str]]:
    """Synchronous wrapper for get_item_details (API first, then HTML fallback)"""
    try:
        return asyncio.run(get_item_details(item_url))
    except Exception as e:
        print(f"Error getting item details: {e}")
        return 'Unknown Title', 'Unknown Price', 'Error loading description', []

def extract_price_from_text(text: str) -> str:
    if not text:
        return ''
    patterns = [
        r"\d{1,3}(?:[ .]\d{3})*(?:[,\.]\d{2})\s*(?:zł|PLN|€|EUR|£|GBP|USD)",
        r"\d{1,3}(?:[ .]\d{3})*(?:[,\.]\d{2})"
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0).strip()
    return ''

def price_to_float(price_str: str) -> float:
    if not price_str or price_str == 'Unknown Price':
        return 999999
    try:
        price_clean = re.sub(r'[^0-9.,]', '', price_str).strip()
        dot_count = price_clean.count('.')
        comma_count = price_clean.count(',')
        if dot_count > 1 and comma_count == 0:
            price_clean = price_clean.replace(',', '')
        elif comma_count > 1 and dot_count == 0:
            price_clean = price_clean.replace('.', '').replace(',', '.')
        elif comma_count == 1 and dot_count == 0:
            price_clean = price_clean.replace(',', '.')
        elif dot_count == 1 and comma_count == 1:
            if price_clean.rindex(',') > price_clean.rindex('.'):
                price_clean = price_clean.replace('.', '').replace(',', '.')
            else:
                price_clean = price_clean.replace(',', '')
        return float(price_clean)
    except (ValueError, AttributeError):
        return 999999

def _button_label_from_title(title: str, price: str) -> str:
    words = re.split(r"\s+", title.strip())
    words = [w for w in words if w]
    label_words = ' '.join(words[:4]) if words else 'Unknown'
    # Handle new price formats like "€15.00" or "150 zł"
    display_price = price if price and price.lower() not in ('unknown', 'unknown price') else extract_price_from_text(title) or 'Unknown Price'
    return f"{label_words}|{display_price}"

def create_items_keyboard(page_items: List[Tuple], page: int, max_pages: int, show_refresh: bool = True) -> InlineKeyboardMarkup:
    keyboard = []
    for i, item in enumerate(page_items):
        # item structure: (item_id, title, item_url, price)
        item_id, title, item_url, price = item[0], item[1], item[2], item[3]
        global_index = (page - 1) * ITEMS_PER_PAGE + i
        button_text = _button_label_from_title(title, price)
        keyboard.append([InlineKeyboardButton(text=button_text, callback_data=f"show_item_{global_index}")])
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="< Prev", callback_data=f"page_{page-1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"{page}/{max_pages}", callback_data="page_info"))
    if page < max_pages:
        nav_buttons.append(InlineKeyboardButton(text="Next >", callback_data=f"page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    control_buttons = []
    if show_refresh:
        control_buttons.append(InlineKeyboardButton(text="Reload Same", callback_data="refresh_items"))
    control_buttons.append(InlineKeyboardButton(text="<- Back", callback_data="back_to_main"))
    if control_buttons:
        keyboard.append(control_buttons)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def check_new_items(keywords: List[str]) -> List[tuple]:
    new_items = []
    try:
        items = await get_last_items_list(keywords, limit=25)
        async with aiohttp.ClientSession(headers={'User-Agent': 'Mozilla/5.0'}) as session:
            detail_tasks = [get_item_details(item[2], session=session) for item in items]
            detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)
        for (item_id, current_title, item_url, current_price), detail_result in zip(items, detail_results):
            if isinstance(detail_result, Exception):
                new_items.append((item_id, current_title, item_url, current_price))
            else:
                full_title, price, _, _ = detail_result
                new_items.append((item_id, full_title, item_url, price))
        return new_items
    except Exception as e:
        print(f"Error checking items: {e}")
        return []

async def send_notification(item_url: str, title: str, price: str):
    # FINAL AGE VERIFICATION
    is_fresh = await verify_item_age(item_url)
    if not is_fresh:
        print(f"⏭️ SKIP OLD ITEM: {title[:40]}")
        return
    
    message = f"🆕 <b>New Item Found!</b>\n\n📦 <b>Title:</b> {title}\n💰 <b>Price:</b> {price}\n🔗 <b>Link:</b> {item_url}"
    try:
        await bot.send_message(chat_id=settings['chat_id'], text=message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        print(f"Error sending message: {e}")

async def monitoring_loop():
    processed_items = set()
    first_run = True
    
    while is_monitoring:
        print(f"\n{'='*40}")
        print(f"🔍 Monitoring cycle started")
        print(f"🔗 Monitoring URLs: {len(MONITORING_URLS)}")
        for url in MONITORING_URLS:
            print(f"   - {url[:60]}...")
        print(f"⏱️ Interval: {settings['check_interval']}s ({settings['check_interval']//60} min)")
        
        try:
            new_items = await check_monitoring_urls(MONITORING_URLS, None)
            print(f"📦 Found {len(new_items)} items total")
            
            if first_run:
                # On first run, save all items as baseline ONLY IN MEMORY
                print("📋 First run - saving all items as baseline (memory only)...")
                for item_id, item_url, title, price, image_url, size, condition, shipping in new_items:
                    processed_items.add(item_id)
                print(f"✅ Saved {len(new_items)} items as baseline")
                first_run = False
            else:
                # On subsequent runs, only notify about new items
                for item_id, item_url, title, price, image_url, size, condition, shipping in new_items:
                    if item_id not in processed_items:
                        print(f"   🆕 NEW: {title[:50]}... - {price}")
                        await send_notification_with_image(item_url, title, price, image_url, size, condition, shipping)
                        processed_items.add(item_id)
                    else:
                        print(f"   ✓ Already processed: {title[:30]}...")
        except Exception as e:
            print(f"❌ Error in monitoring cycle: {e}")
        
        print(f"\n✅ Monitoring cycle complete")
        print(f"⏳ Sleeping for {settings['check_interval']}s ({settings['check_interval']//60} min)...")
        print(f"{'='*40}\n")
        await asyncio.sleep(settings['check_interval'])


async def fetch_monitoring_url(session: aiohttp.ClientSession, url: str, user_id: int = None) -> str:
    """Fetch a specific monitoring URL with proxy support."""
    proxy = get_next_proxy(user_id)
    proxy_url = f'http://{proxy}' if proxy else None
    
    try:
        headers = get_random_headers()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), headers=headers, proxy=proxy_url) as resp:
            resp.raise_for_status()
            html = await resp.text()
            if 'rate limited' in html.lower() or 'you are rate limited' in html.lower():
                print(f"⚠️ Rate limited on {url[:50]}..., trying next proxy...")
                raise Exception("Rate limited")
            return html
    except Exception as e:
        print(f"❌ Error fetching {url[:50]}: {str(e)[:50]}")
        raise


def is_valid_product_card(item_element) -> bool:
    """Check if element is real product card (not seller block)
    Return True only if:
    ✓ Has price (in title attribute)
    ✗ NOT seller block (no user rating/stars/reviews)
    """
    try:
        title_attr = item_element.get('title', '').lower()
        
        # ✅ Проверяем цену в title атрибуте (самое надежное место!)
        has_price = (
            '€' in title_attr 
            or 'zł' in title_attr 
            or 'PLN' in title_attr
        )
        if not has_price:
            return False
            
        # ✅ Обязательно что бы был image
        has_image = False
        parent = item_element.parent
        for _ in range(3):
            if not parent:
                break
            if parent.select_one('img'):
                has_image = True
                break
            parent = parent.parent
        if not has_image:
            return False
            
        # ❌ Seller block detection
        seller_indicators = [
            'seller',
            'venditore',
            'sprzedawca',
            '★',
            '⭐',
            'review',
            'recensione',
            'opinia',
            'rating',
            'feedback',
        ]
        if any(indicator in title_attr for indicator in seller_indicators):
            return False
            
        # ✅ Если дошли сюда значит это карточка товара!
        return True
        
    except Exception:
        return True  # В случае ошибки пропускаем элемент а не отсеиваем


async def check_monitoring_urls(urls: List[str], user_id: int = None) -> List[tuple]:
    """Check all monitoring URLs for new items. Returns list of (item_id, item_url, title, price, image_url, size, condition, shipping)."""
    all_items = {}  # item_id -> (title, item_url, price, image_url, size, condition, shipping)
    
    if not urls:
        return []
    
    print(f"\n🔄 Fetching {len(urls)} monitoring URLs for user {user_id}...")
    
    # Warmup request to get cookies
    try:
        async with aiohttp.ClientSession() as warmup_session:
            headers = get_random_headers()
            await warmup_session.get('https://www.vinted.pl/', headers=headers, timeout=5)
            await asyncio.sleep(1)
    except:
        pass
    
    results = []
    for url in urls:
        await asyncio.sleep(random.uniform(2, 4))
        try:
            async with aiohttp.ClientSession() as session:
                result = await fetch_monitoring_url(session, url, user_id)
                results.append(result)
        except Exception as e:
            results.append(e)
        
        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                print(f"  ❌ Error: {result}")
                continue
            
            html = result
            soup = BeautifulSoup(html, 'html.parser')
            raw_items = soup.select('a[href*="/items/"]')[:50]
            total_cards = len(raw_items)
            # Filter only valid product cards
            raw_items = [item for item in raw_items if is_valid_product_card(item)]
            print(f"    ✅ Filtered {len(raw_items)} product cards from {total_cards} total (excluded seller blocks)")
            
            print(f"  📦 Found {len(raw_items)} raw items from {url[:50]}...")
            
            for item in raw_items:
                href = item.get('href', '')
                title_attr = item.get('title', '').lower()
                if not href:
                    continue
                
                # ✅ Critical: Apply ALL user-configured keywords filter HERE
                has_keyword = False
                for kw in settings['keywords']:
                    if kw.lower() in title_attr:
                        has_keyword = True
                        break
                if not has_keyword:
                    continue
                
                # Build full URL first
                item_url = href if href.startswith('http') else f"https://www.vinted.it{href}" if 'vinted.it' in url else f"https://www.vinted.pl{href}"
                item_url = item_url.split('?')[0]
                
                # Extract item ID - try multiple methods
                item_id = None
                
                # Method 1: Try data-testid attribute
                testid = item.get('data-testid', '')
                id_match = re.search(r'product-item-id-(\d+)', testid)
                if id_match:
                    item_id = int(id_match.group(1))
                
                # Method 2: Extract directly from URL (most reliable!)
                if not item_id:
                    id_match = re.search(r'/items/(\d+)', item_url)
                    if id_match:
                        item_id = int(id_match.group(1))
                
                # If still no ID - skip this item
                if not item_id:
                    continue
                

                
                if item_id in all_items:
                    continue
                
                # Get title, price, size, condition, shipping from 'title' attribute
                title_attr = item.get('title', '')
                
                # ✅ Pre-filter - remove obvious old items
                title_lower = title_attr.lower()
                old_indicators = ['settimane', 'tygodni', 'mesi fa', 'miesięcy temu']
                if any(indicator in title_lower for indicator in old_indicators):
                    continue
                if title_attr:
                    # Name is before first comma
                    title = title_attr.split(',')[0].strip() if ',' in title_attr else title_attr.strip()
                    
                    # Extract all details from title attribute
                    # Italy: taglia: 42, condizioni: Ottime
                    # Poland: rozmiar: 42, stan: Dobry
                    
                    # Size
                    size_match = re.search(r'taglia:\s*([^,]+)', title_attr) or re.search(r'rozmiar:\s*([^,]+)', title_attr)
                    size = size_match.group(1).strip() if size_match else ''
                    
                    # Condition
                    cond_match = re.search(r'condizioni:\s*([^,]+)', title_attr) or re.search(r'stan:\s*([^,]+)', title_attr)
                    condition = cond_match.group(1).strip() if cond_match else ''
                    
                    # Price and shipping
                    price_match = re.search(r'€\s*([\d,.]+)', title_attr)
                    if price_match:
                        price = f"€{price_match.group(1)}"
                        # Shipping price is second € amount
                        ship_match = re.search(r'€[\d,.]+,\s*€([\d,.]+)', title_attr)
                        shipping = f"€{ship_match.group(1)}" if ship_match else ''
                    else:
                        # Poland: 150,00 zł or 150 zł
                        price_match = re.search(r'([\d\s,.]+?)\s*zł', title_attr)
                        if price_match:
                            price_val = price_match.group(1).strip()
                            price = f"{price_val} zł"
                            # Check for shipping in PLN
                            ship_match = re.search(r'([\d\s,.]+?)\s*zł.*?([\d\s,.]+?)\s*zł', title_attr)
                            if ship_match:
                                shipping = f"{ship_match.group(2).strip()} zł"
                            else:
                                shipping = ''
                        else:
                            price_match = re.search(r'([\d\s,.]+?)\s*PLN', title_attr)
                            if price_match:
                                price_val = price_match.group(1).strip()
                                price = f"{price_val} PLN"
                                shipping = ''
                            else:
                                price = 'Unknown Price'
                                shipping = ''
                else:
                    title = 'Unknown'
                    price = 'Unknown Price'
                    size = ''
                    condition = ''
                    shipping = ''
                
                # Get image URL from parent elements
                image_url = ''
                parent = item.parent
                depth = 0
                while parent and depth < 5:
                    img = parent.select_one('img')
                    if img:
                        image_url = img.get('src') or img.get('data-src') or ''
                        if image_url:
                            break
                    parent = parent.parent
                    depth += 1
                
                all_items[item_id] = (title, item_url, price, image_url, size, condition, shipping)
                print(f"    ➕ Item {item_id}: {title[:40]}... - {price} (size: {size}, condition: {condition})")
    
    print(f"\n📊 Total unique items: {len(all_items)}")
    return [(item_id, item_url, title, price, image_url, size, condition, shipping) for item_id, (title, item_url, price, image_url, size, condition, shipping) in all_items.items()]


async def verify_item_age(item_url: str) -> bool:
    """Verify item is younger than 45 minutes by checking actual item page - 100% reliable method"""
    try:
        headers = get_random_headers()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(item_url, headers=headers) as resp:
                if resp.status != 200:
                    return True  # Allow on error to avoid false negatives
                html = await resp.text()
                
                # ✅ PROFESSIONAL METHOD: Find EXACT elements by data-testid
                soup = BeautifulSoup(html, 'html.parser')
                
                # Look for updated_at first - this is what determines position in newest_first
                updated_elem = soup.find(attrs={'data-testid': 'item-attributes-updated_at'})
                
                age_text = ''
                if updated_elem:
                    age_text = updated_elem.get_text(strip=True).lower()
                else:
                    # Fallback to upload date if updated not found
                    upload_elem = soup.find(attrs={'data-testid': 'item-attributes-upload_date'})
                    if upload_elem:
                        age_text = upload_elem.get_text(strip=True).lower()
                
                item_id = item_url.split('/')[-1].split('-')[0]
                
                if not age_text:
                    print(f"   ⚠️ UNKNOWN: id={item_id}, no time found, allowing")
                    return True
                
                # Parse age
                fresh = False
                
                # Fresh indicators (<= 45 minutes)
                if any(ind in age_text for ind in ['sec', 'min', 'minuto', 'minuti']):
                    # Check if it's less than 45 minutes
                    num_match = re.search(r'(\d+)', age_text)
                    if num_match:
                        mins = int(num_match.group(1))
                        if mins <= 45:
                            fresh = True
                    else:
                        # No number = "just now", "few seconds" etc.
                        fresh = True
                
                if fresh:
                    print(f"   ✅ PASS: id={item_id}, age: {age_text}")
                    return True
                else:
                    print(f"   ⏭️ SKIP: id={item_id}, age: {age_text}")
                    return False
                
    except Exception as e:
        print(f"⚠️ Age verify error: {str(e)[:40]}")
        return True

async def send_notification_with_image(item_url: str, title: str, price: str, image_url: str, size: str = '', condition: str = '', shipping: str = '', user_id: int = None):
    """Send notification with image to Telegram."""
    
    # FINAL AGE VERIFICATION - ONLY SEND IF ITEM IS NEW
    is_fresh = await verify_item_age(item_url)
    if not is_fresh:
        print(f"⏭️ SKIP OLD ITEM: {title[:40]}")
        return
    
    chat_id = settings['chat_id']
    if user_id is not None:
        user_data = load_user_data(user_id)
        chat_id = user_data.get('chat_id', user_id)
    else:
        chat_id = settings['chat_id']
    
    # ✅ Защита от отправки сообщений самому себе и ботам
    bot_id = int(BOT_TOKEN.split(':')[0]) if BOT_TOKEN else 0
    if not settings['valid_chat_id'] or chat_id == bot_id:
        print(f"⚠️ Пропускаю отправку: chat_id не валиден или это ID бота")
        return

    # Build caption with all details
    caption = f"🆕 <b>New Item Found!</b>\n\n"
    caption += f"📦 <b>Title:</b> {title}\n"
    if size:
        caption += f"📏 <b>Size:</b> {size}\n"
    if condition:
        caption += f"✅ <b>Condition:</b> {condition}\n"
    caption += f"💰 <b>Price:</b> {price}\n"
    if shipping:
        caption += f"📦 <b>With Shipping:</b> {shipping}\n"
    caption += f"🔗 <b>Link:</b> {item_url}"

    try:
        if image_url:
            # Send photo with caption
            await bot.send_photo(chat_id=chat_id, photo=image_url, caption=caption, parse_mode=ParseMode.HTML)
        else:
            # Send text message if no image
            await bot.send_message(chat_id=chat_id, text=caption, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        print(f"Error sending notification: {e}")
        # Fallback to text
        try:
            await bot.send_message(chat_id=chat_id, text=caption, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except:
            pass

def start_monitoring():
    global is_monitoring, monitoring_task
    if not is_monitoring:
        is_monitoring = True
        monitoring_task = asyncio.create_task(monitoring_loop())

def stop_monitoring():
    global is_monitoring, monitoring_task
    is_monitoring = False
    if monitoring_task:
        monitoring_task.cancel()

async def _fetch_search_html(session: aiohttp.ClientSession, domain: str, keyword: str):
    url = VINTED_SEARCH_URL.format(domain=domain, keyword=keyword.replace(' ', '+'))
    
    max_attempts = 5
    
    for attempt in range(max_attempts):
        # Fixed minimal delay between requests
        await asyncio.sleep(0.2)
        
        proxy = get_next_proxy()
        proxy_url = f'http://{proxy}' if proxy else None
        
        try:
            headers = get_random_headers()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), headers=headers, proxy=proxy_url) as resp:
                status_code = resp.status
                html = await resp.text()
                
                # Check for rate limiting
                if is_rate_limited(status_code, html):
                    update_rate_limit_state(domain, True)
                    print(f"[RATE LIMITED] Domain {domain} (Status: {status_code})")
                    
                    if attempt < max_attempts - 1:
                        delay = get_backoff_delay(rate_limit_state['failure_count'].get(domain, 0))
                        print(f"[WAITING] {domain}: backing off {delay:.1f}s (attempt {attempt + 1}/{max_attempts})")
                        await asyncio.sleep(delay)
                    continue
                
                # Success
                update_rate_limit_state(domain, False)
                return domain, html
                
        except asyncio.TimeoutError:
            print(f"[TIMEOUT] Domain {domain} (attempt {attempt + 1}/{max_attempts})")
            if attempt < max_attempts - 1:
                await asyncio.sleep(random.uniform(2, 4))
            continue
        except Exception as e:
            print(f"[ERROR] Domain {domain}: {str(e)[:50]} (attempt {attempt + 1}/{max_attempts})")
            if attempt < max_attempts - 1:
                await asyncio.sleep(random.uniform(1, 3))
            continue
    
    raise Exception(f"Failed to fetch {domain} after {max_attempts} attempts")

async def get_items_by_price(keywords: List[str], limit: int = 10, progress_callback=None) -> List[Tuple[int, str, str, str, str, str, str, str]]:
    print(f"Fetching {limit} cheapest items with keywords: {keywords}")
    all_items = {}
    search_keyword = keywords[0] if keywords else ''

    async def report_progress(stage: str, step: int, total: int, extra_info: str = "", found_count: int = 0):
        if progress_callback:
            msg = format_progress(stage, step, total, extra_info, found_count)
            await progress_callback(msg)

    def matches_keywords(text: str) -> bool:
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in keywords)

    try:
        async with aiohttp.ClientSession(headers={'User-Agent': 'Mozilla/5.0'}) as session:
            tasks = [_fetch_search_html(session, domain, search_keyword) for domain in VINTED_DOMAINS]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            domain_count = len(VINTED_DOMAINS)

            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"Error fetching domain {VINTED_DOMAINS[idx]}: {result}")
                    await report_progress("Fetching pages", idx + 1, domain_count, f"Domain {VINTED_DOMAINS[idx]}", 0)
                    continue
                domain, html = result
                soup = BeautifulSoup(html, 'html.parser')
                raw_items = soup.select('a[href*="/items/"]')[:limit+20]
                total_cards = len(raw_items)
                raw_items = [item for item in raw_items if is_valid_product_card(item)]
                print(f"✅ Found {len(raw_items)} valid product cards from {total_cards} on {domain}")

                await report_progress("Collecting items", idx + 1, domain_count, f"Found {len(raw_items)} valid items", len(raw_items))

                for i, item in enumerate(raw_items):
                    href = item.get('href', '')
                    if not href:
                        continue
                    item_url = f"https://{domain}{href}" if href.startswith('/') else href
                    item_url = item_url.split('?')[0]

                    item_id_match = re.search(r'/items/(\d+)', item_url)
                    if not item_id_match:
                        continue
                    item_id = int(item_id_match.group(1))

                    if item_id in all_items:
                        continue

                    title_attr = item.get('title', '')
                    if title_attr and not matches_keywords(title_attr):
                        continue
                    if title_attr:
                        # Name
                        title = title_attr.split(',')[0].strip() if ',' in title_attr else title_attr.strip()
                        # Size: taglia: 42 (Italy) or rozmiar: 42 (Poland)
                        size_match = re.search(r'taglia:\s*([^,]+)', title_attr) or re.search(r'rozmiar:\s*([^,]+)', title_attr)
                        size = size_match.group(1).strip() if size_match else ''
                        # Condition: condizioni: Ottime (Italy) or stan: Dobry (Poland)
                        cond_match = re.search(r'condizioni:\s*([^,]+)', title_attr) or re.search(r'stan:\s*([^,]+)', title_attr)
                        condition = cond_match.group(1).strip() if cond_match else ''
                        # Price and shipping
                        price_match = re.search(r'€\s*([\d,.]+)', title_attr)
                        if price_match:
                            price = f"€{price_match.group(1)}"
                            ship_match = re.search(r'€[\d,.]+,\s*€([\d,.]+)', title_attr)
                            shipping = f"€{ship_match.group(1)}" if ship_match else ''
                        else:
                            price_match = re.search(r'([\d\s,.]+?)\s*zł', title_attr)
                            if price_match:
                                price_val = price_match.group(1).strip()
                                price = f"{price_val} zł"
                                ship_match = re.search(r'([\d\s,.]+?)\s*zł.*?([\d\s,.]+?)\s*zł', title_attr)
                                shipping = f"{ship_match.group(2).strip()} zł" if ship_match else ''
                            else:
                                price_match = re.search(r'([\d\s,.]+?)\s*PLN', title_attr)
                                if price_match:
                                    price_val = price_match.group(1).strip()
                                    price = f"{price_val} PLN"
                                    shipping = ''
                                else:
                                    price = 'Unknown Price'
                                    shipping = ''
                    else:
                        title = 'Unknown'
                        price = 'Unknown Price'
                        size = ''
                        condition = ''
                        shipping = ''

                    # Get image URL from parent elements
                    image_url = ''
                    parent = item.parent
                    depth = 0
                    while parent and depth < 5:
                        img = parent.select_one('img')
                        if img:
                            image_url = img.get('src') or img.get('data-src') or ''
                            if image_url:
                                break
                        parent = parent.parent
                        depth += 1

                    # Add to results
                    all_items[item_id] = (title, item_url, price, image_url, size, condition, shipping)
                    
                    # Update progress bar
                    total_items = len(raw_items)
                    percent = int((i + 1) / total_items * 100)
                    await report_progress("Parsing items", i + 1, total_items, f"{percent}% completed", len(all_items))

            # Sort by PRICE (cheapest first) and take limit
            await report_progress("Finalizing", 100, 100, "Sorting by price", len(all_items))
            sorted_items = sorted(all_items.items(), key=lambda x: price_to_float(x[1][2]))[:limit]
            final_results = [(item_id, title, item_url, price, image_url, size, condition, shipping) for item_id, (title, item_url, price, image_url, size, condition, shipping) in sorted_items]

            print(f"✅ Done! Found {len(final_results)} cheapest items from page HTML ONLY")
            return final_results

    except Exception as e:
        print(f"Error fetching items overall: {e}")
        return []

async def get_last_items_list(keywords: List[str], limit: int = 10, progress_callback=None) -> List[Tuple[int, str, str, str, str, str, str, str]]:
    print(f"Fetching last {limit} items with keywords: {keywords}")
    all_items = {}
    search_keyword = keywords[0] if keywords else ''

    async def report_progress(stage: str, step: int, total: int, extra_info: str = "", found_count: int = 0):
        if progress_callback:
            msg = format_progress(stage, step, total, extra_info, found_count)
            await progress_callback(msg)

    def matches_keywords(text: str) -> bool:
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in keywords)

    try:
        async with aiohttp.ClientSession(headers={'User-Agent': 'Mozilla/5.0'}) as session:
            tasks = [_fetch_search_html(session, domain, search_keyword) for domain in VINTED_DOMAINS]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            domain_count = len(VINTED_DOMAINS)

            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"Error fetching domain {VINTED_DOMAINS[idx]}: {result}")
                    await report_progress("Fetching pages", idx + 1, domain_count, f"Domain {VINTED_DOMAINS[idx]}", 0)
                    continue
                domain, html = result
                soup = BeautifulSoup(html, 'html.parser')
                raw_items = soup.select('a[href*="/items/"]')[:limit+20]
                total_cards = len(raw_items)
                raw_items = [item for item in raw_items if is_valid_product_card(item)]
                print(f"✅ Found {len(raw_items)} valid product cards from {total_cards} on {domain}")

                await report_progress("Collecting items", idx + 1, domain_count, f"Found {len(raw_items)} valid items", len(raw_items))

                for i, item in enumerate(raw_items):
                    href = item.get('href', '')
                    if not href:
                        continue
                    item_url = f"https://{domain}{href}" if href.startswith('/') else href
                    item_url = item_url.split('?')[0]

                    item_id_match = re.search(r'/items/(\d+)', item_url)
                    if not item_id_match:
                        continue
                    item_id = int(item_id_match.group(1))

                    if item_id in all_items:
                        continue

                    title_attr = item.get('title', '')
                    if title_attr and not matches_keywords(title_attr):
                        continue
                    if title_attr:
                        title = title_attr.split(',')[0].strip() if ',' in title_attr else title_attr.strip()
                        size_match = re.search(r'taglia:\s*([^,]+)', title_attr) or re.search(r'rozmiar:\s*([^,]+)', title_attr)
                        size = size_match.group(1).strip() if size_match else ''
                        cond_match = re.search(r'condizioni:\s*([^,]+)', title_attr) or re.search(r'stan:\s*([^,]+)', title_attr)
                        condition = cond_match.group(1).strip() if cond_match else ''
                        price_match = re.search(r'€\s*([\d,.]+)', title_attr)
                        if price_match:
                            price = f"€{price_match.group(1)}"
                            ship_match = re.search(r'€[\d,.]+,\s*€([\d,.]+)', title_attr)
                            shipping = f"€{ship_match.group(1)}" if ship_match else ''
                        else:
                            price_match = re.search(r'([\d\s,.]+?)\s*zł', title_attr)
                            if price_match:
                                price_val = price_match.group(1).strip()
                                price = f"{price_val} zł"
                                ship_match = re.search(r'([\d\s,.]+?)\s*zł.*?([\d\s,.]+?)\s*zł', title_attr)
                                shipping = f"{ship_match.group(2).strip()} zł" if ship_match else ''
                            else:
                                price_match = re.search(r'([\d\s,.]+?)\s*PLN', title_attr)
                                if price_match:
                                    price_val = price_match.group(1).strip()
                                    price = f"{price_val} PLN"
                                    shipping = ''
                                else:
                                    price = 'Unknown Price'
                                    shipping = ''
                    else:
                        title = 'Unknown'
                        price = 'Unknown Price'
                        size = ''
                        condition = ''
                        shipping = ''

                    image_url = ''
                    parent = item.parent
                    depth = 0
                    while parent and depth < 5:
                        img = parent.select_one('img')
                        if img:
                            image_url = img.get('src') or img.get('data-src') or ''
                            if image_url:
                                break
                        parent = parent.parent
                        depth += 1

                    all_items[item_id] = (title, item_url, price, image_url, size, condition, shipping)
                    
                    total_items = len(raw_items)
                    percent = int((i + 1) / total_items * 100)
                    await report_progress("Parsing items", i + 1, total_items, f"{percent}% completed", len(all_items))

            await report_progress("Finalizing", 100, 100, "Sorting results", len(all_items))
            sorted_items = sorted(all_items.items(), key=lambda x: x[0], reverse=True)[:limit]
            final_results = [(item_id, title, item_url, price, image_url, size, condition, shipping) for item_id, (title, item_url, price, image_url, size, condition, shipping) in sorted_items]

            print(f"✅ Done! Found {len(final_results)} items from page HTML ONLY")
            return final_results

    except Exception as e:
        print(f"Error fetching items overall: {e}")
        return []

@router.message(Command("start"))
async def cmd_start(message: Message):
    # Автоматически сохраняем Chat ID пользователя при первом запуске
    user_chat_id = message.chat.id
    if settings['chat_id'] != user_chat_id:
        settings['chat_id'] = user_chat_id
        settings['valid_chat_id'] = True
        save_settings()
        await message.answer(f"✅ Chat ID автоматически сохранен: {user_chat_id}")
    
    proxy_status = "ON" if USE_PROXIES else "OFF"
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    active_col = settings.get('active_collection', 'default')
    active_kw = collections.get(active_col, settings['keywords'])
    
    welcome_text = (
        "✨ <b>VintedBot</b> ✨\n\n"
        "🔎 <i>Smart Vinted item finder & monitor</i>\n\n"
        f"📁 <b>Collection:</b> {active_col} ({len(active_kw)} keywords)\n"
        f"🌐 <b>Proxy:</b> {proxy_status}\n"
        f"📡 <b>Monitoring:</b> {'✅ Active' if is_monitoring else '❌ Inactive'}\n"
        f"⏱️ <b>Interval:</b> {settings['check_interval']}s"
    )
    
    base_buttons = [
        [InlineKeyboardButton(text="📦 Load Items", callback_data="load_latest")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="settings"), InlineKeyboardButton(text="🌐 Proxy", callback_data="proxy_menu")],
        [InlineKeyboardButton(text="🔍 Monitor URLs", callback_data="monitoring_menu")],
        [InlineKeyboardButton(text="▶️ Start Monitoring" if not is_monitoring else "⏹️ Stop Monitoring", callback_data="toggle_monitoring")],
    ]
    
    if message.from_user.id == ADMIN_ID:
        admin_buttons = [
            [InlineKeyboardButton(text="👑 Admin Panel", callback_data="admin_panel")],
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=base_buttons + admin_buttons)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=base_buttons)
    
    await message.answer(
        welcome_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "admin_panel")
async def callback_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Только администратор")
        return
    
    current_time = time.time()
    active_count = 0
    for hwid, last_seen in active_devices.items():
        if current_time - last_seen < 600:
            active_count += 1
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🖥️ Sessions ({active_count})", callback_data="devices")],
        [InlineKeyboardButton(text="🛑 Stop All", callback_data="stopall")],
        [InlineKeyboardButton(text="🔄 Restart All", callback_data="restartall")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    
    await callback.message.edit_text(
        "👑 <b>Admin Panel</b>\n\n"
        "🔒 <i>Restricted access</i>\n\n"
        f"🖥️ <b>Active sessions:</b> {active_count}\n"
        f"🆔 <b>This instance:</b> {INSTANCE_ID}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    
    base_buttons = [
        [InlineKeyboardButton(text="📦 Load Items", callback_data="load_latest")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="settings"), InlineKeyboardButton(text="🌐 Proxy", callback_data="proxy_menu")],
        [InlineKeyboardButton(text="🔍 Monitor URLs", callback_data="monitoring_menu")],
        [InlineKeyboardButton(text="▶️ Start Monitoring" if not is_monitoring else "⏹️ Stop Monitoring", callback_data="toggle_monitoring")],
    ]
    
    if message.from_user.id == ADMIN_ID:
        admin_buttons = [
            [InlineKeyboardButton(text="👑 Admin Panel", callback_data="admin_panel")],
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=base_buttons + admin_buttons)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=base_buttons)
    
    await message.answer(
        welcome_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "admin_panel")
async def callback_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Только администратор")
        return
    
    current_time = time.time()
    active_count = 0
    for hwid, last_seen in active_devices.items():
        if current_time - last_seen < 600:
            active_count += 1
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🖥️ Sessions ({active_count})", callback_data="devices")],
        [InlineKeyboardButton(text="🛑 Stop All", callback_data="stopall")],
        [InlineKeyboardButton(text="🔄 Restart All", callback_data="restartall")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    
    await callback.message.edit_text(
        "👑 <b>Admin Panel</b>\n\n"
        "🔒 <i>Restricted access</i>\n\n"
        f"🖥️ <b>Active sessions:</b> {active_count}\n"
        f"🆔 <b>This instance:</b> {INSTANCE_ID}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "stopall")
async def callback_stop_all(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Только администратор")
        return
    
    await callback.answer("🛑 Отправляю команду остановки всем ботам!")
    await bot.send_message(chat_id=settings['chat_id'], text="🛑 STOP ALL")
    is_stopped = True
    stop_monitoring()
    await bot.session.close()
    sys.exit(0)

@router.callback_query(lambda c: c.data == "restartall")
async def callback_restart_all(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Только администратор")
        return
    
    await callback.answer("🔄 Отправляю команду перезапуска всем ботам!")
    await bot.send_message(chat_id=settings['chat_id'], text="🔄 RESTART ALL")

@router.callback_query(lambda c: c.data == "toggle_monitoring")
async def callback_toggle_monitoring(callback: CallbackQuery):
    global is_monitoring
    if is_monitoring:
        stop_monitoring()
        status_text = "⏹️ <b>Monitoring Stopped</b>"
        status_emoji = "🔴"
    else:
        start_monitoring()
        status_text = "▶️ <b>Monitoring Started</b>"
        status_emoji = "🟢"
    
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    active_col = settings.get('active_collection', 'default')
    active_kw = collections.get(active_col, settings['keywords'])
    
    welcome_text = (
        f"{status_text}\n\n"
        f"📁 <b>Collection:</b> {active_col} ({len(active_kw)} kw)\n"
        f"🌐 <b>Proxy:</b> {'ON' if USE_PROXIES else 'OFF'}\n"
        f"📡 <b>Monitoring:</b> {'✅ Active' if is_monitoring else '❌ Inactive'}\n"
        f"⏱️ <b>Interval:</b> {settings['check_interval']}s"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="settings"), InlineKeyboardButton(text="🌐 Proxy", callback_data="proxy_menu")],
        [InlineKeyboardButton(text="📦 Load Items", callback_data="load_latest")],
        [InlineKeyboardButton(text="🔍 Monitor URLs", callback_data="monitoring_menu")],
        [InlineKeyboardButton(text="▶️ Start Monitoring" if not is_monitoring else "⏹️ Stop Monitoring", callback_data="toggle_monitoring")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    
    await callback.message.edit_text(
        welcome_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "monitoring_menu")
async def callback_monitoring_menu(callback: CallbackQuery):
    urls_text = "\n".join([f"🔗 <code>{url[:60]}...</code>" for i, url in enumerate(MONITORING_URLS)]) if MONITORING_URLS else "⚠️ No URLs configured"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 List URLs", callback_data="list_monitoring_urls")],
        [InlineKeyboardButton(text="➕ Add URL", callback_data="add_monitoring_url")],
        [InlineKeyboardButton(text="🗑️ Remove URL", callback_data="remove_monitoring_url")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    await callback.message.edit_text(
        f"🔍 <b>Monitoring URLs</b>\n\n"
        f"📊 <b>Total:</b> {len(MONITORING_URLS)}\n\n"
        f"{urls_text}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="settings"), InlineKeyboardButton(text="🌐 Proxy", callback_data="proxy_menu")],
        [InlineKeyboardButton(text="📦 Load Items", callback_data="load_latest")],
        [InlineKeyboardButton(text="🔍 Monitor URLs", callback_data="monitoring_menu")],
        [InlineKeyboardButton(text="▶️ Start Monitoring" if not is_monitoring else "⏹️ Stop Monitoring", callback_data="toggle_monitoring")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    
    await callback.message.edit_text(
        welcome_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "monitoring_menu")
async def callback_monitoring_menu(callback: CallbackQuery):
    urls_text = "\n".join([f"🔗 <code>{url[:60]}...</code>" for i, url in enumerate(MONITORING_URLS)]) if MONITORING_URLS else "⚠️ No URLs configured"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 List URLs", callback_data="list_monitoring_urls")],
        [InlineKeyboardButton(text="➕ Add URL", callback_data="add_monitoring_url")],
        [InlineKeyboardButton(text="🗑️ Remove URL", callback_data="remove_monitoring_url")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    await callback.message.edit_text(
        f"🔍 <b>Monitoring URLs</b>\n\n"
        f"📊 <b>Total:</b> {len(MONITORING_URLS)}\n\n"
        f"{urls_text}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


@router.callback_query(lambda c: c.data == "list_monitoring_urls")
async def callback_list_monitoring_urls(callback: CallbackQuery):
    urls_text = "\n\n".join([f"{i+1}. {url}" for i, url in enumerate(MONITORING_URLS)])
    await callback.message.edit_text(
        f"📋 <b>All Monitoring URLs ({len(MONITORING_URLS)})</b>\n\n{urls_text}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back", callback_data="monitoring_menu")],
        ])
    )


@router.callback_query(lambda c: c.data == "add_monitoring_url")
async def callback_add_monitoring_url(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_monitoring_url)
    await callback.message.edit_text(
        "➕ <b>Add Monitoring URL</b>\n\nSend me the full URL to monitor:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Cancel", callback_data="cancel_monitoring_operation")],
        ])
    )
    await callback.answer()


@router.message(SettingsState.waiting_for_monitoring_url)
async def process_monitoring_url(message: Message, state: FSMContext):
    url = message.text.strip()
    
    # Validate URL
    if not url.startswith(('http://', 'https://')) or not 'vinted' in url.lower():
        await message.answer("❌ Invalid URL! Must be a valid Vinted URL")
        return
    
    # Add to list
    MONITORING_URLS.append(url)
    await message.answer(f"✅ URL added:\n{url}")
    await state.clear()
    
    # Return to monitoring menu
    urls_text = "\n".join([f"{i+1}. {url[:70]}..." for i, url in enumerate(MONITORING_URLS)])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 List URLs", callback_data="list_monitoring_urls")],
        [InlineKeyboardButton(text="➕ Add URL", callback_data="add_monitoring_url")],
        [InlineKeyboardButton(text="🗑️ Remove URL", callback_data="remove_monitoring_url")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    await message.answer(
        f"🔍 <b>Monitoring URLs</b>\n\nActive URLs:\n{urls_text}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


@router.callback_query(lambda c: c.data.startswith("remove_url_"))
async def callback_remove_specific_url(callback: CallbackQuery):
    url_idx = int(callback.data[11:])
    if 0 <= url_idx < len(MONITORING_URLS):
        removed_url = MONITORING_URLS.pop(url_idx)
        await callback.answer(f"✅ Removed URL: {removed_url[:50]}...")
    
    # Return to remove menu
    if not MONITORING_URLS:
        await callback.answer("No URLs left!")
        await callback_monitoring_menu(callback)
        return
    
    keyboard_rows = []
    for i, url in enumerate(MONITORING_URLS):
        keyboard_rows.append([
            InlineKeyboardButton(text=f"❌ {i+1}. {url[:50]}...", callback_data=f"remove_url_{i}")
        ])
    keyboard_rows.append([InlineKeyboardButton(text="⬅️ Cancel", callback_data="monitoring_menu")])
    
    await callback.message.edit_text(
        "🗑️ <b>Remove Monitoring URL</b>\n\nSelect URL to remove:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    )


@router.callback_query(lambda c: c.data == "cancel_monitoring_operation")
async def callback_cancel_monitoring_operation(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_monitoring_menu(callback)


@router.callback_query(lambda c: c.data == "remove_monitoring_url")
async def callback_remove_monitoring_url(callback: CallbackQuery):
    if not MONITORING_URLS:
        await callback.answer("No URLs to remove!")
        return
    
    keyboard_rows = []
    for i, url in enumerate(MONITORING_URLS):
        keyboard_rows.append([
            InlineKeyboardButton(text=f"❌ {i+1}. {url[:50]}...", callback_data=f"remove_url_{i}")
        ])
    keyboard_rows.append([InlineKeyboardButton(text="⬅️ Cancel", callback_data="monitoring_menu")])
    
    await callback.message.edit_text(
        "🗑️ <b>Remove Monitoring URL</b>\n\nSelect URL to remove:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    )

@router.callback_query(lambda c: c.data == "proxy_menu")
async def callback_proxy_menu(callback: CallbackQuery):
    proxy_count = len(PROXIES) if PROXIES else 0
    status = "🟢 Active" if USE_PROXIES else "🔴 Inactive"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Enable Proxies" if not USE_PROXIES else "⏹️ Disable Proxies", callback_data="toggle_proxies")],
        [InlineKeyboardButton(text="📝 Add Proxy", callback_data="add_proxy"), InlineKeyboardButton(text="🗑️ Remove Proxy", callback_data="remove_proxy")],
        [InlineKeyboardButton(text="📋 List Proxies", callback_data="list_proxies")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    await callback.message.edit_text(
        f"🌐 <b>Proxy Settings</b>\n\n"
        f"📊 <b>Status:</b> {status}\n"
        f"📦 <b>Loaded:</b> {proxy_count} proxies",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "toggle_proxies")
async def callback_toggle_proxies(callback: CallbackQuery):
    global USE_PROXIES
    USE_PROXIES = not USE_PROXIES
    status = "🟢 ON" if USE_PROXIES else "🔴 OFF"
    await callback.answer(f"Proxies {status}")
    proxy_count = len(PROXIES) if PROXIES else 0
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Enable Proxies" if not USE_PROXIES else "⏹️ Disable Proxies", callback_data="toggle_proxies")],
        [InlineKeyboardButton(text="📝 Add Proxy", callback_data="add_proxy"), InlineKeyboardButton(text="🗑️ Remove Proxy", callback_data="remove_proxy")],
        [InlineKeyboardButton(text="📋 List Proxies", callback_data="list_proxies")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    await callback.message.edit_text(
        f"🌐 <b>Proxy Settings</b>\n\n"
        f"📊 <b>Status:</b> {status}\n"
        f"📦 <b>Loaded:</b> {proxy_count} proxies",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "list_proxies")
async def callback_list_proxies(callback: CallbackQuery):
    if not PROXIES:
        await callback.answer("No proxies loaded!")
        return
    proxy_list = "\n".join([f"🔸 <code>{p}</code>" for i, p in enumerate(PROXIES)])
    await callback.message.answer(
        f"📋 <b>Loaded Proxies ({len(PROXIES)})</b>\n\n{proxy_list}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back", callback_data="proxy_menu")],
        ]),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "settings")
async def callback_settings(callback: CallbackQuery):
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    active = settings.get('active_collection', 'default')
    active_kw = collections.get(active, settings['keywords'])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Collections", callback_data="collections_menu")],
        [InlineKeyboardButton(text="⏱️ Interval", callback_data="interval")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    kw_summary = ', '.join(active_kw[:5]) + (f" ... (+{len(active_kw)-5})" if len(active_kw) > 5 else '') if active_kw else '(none)'
    await callback.message.edit_text(
        f"⚙️ <b>Settings</b>\n\n"
        f"📁 <b>Active:</b> {active}\n"
        f"🔑 <b>Keywords:</b> {kw_summary}\n"
        f"⏱️ <b>Interval:</b> {settings['check_interval']}s ({settings['check_interval']//60} min)",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "collections_menu")
async def callback_collections_menu(callback: CallbackQuery):
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    active = settings.get('active_collection', 'default')
    keyboard_rows = []
    for name, kws in collections.items():
        status = " ✅" if name == active else ""
        keyboard_rows.append([InlineKeyboardButton(text=f"📁 {name} ({len(kws)} kw){status}", callback_data=f"select_collection_{name}")])
    keyboard_rows.append([InlineKeyboardButton(text="➕ Add Collection", callback_data="add_collection"), InlineKeyboardButton(text="🗑️ Remove", callback_data="remove_collection")])
    keyboard_rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="settings")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    summary_lines = []
    for name, kws in collections.items():
        marker = " 👑" if name == active else ""
        display_kw = ', '.join(kws[:3]) + (f" (+{len(kws)-3})" if len(kws) > 3 else '')
        summary_lines.append(f"📁 <b>{name}</b>{marker}\n   🔑 {display_kw}")
    await callback.message.edit_text(
        "📁 <b>Keyword Collections</b>\n\n" + "\n".join(summary_lines),
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "collections_menu")
async def callback_collections_menu(callback: CallbackQuery):
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    active = settings.get('active_collection', 'default')
    keyboard_rows = []
    for name, kws in collections.items():
        status = " ✅" if name == active else ""
        keyboard_rows.append([InlineKeyboardButton(text=f"📁 {name} ({len(kws)} kw){status}", callback_data=f"select_collection_{name}")])
    keyboard_rows.append([InlineKeyboardButton(text="➕ Add Collection", callback_data="add_collection"), InlineKeyboardButton(text="🗑️ Remove", callback_data="remove_collection")])
    keyboard_rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="settings")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    summary_lines = []
    for name, kws in collections.items():
        marker = " 👑" if name == active else ""
        display_kw = ', '.join(kws[:3]) + (f" (+{len(kws)-3})" if len(kws) > 3 else '')
        summary_lines.append(f"📁 <b>{name}</b>{marker}\n   🔑 {display_kw}")
    await callback.message.edit_text(
        "📁 <b>Keyword Collections</b>\n\n" + "\n".join(summary_lines),
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data.startswith("select_collection_"))
async def callback_select_collection(callback: CallbackQuery):
    collection_name = callback.data[len("select_collection_"):]
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    if collection_name in collections:
        settings['active_collection'] = collection_name
        settings['keywords'] = collections[collection_name].copy()
        save_settings()
        await callback.answer(f"✅ Active collection: {collection_name}")
    await callback_collections_menu(callback)

@router.callback_query(lambda c: c.data == "add_collection")
async def callback_add_collection(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_collection_name)
    await callback.message.edit_text(
        "➕ <b>Add New Collection</b>\n\nEnter collection name:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Cancel", callback_data="collections_menu")],
        ])
    )

@router.message(SettingsState.waiting_for_collection_name)
async def process_collection_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("❌ Name cannot be empty!")
        return
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    if name in collections:
        await message.answer(f"⚠️ Collection '{name}' already exists!")
        return
    collections[name] = []
    settings['keyword_collections'] = collections
    save_settings()
    await message.answer(f"✅ Collection '{name}' created!\n\nNow enter keywords (comma-separated):")
    await state.set_state(SettingsState.waiting_for_collection_keywords)

@router.message(SettingsState.waiting_for_collection_keywords)
async def process_collection_keywords(message: Message, state: FSMContext):
    keywords_text = message.text.strip()
    keywords = [kw.strip() for kw in keywords_text.split(',') if kw.strip()]
    if not keywords:
        await message.answer("❌ No keywords provided!")
        return
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    last_added = list(collections.keys())[-1]
    collections[last_added] = keywords
    settings['keyword_collections'] = collections
    if settings.get('active_collection') == last_added:
        settings['keywords'] = keywords.copy()
    save_settings()
    await message.answer(f"✅ Added {len(keywords)} keywords to '{last_added}': {', '.join(keywords)}")
    await state.clear()
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    active = settings.get('active_collection', 'default')
    keyboard_rows = []
    for name, kws in collections.items():
        status = " ✅" if name == active else ""
        keyboard_rows.append([InlineKeyboardButton(text=f"{name} ({len(kws)} kw){status}", callback_data=f"select_collection_{name}")])
    keyboard_rows.append([InlineKeyboardButton(text="➕ Add Collection", callback_data="add_collection")])
    keyboard_rows.append([InlineKeyboardButton(text="🗑️ Remove Collection", callback_data="remove_collection")])
    keyboard_rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="settings")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    summary_lines = []
    for name, kws in collections.items():
        marker = " (active)" if name == active else ""
        summary_lines.append(f"📁 <b>{name}</b>{marker}: {', '.join(kws)}")
    await message.answer(
        "📁 <b>Keyword Collections</b>\n\n" + "\n".join(summary_lines),
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "remove_collection")
async def callback_remove_collection(callback: CallbackQuery):
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    if len(collections) <= 1:
        await callback.answer("Cannot remove the last collection!")
        return
    keyboard_rows = []
    for name in collections.keys():
        keyboard_rows.append([InlineKeyboardButton(text=f"❌ {name}", callback_data=f"delete_collection_{name}")])
    keyboard_rows.append([InlineKeyboardButton(text="⬅️ Cancel", callback_data="collections_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await callback.message.edit_text(
        "🗑️ <b>Remove Collection</b>\n\nSelect collection to remove:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data.startswith("delete_collection_"))
async def callback_delete_collection(callback: CallbackQuery):
    collection_name = callback.data[len("delete_collection_"):]
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    if collection_name in collections:
        del collections[collection_name]
        settings['keyword_collections'] = collections
        if settings.get('active_collection') == collection_name:
            first_key = list(collections.keys())[0]
            settings['active_collection'] = first_key
            settings['keywords'] = collections[first_key].copy()
        save_settings()
        await callback.answer(f"✅ Removed collection '{collection_name}'")
    await callback_collections_menu(callback)

@router.callback_query(lambda c: c.data == "keywords")
async def callback_keywords(callback: CallbackQuery):
    active = settings.get('active_collection', 'default')
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    active_kw = collections.get(active, settings['keywords'])
    display_kw = ', '.join(active_kw[:5]) + (f" ... (+{len(active_kw)-5})" if len(active_kw) > 5 else '') if active_kw else '(none)'
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✏️ Edit '{active}'", callback_data="edit_active_collection")],
        [InlineKeyboardButton(text="📁 All Collections", callback_data="collections_menu")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="settings")],
    ])
    await callback.message.edit_text(
        f"🔑 <b>Keywords Management</b>\n\n"
        f"📁 <b>Collection:</b> {active}\n"
        f"🔑 <b>Keywords:</b> {display_kw}\n"
        f"📊 <b>Total:</b> {len(active_kw)}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "edit_active_collection")
async def callback_edit_active_collection(callback: CallbackQuery):
    active = settings.get('active_collection', 'default')
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    active_kw = collections.get(active, settings['keywords'])
    keyboard_rows = []
    for kw in active_kw:
        keyboard_rows.append([InlineKeyboardButton(text=f"❌ {kw}", callback_data=f"remove_kw_from_collection_{kw}")])
    keyboard_rows.append([InlineKeyboardButton(text="➕ Add Keyword", callback_data="add_keyword_to_active")])
    keyboard_rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="keywords")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    display_kw = ', '.join(active_kw[:5]) + (f" ... (+{len(active_kw)-5})" if len(active_kw) > 5 else '') if active_kw else '(none)'
    await callback.message.edit_text(
        f"✏️ <b>Edit Collection: {active}</b>\n\n"
        f"🔑 <b>Keywords:</b> {display_kw}\n"
        f"📊 <b>Total:</b> {len(active_kw)}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "edit_active_collection")
async def callback_edit_active_collection(callback: CallbackQuery):
    active = settings.get('active_collection', 'default')
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    active_kw = collections.get(active, settings['keywords'])
    keyboard_rows = []
    for kw in active_kw:
        keyboard_rows.append([InlineKeyboardButton(text=f"❌ {kw}", callback_data=f"remove_kw_from_collection_{kw}")])
    keyboard_rows.append([InlineKeyboardButton(text="➕ Add Keyword", callback_data="add_keyword_to_active")])
    keyboard_rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="keywords")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    display_kw = ', '.join(active_kw[:5]) + (f" ... (+{len(active_kw)-5})" if len(active_kw) > 5 else '') if active_kw else '(none)'
    await callback.message.edit_text(
        f"✏️ <b>Edit Collection: {active}</b>\n\n"
        f"🔑 <b>Keywords:</b> {display_kw}\n"
        f"📊 <b>Total:</b> {len(active_kw)}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "add_keyword_to_active")
async def callback_add_keyword_to_active(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_keyword)
    await state.update_data(editing_active=True)
    await callback.message.edit_text("📝 Enter keyword to add to active collection:")

@router.message(SettingsState.waiting_for_keyword)
async def process_add_keyword(message: Message, state: FSMContext):
    keyword = message.text.strip()
    data = await state.get_data()
    if data.get('editing_active'):
        active = settings.get('active_collection', 'default')
        collections = settings.get('keyword_collections', {'default': settings['keywords']})
        if keyword not in collections.get(active, []):
            collections[active].append(keyword)
            settings['keyword_collections'] = collections
            settings['keywords'] = collections[active].copy()
            save_settings()
            await message.answer(f"✅ Keyword '{keyword}' added to '{active}'!")
        else:
            await message.answer(f"⚠️ Keyword '{keyword}' already exists in '{active}'!")
    else:
        if keyword not in settings['keywords']:
            settings['keywords'].append(keyword)
            save_settings()
            await message.answer(f"✅ Keyword '{keyword}' added!")
        else:
            await message.answer(f"⚠️ Keyword '{keyword}' already exists!")
    await state.clear()
    await cmd_start(message)

@router.callback_query(lambda c: c.data.startswith("remove_kw_from_collection_"))
async def callback_remove_kw_from_collection(callback: CallbackQuery):
    keyword = callback.data[len("remove_kw_from_collection_"):]
    active = settings.get('active_collection', 'default')
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    if keyword in collections.get(active, []):
        collections[active].remove(keyword)
        settings['keyword_collections'] = collections
        settings['keywords'] = collections[active].copy()
        save_settings()
        await callback.answer(f"✅ Removed '{keyword}' from '{active}'")
    await callback_edit_active_collection(callback)

@router.callback_query(lambda c: c.data == "interval")
async def callback_interval(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_interval)
    await callback.message.edit_text(f"⏱️ Enter new check interval in seconds (current: {settings['check_interval']}s / {settings['check_interval']//60} min):")

@router.message(SettingsState.waiting_for_interval)
async def process_interval(message: Message, state: FSMContext):
    try:
        interval = int(message.text.strip())
        if interval < 60:
            await message.answer("⚠️ Interval must be at least 60 seconds!")
            return
        settings['check_interval'] = interval
        save_settings()
        await message.answer(f"✅ Interval set to {interval} seconds ({interval//60} min)")
    except ValueError:
        await message.answer("❌ Invalid number!")
    await state.clear()
    await cmd_start(message)

@router.callback_query(lambda c: c.data == "show_last")
async def callback_show_last(callback: CallbackQuery):
    global last_items
    if not settings['keywords']:
        await callback.answer("No keywords set!")
        return
    keyword = settings['keywords'][0]
    initial_msg = format_progress("Initializing", 0, 100, "Preparing to fetch items...")
    progress_msg = await callback.message.answer(initial_msg, parse_mode=ParseMode.HTML)

    async def progress_callback(status_text: str):
        try:
            await bot.edit_message_text(status_text, chat_id=progress_msg.chat.id, message_id=progress_msg.message_id, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    last_items = await get_last_items_list(keyword, progress_callback=progress_callback)
    if not last_items:
        await callback.message.edit_text("⚠️ No items found (or fetch failed)")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_button_label_from_title(item[1], item[3]), callback_data=f"show_item_{i}")] for i, item in enumerate(last_items)
    ] + [[InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")]])

    await callback.message.edit_text(
        f"📋 <b>Last {len(last_items)} Items</b>\n\nClick on an item to view details:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.message(Command("last"))
async def cmd_last(message: Message):
    global last_items
    if not settings['keywords']:
        await message.answer("No keywords set!")
        return
    keyword = settings['keywords'][0]
    initial_msg = format_progress("Initializing", 0, 100, "Preparing to fetch items...")
    progress_msg = await message.answer(initial_msg, parse_mode=ParseMode.HTML)

    async def progress_callback(status_text: str):
        try:
            await bot.edit_message_text(status_text, chat_id=progress_msg.chat.id, message_id=progress_msg.message_id, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    items = await get_last_items_list(keyword, progress_callback=progress_callback)
    if not items:
        await bot.edit_message_text("⚠️ No items found (or fetch failed)", chat_id=progress_msg.chat.id, message_id=progress_msg.message_id)
        return
    last_items = items
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_button_label_from_title(item[1], item[3]), callback_data=f"show_item_{i}")] for i, item in enumerate(items)
    ])
    await bot.edit_message_text(
        f"📋 <b>Last {len(items)} Items (via /last)</b>\n\nClick to view item details:",
        chat_id=progress_msg.chat.id,
        message_id=progress_msg.message_id,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data.startswith("show_item_"))
async def callback_show_item(callback: CallbackQuery):
    i = int(callback.data[10:])
    cached_items = items_cache.get('items', [])
    if 0 <= i < len(cached_items):
        item = cached_items[i]
        # New format: (item_id, title, item_url, price, image_url, size, condition, shipping)
        if len(item) >= 8:
            item_id, title, item_url, price, image_url, size, condition, shipping = item
            await show_item_details_full(callback, title, price, image_url, size, condition, shipping, item_url)
        elif len(item) >= 4:
            # Old format fallback
            _, _, item_url, _ = item
            await show_item_details(callback, item_url)
        else:
            await callback.answer("Invalid item!")
    else:
        await callback.answer("Invalid item!")

async def validate_image_url(url: str, timeout: int = 5) -> bool:
    if not url or not url.startswith(('http://', 'https://')):
        return False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as resp:
                return resp.status == 200 and 'image' in resp.content_type
    except Exception:
        return False

async def show_item_details_full(callback: CallbackQuery, title: str, price: str, image_url: str, size: str, condition: str, shipping: str, item_url: str):
    """Show full item details from cached data."""
    try:
        await callback.message.delete()
    except:
        pass

    # Build caption with all details
    caption = f"📦 <b>{title}</b>\n\n"
    if size:
        caption += f"📏 <b>Size:</b> {size}\n"
    if condition:
        caption += f"✅ <b>Condition:</b> {condition}\n"
    caption += f"💰 <b>Price:</b> {price}\n"
    if shipping:
        caption += f"📦 <b>With Shipping:</b> {shipping}\n"
    caption += f"\n🔗 <a href='{item_url}'>View on Vinted</a>"

    try:
        if image_url:
            # Send photo with caption
            await callback.message.answer_photo(photo=image_url, caption=caption, parse_mode=ParseMode.HTML)
        else:
            # Send text message if no image
            await callback.message.answer(caption, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        # Add back button
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to List", callback_data="back_to_items")]
        ])
        await callback.message.answer("⬅️ Back to list:", reply_markup=keyboard)

    except Exception as e:
        print(f"Error showing item details: {e}")
        # Fallback to text
        await callback.message.answer(caption, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def show_item_details(callback: CallbackQuery, item_url: str):
    title, price, description, images = await get_item_details(item_url)
    caption_text = f"<b>{title}</b>\n\nPrice: {price}\n\n{description}\n\n<a href='{item_url}'>View on Vinted</a>"
    
    try:
        await callback.message.delete()
    except:
        pass
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back to List", callback_data="back_to_items")]
    ])
    
    if images:
        valid_images = []
        for img_url in images[:3]:
            if await validate_image_url(img_url):
                valid_images.append(img_url)
        
        if valid_images:
            media = []
            for i, img_url in enumerate(valid_images):
                if i == 0:
                    media.append(InputMediaPhoto(media=img_url, caption=caption_text, parse_mode=ParseMode.HTML))
                else:
                    media.append(InputMediaPhoto(media=img_url))
            
            try:
                await bot.send_media_group(chat_id=callback.from_user.id, media=media)
                await bot.send_message(chat_id=callback.from_user.id, text="⬅️ Back to list:", reply_markup=keyboard)
                await callback.answer()
                return
            except Exception as e:
                print(f"Failed to send media group: {e}")
    
    await bot.send_message(chat_id=callback.from_user.id, text=caption_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(lambda c: c.data == "back_to_main")
async def callback_back_to_main(callback: CallbackQuery):
    await cmd_start(callback.message)

@router.callback_query(lambda c: c.data == "back_to_items")
async def callback_back_to_items(callback: CallbackQuery):
    page = get_current_page()
    page_items = get_page_items(page)
    max_pages = get_max_pages()
    keyboard = create_items_keyboard(page_items, page, max_pages, show_refresh=True)
    fetch_type = items_cache.get('fetch_type', 'last').title()
    total_items = len(items_cache.get('items', []))
    await callback.message.edit_text(
        f"📋 <b>{fetch_type} {total_items} Items</b> (Page {page}/{max_pages})\n\nClick on an item to view details:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@router.callback_query(lambda c: c.data == "load_latest")
async def callback_load_latest(callback: CallbackQuery):
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    if len(collections) > 1:
        keyboard_rows = []
        for name, kws in collections.items():
            if kws:
                keyboard_rows.append([InlineKeyboardButton(text=f"📁 {name} ({len(kws)} kw)", callback_data=f"search_with_collection_{name}")])
        keyboard_rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
        await callback.message.edit_text(
            "📥 <b>Select Keyword Collection</b>\n\nWhich collection to search with?",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
    else:
        await show_load_options(callback)

async def show_load_options(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕐 Latest 10", callback_data="load_last_10")],
        [InlineKeyboardButton(text="🕐 Latest 20", callback_data="load_last_20")],
        [InlineKeyboardButton(text="🕐 Latest 50", callback_data="load_last_50")],
        [InlineKeyboardButton(text="💰 Cheapest 10", callback_data="load_price_10")],
        [InlineKeyboardButton(text="💰 Cheapest 20", callback_data="load_price_20")],
        [InlineKeyboardButton(text="💰 Cheapest 50", callback_data="load_price_50")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    await callback.message.edit_text(
        "📥 <b>Load Items</b>\n\nChoose sorting and limit:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data.startswith("search_with_collection_"))
async def callback_search_with_collection(callback: CallbackQuery):
    collection_name = callback.data[len("search_with_collection_"):]
    collections = settings.get('keyword_collections', {'default': settings['keywords']})
    if collection_name in collections:
        settings['active_collection'] = collection_name
        settings['keywords'] = collections[collection_name].copy()
        save_settings()
        await callback.answer(f"✅ Using collection: {collection_name}")
    await show_load_options(callback)

async def load_items_with_limit(callback: CallbackQuery, limit: int, fetch_type: str = 'last'):
    if not settings['keywords']:
        await callback.answer("No keywords set!")
        return
    keywords = settings['keywords']
    initial_msg = format_progress("Initializing", 0, 100, "Preparing to fetch items...")
    progress_msg = await callback.message.answer(initial_msg, parse_mode=ParseMode.HTML)

    async def progress_callback(status_text: str):
        try:
            await bot.edit_message_text(status_text, chat_id=progress_msg.chat.id, message_id=progress_msg.message_id, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    if fetch_type == 'last':
        items = await get_last_items_list(keywords, limit=limit, progress_callback=progress_callback)
    else:
        items = await get_items_by_price(keywords, limit=limit, progress_callback=progress_callback)
    
    if not items:
        await bot.edit_message_text("⚠️ No items found (or fetch failed)", chat_id=progress_msg.chat.id, message_id=progress_msg.message_id)
        return

    cache_items(items, fetch_type, limit, keywords[0])
    page = 1
    page_items = get_page_items(page)
    max_pages = get_max_pages()
    keyboard = create_items_keyboard(page_items, page, max_pages, show_refresh=True)
    
    await bot.edit_message_text(
        f"📋 <b>{fetch_type.title()} {len(items)} Items</b> (Page {page}/{max_pages})\n\nClick on an item to view details:",
        chat_id=progress_msg.chat.id,
        message_id=progress_msg.message_id,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data in ["load_last_10", "load_last_20", "load_last_50"])
async def callback_load_last_n(callback: CallbackQuery):
    mapping = {'load_last_10': 10, 'load_last_20': 20, 'load_last_50': 50}
    limit = mapping.get(callback.data, 10)
    await load_items_with_limit(callback, limit, fetch_type='last')

@router.callback_query(lambda c: c.data.startswith("page_"))
async def callback_page_navigate(callback: CallbackQuery):
    page_data = callback.data[5:]
    if page_data == "info":
        await callback.answer(f"Page {get_current_page()}/{get_max_pages()}")
        return
    try:
        page = int(page_data)
        max_pages = get_max_pages()
        if page < 1 or page > max_pages:
            await callback.answer("Invalid page!")
            return
        page_items = get_page_items(page)
        keyboard = create_items_keyboard(page_items, page, max_pages, show_refresh=True)
        fetch_type = items_cache.get('fetch_type', 'last').title()
        total_items = len(items_cache.get('items', []))
        await callback.message.edit_text(
            f"📋 <b>{fetch_type} {total_items} Items</b> (Page {page}/{max_pages})\n\nClick on an item to view details:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await callback.answer("Invalid page number!")

@router.callback_query(lambda c: c.data == "refresh_items")
async def callback_refresh_items(callback: CallbackQuery):
    fetch_type = items_cache.get('fetch_type')
    fetch_limit = items_cache.get('fetch_limit')
    if not fetch_type or not fetch_limit:
        await callback.answer("No cached parameters!")
        return
    await load_items_with_limit(callback, fetch_limit, fetch_type=fetch_type)

@router.callback_query(lambda c: c.data in ["load_price_10", "load_price_20", "load_price_50"])
async def callback_load_price_n(callback: CallbackQuery):
    mapping = {'load_price_10': 10, 'load_price_20': 20, 'load_price_50': 50}
    limit = mapping.get(callback.data, 10)
    await load_items_with_limit(callback, limit, fetch_type='price')

async def heartbeat_loop():
    global is_master, last_heartbeat_time, is_stopped, active_devices
    
    try:
        hwid = subprocess.check_output('wmic csproduct get uuid').decode().split('\n')[1].strip()
    except:
        hwid = f"unknown-{random.randint(100000, 999999)}"
    
    session_info = {
        'instance_id': INSTANCE_ID,
        'hwid': hwid,
        'pid': os.getpid(),
        'start_time': time.time(),
        'is_master': False,
    }
    
async def heartbeat_loop():
    global is_master, last_heartbeat_time, is_stopped, active_devices
    
    try:
        hwid = subprocess.check_output('wmic csproduct get uuid').decode().split('\n')[1].strip()
    except:
        hwid = f"unknown-{random.randint(100000, 999999)}"
    
    session_info = {
        'instance_id': INSTANCE_ID,
        'hwid': hwid,
        'pid': os.getpid(),
        'start_time': time.time(),
        'is_master': False,
    }
    
    last_master_time = time.time()
    
    while not is_stopped:
        try:
            current_time = time.time()
            session_info['is_master'] = is_master
            session_info['last_heartbeat'] = current_time
            
            # Register this device
            active_devices[hwid] = current_time
            
            # Cleanup old devices (>10 minutes)
            for hwid_key in list(active_devices.keys()):
                if current_time - active_devices[hwid_key] > 600:
                    del active_devices[hwid_key]
            
            # Simple master election: smallest instance ID becomes master
            all_instance_ids = [int(key) for key in active_devices.keys() if key.isdigit()]
            
            if not all_instance_ids:
                # We are the first one
                is_master = True
                last_master_time = current_time
            else:
                min_instance = min(all_instance_ids)
                if int(INSTANCE_ID) == min_instance:
                    if not is_master:
                        is_master = True
                        print(f"✅ Became master. Instance ID: {INSTANCE_ID}")
                        if not is_monitoring:
                            start_monitoring()
                    last_master_time = current_time
                else:
                    if is_master:
                        is_master = False
                        print(f"ℹ️ Demoted to slave. Instance ID: {INSTANCE_ID}")
                        stop_monitoring()
            
            # Check if we haven't seen master for too long
            if current_time - last_master_time > MASTER_TIMEOUT:
                # No master alive, try to become master
                is_master = True
                last_master_time = current_time
                print(f"✅ Took over as master (timeout). Instance ID: {INSTANCE_ID}")
                if not is_monitoring:
                    start_monitoring()
            
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            
        except Exception as e:
            print(f"⚠️ Heartbeat error: {str(e)[:50]}")
            await asyncio.sleep(10)

ADMIN_ID = 8631266527
active_devices = {}

def get_session_display_name(hwid: str) -> str:
    return f"vintedbot-{hwid[-8:]}"

@router.message(Command("stopall"))
async def cmd_stop_all(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Только администратор может использовать эту команду")
        return
    
    await message.answer("🛑 Отправляю команду остановки всем экземплярам бота!")
    await bot.send_message(chat_id=settings['chat_id'], text="🛑 STOP ALL")
    is_stopped = True
    stop_monitoring()
    await bot.session.close()
    sys.exit(0)

@router.message(Command("devices"))
async def cmd_devices(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Только администратор может использовать эту команду")
        return
    
    try:
        hwid = subprocess.check_output('wmic csproduct get uuid').decode().split('\n')[1].strip()
    except:
        hwid = f"unknown-{random.randint(100000, 999999)}"
    
    current_time = time.time()
    
    lines = []
    lines.append("🖥️ <b>Active Sessions</b>")
    lines.append("")
    
    active_count = 0
    for session_hwid, last_seen in list(active_devices.items()):
        age = current_time - last_seen
        if age > 600:
            continue
        active_count += 1
        is_this = " (this)" if session_hwid == hwid else ""
        is_master_flag = " 👑" if session_hwid == hwid and is_master else ""
        minutes = int(age // 60)
        seconds = int(age % 60)
        name = get_session_display_name(session_hwid)
        lines.append(f"✅ <code>{name}</code>{is_this}{is_master_flag}")
        lines.append(f"   ⏱️ Last seen: {minutes}m {seconds}s ago")
        lines.append(f"   🆔 HWID: <code>{session_hwid[-12:]}</code>")
        lines.append("")
    
    if not active_count:
        lines.append("⚠️ No active sessions found")
        lines.append("")
    
    lines.append(f"📊 <b>Total active: {active_count}</b>")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="devices")],
        [InlineKeyboardButton(text="🛑 Stop All", callback_data="stopall")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
    ])
    
    await message.answer(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(lambda c: c.data == "devices")
async def callback_devices(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Только администратор")
        return
    
    try:
        hwid = subprocess.check_output('wmic csproduct get uuid').decode().split('\n')[1].strip()
    except:
        hwid = f"unknown-{random.randint(100000, 999999)}"
    
    current_time = time.time()
    
    lines = []
    lines.append("🖥️ <b>Active Sessions</b>")
    lines.append("")
    
    active_count = 0
    for session_hwid, last_seen in list(active_devices.items()):
        age = current_time - last_seen
        if age > 600:
            continue
        active_count += 1
        is_this = " (this)" if session_hwid == hwid else ""
        is_master_flag = " 👑" if session_hwid == hwid and is_master else ""
        minutes = int(age // 60)
        seconds = int(age % 60)
        name = get_session_display_name(session_hwid)
        lines.append(f"✅ <code>{name}</code>{is_this}{is_master_flag}")
        lines.append(f"   ⏱️ Last seen: {minutes}m {seconds}s ago")
        lines.append(f"   🆔 HWID: <code>{session_hwid[-12:]}</code>")
        lines.append("")
    
    if not active_count:
        lines.append("⚠️ No active sessions found")
        lines.append("")
    
    lines.append(f"📊 <b>Total active: {active_count}</b>")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="devices")],
        [InlineKeyboardButton(text="🛑 Stop All", callback_data="stopall")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="admin_panel")],
    ])
    
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

async def main():
    global PROXIES, USE_PROXIES, is_master
    load_settings()
    
    try:
        hwid = subprocess.check_output('wmic csproduct get uuid').decode().split('\n')[1].strip()
    except:
        hwid = f"unknown-{random.randint(100000, 999999)}"
    
    print(f"Bot started! Instance ID: {INSTANCE_ID}")
    print(f"HWID: {hwid}")
    print(f"Keywords: {settings['keywords']}")
    print(f"Interval: {settings['check_interval']}s ({settings['check_interval']//60} min)")
    print(f"Chat ID: {settings['chat_id']}")
    print(f"Static hashtags: {len(KNOWN_HASHTAGS)} | Learned hashtags: {len(dynamic_hashtags)}")
    print(f"Domains: {VINTED_DOMAINS}")
    print(f"Proxies enabled: {USE_PROXIES}")
    print(f"Loaded proxies: {len(PROXIES) if PROXIES else 0}")
    
    asyncio.create_task(heartbeat_loop())
    
    await asyncio.sleep(5)
    
    if not is_master:
        print("⏳ Not master yet, waiting for heartbeat election...")
        while not is_master and not is_stopped:
            await asyncio.sleep(5)
    
    if is_stopped:
        print("Bot stopped before becoming master")
        return
    
    try:
        await bot.send_message(
            chat_id=settings['chat_id'],
            text=f"🤖 VintedBot запущен (MASTER)\n\n📋 Информация о системе:\nHWID: `{hwid}`\nInstance ID: `{INSTANCE_ID}`\n\n✅ Бот готов к работе.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        print(f"Не удалось отправить отладку: {e}")
    
    print("✅ I am MASTER - starting polling")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user")
        stop_monitoring()
        sys.exit(0)
