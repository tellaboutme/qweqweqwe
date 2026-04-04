import os

# Try load .env file only if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Load environment variables (works both with .env and Render system env vars)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Keywords to search for
KEYWORDS = ['Swear London']

# Vinted domains to search (only .it and .pl for monitoring)
VINTED_DOMAINS = [
    'www.vinted.it',
    'www.vinted.pl',
]

# Monitoring URLs with specific search parameters (for monitoring loop)
MONITORING_URLS = [
    'https://www.vinted.pl/catalog?search_text=Swear%20London&order=newest_first&page=1',
    'https://www.vinted.pl/catalog?order=newest_first&page=1&brand_ids[]=324572',
    'https://www.vinted.it/catalog?search_text=Swear+London&order=newest_first&page=1',
    'https://www.vinted.it/catalog?order=newest_first&page=1&brand_ids[]=324572',
]

# Vinted search URL template (for manual search with pagination)
VINTED_SEARCH_URL = 'https://{domain}/catalog?search_text={keyword}&order=newest_first'

# Proxies for rotating requests (to avoid IP bans)
# Format: 'ip:port' for HTTP proxies or 'socks5://user:pass@ip:port' for SOCKS5
# Leave empty list to use no proxies
PROXIES = os.environ.get('PROXIES', '').split(',') if os.environ.get('PROXIES') else []

# Enable/disable proxy usage globally
USE_PROXIES = False

# Auto-fetch and test free proxies on startup (set to False to use only manually configured proxies)
AUTO_FETCH_PROXIES = False
PROXY_TEST_TIMEOUT = 30  # Timeout in seconds for testing proxies on startup

# File to store processed item IDs
PROCESSED_ITEMS_FILE = 'processed_items.json'

# Check interval in seconds
CHECK_INTERVAL = 30  # 5 minutes

# Settings file
SETTINGS_FILE = 'settings.json'

# Known hashtags/brands to detect in descriptions (for filtering out irrelevant items)
KNOWN_HASHTAGS = [
    'archive', 'archives', 'ifsixwasnine', 'number (n)ine', 'goa', 'japanese', 'kmrii', 'lgb', '14th', 'addiction',
    'tornado mart', 'vkei', 'civarize', 'in the attic', 'semantic design', 'vintage', 'the old curiosity shop',
    'bpn', 'black peace now', 'flared', 'kleš', 'nenet', 'ne-net', '20471120', 'vivienne westwood', 'fashion',
    'beauty:beast', 'beauty beast', 'demonia', 'swear london', 'alternative', 'new rock', 'cyberdog', 'rave',
    'undercover', 'hysteric glamour', 'if6was9'
]

# Hashtag learning configuration
HASHTAG_STATS_FILE = 'hashtag_stats.json'
HASHTAG_MIN_OCCURRENCES = 3  # Minimum times a word must appear to be considered a hashtag
HASHTAG_MIN_LENGTH = 3  # Minimum word length to consider

# Stop words to exclude from hashtag learning
STOP_WORDS = {
    'size', 'xs', 'xx', 'l', 'm', 's', 'xl', 'xxl', '3xl', '4xl', 'uk', 'eu', 'us',
    'the', 'a', 'an', 'and', 'or', 'of', 'in', 'is', 'it', 'to', 'by', 'for', 'with',
    'on', 'at', 'be', 'as', 'are', 'from', 'have', 'has', 'had', 'do', 'does', 'did',
    'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can', 'wear', 'worn',
    'clothes', 'items', 'brand', 'brands', 'new', 'used', 'item', 'clothing', 'price',
    'color', 'colour', 'white', 'black', 'red', 'blue', 'green', 'yellow', 'pink',
    'number', 'size', 'length', 'width', 'description', 'condition', 'vintage', 'rare',
    'limited', 'edition', 'original', 'authentic', 'genuine', 'real', 'check', 'great',
    'good', 'nice', 'beautiful', 'amazing', 'cool', 'awesome', 'best', 'perfect',
    'london', 'swear', 'extra', 'padding'  # Exclude main keywords and common padding
}

# Shoe-related keywords (items with these should be included)
SHOE_KEYWORDS = {
    'boot', 'boots', 'shoe', 'shoes', 'sneaker', 'sneakers', 'trainer', 'trainers',
    'runner', 'runners', 'oxford', 'loafer', 'loafers', 'pump', 'pumps', 'heel', 'heels',
    'sandal', 'sandals', 'slipper', 'slippers', 'flip-flop', 'flipflop', 'moccasin', 'creeper',
    'creepers', 'platform', 'platforms', 'wedge', 'wedges', 'clog', 'clogs', 'espadrille',
    'mule', 'mules', 'mary jane', 'derby', 'monk', 'ballet', 'flat', 'flats', 'ankle',
    'brogue', 'brogues', 'ballet', 'pointe', 'plimsoll', 'keds', 'converse', 'combat',
    'hiking', 'walking', 'running', 'tennis', 'basketball', 'soccer', 'football',
    'skating', 'roller', 'ski', 'snow', 'winter', 'summer', 'water', 'beach'
}

# Clothing items to exclude (NOT shoes)
CLOTHING_EXCLUDE = {
    'jacket', 'jackets', 'coat', 'coats', 'blazer', 'blazers', 'cardigan', 'cardigans',
    'sweater', 'sweaters', 'jumper', 'jumpers', 'hoodie', 'hoodies', 'sweatshirt', 'sweatshirts',
    'shirt', 'shirts', 'blouse', 'blouses', 't-shirt', 'tshirt', 'tee', 'tees', 'tank', 'tanks',
    'dress', 'dresses', 'skirt', 'skirts', 'pants', 'pant', 'trousers', 'trouser', 'jeans',
    'jean', 'shorts', 'short', 'leggings', 'legging', 'tights', 'tight', 'stocking', 'stockings',
    'underwear', 'bra', 'panties', 'socks', 'sock', 'gloves', 'glove', 'mittens', 'mitten',
    'scarf', 'scarfs', 'tie', 'ties', 'suit', 'suits', 'uniform', 'uniforms', 'vest', 'vests',
    'waistcoat', 'apron', 'aprons', 'robe', 'robes', 'kimono', 'kimono', 'pyjamas', 'pajamas',
    'bodysuit', 'bodysuits', 'swimsuit', 'swimsuits', 'bikini', 'bikinis', 'shorts', 'bermuda'
}