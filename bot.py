import os
import json
import time
from web3 import Web3
from dotenv import load_dotenv
from colorama import init, Fore, Style
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage

# Ініціалізація colorama для кольорового виводу в консоль
init(autoreset=True)

# --- 1. ЗАВАНТАЖЕННЯ КОНФІГУРАЦІЇ ---
load_dotenv()

# Завантаження змінних середовища
RPC_URL = os.getenv("RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDR")
ROUTER_ADDR = os.getenv("ROUTER_ADDR")
WBNB_ADDR = os.getenv("WBNB_ADDR")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Перевірка наявності всіх необхідних змінних
if not all([RPC_URL, PRIVATE_KEY, WALLET_ADDRESS, ROUTER_ADDR, WBNB_ADDR, OPENAI_API_KEY]):
    print(Fore.RED + "Помилка: Перевірте наявність всіх змінних (RPC_URL, PRIVATE_KEY, WALLET_ADDR, ROUTER_ADDR, WBNB_ADDR, OPENAI_API_KEY) у ваших налаштуваннях на Railway.")
    exit()

# Підключення до вузла BSC
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if w3.is_connected():
    print(Fore.GREEN + "Успішно підключено до вузла BSC.")
else:
    print(Fore.RED + "Помилка підключення до вузла BSC. Перевірте RPC_URL.")
    exit()

WALLET_CHECKSUM_ADDRESS = w3.to_checksum_address(WALLET_ADDRESS)

# --- 2. ЗАВАНТАЖЕННЯ ABI ---
try:
    with open("PancakeRouterABI.json", "r") as f:
        ROUTER_ABI = json.load(f)
    with open("PancakeFactoryABI.json", "r") as f:
        FACTORY_ABI = json.load(f)
except FileNotFoundError as e:
    print(Fore.RED + f"Помилка: Не знайдено ABI файл: {e.filename}")
    exit()

# Стандартний ABI для токенів BEP-20 (потрібен для approve і balanceOf)
TOKEN_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"}, {"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"payable":false,"stateMutability":"nonpayable","type":"function"}]')

# --- 3. НАЛАШТУВАННЯ КОНТРАКТІВ І АДРЕС ---
ROUTER_ADDRESS = w3.to_checksum_address(ROUTER_ADDR)
WBNB_ADDRESS = w3.to_checksum_address(WBNB_ADDR)

router = w3.eth.contract(address=ROUTER_ADDRESS, abi=ROUTER_ABI)
factory_address = router.functions.factory().call()
factory = w3.eth.contract(address=factory_address, abi=FACTORY_ABI)

# --- 4. НАЛАШТУВАННЯ ШІ ---
llm = ChatOpenAI(openai_api_key=OPENAI_API_KEY, model_name="gpt-4", temperature=0.7)

# --- 5. КОНФІГУРАЦІЯ ТОРГІВЛІ ---
AMOUNT_TO_BUY_BNB = 0.001  # Скільки BNB витрачати на одну покупку
GAS_LIMIT = 300000        # Ліміт газу для транзакцій
GAS_PRICE_GWEI = 5        # Ціна газу в Gwei

# Словник для зберігання інформації про куплені токени
purchased_tokens = {}

# --- 6. ОСНОВНІ ФУНКЦІЇ БОТА ---

def get_token_price_in_bnb(token_address):
    """Отримує ціну токена в BNB."""
    try:
        amounts_out = router.functions.getAmountsOut(
            w3.to_wei(1, 'ether'),  # 1 токен
            [token_address, WBNB_ADDRESS]
        ).call()
        return w3.from_wei(amounts_out[1], 'ether')
    except Exception:
        # Якщо пряму пару не знайдено, спробуємо через USDT (поширений міст)
        try:
            usdt_address = w3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
            amounts_out_1 = router.functions.getAmountsOut(w3.to_wei(1, 'ether'), [token_address, usdt_address]).call()
            amounts_out_2 = router.functions.getAmountsOut(amounts_out_1[1], [usdt_address, WBNB_ADDRESS]).call()
            return w3.from_wei(amounts_out_2[1], 'ether')
        except Exception:
            return None

def buy_token(token_address):
    """Функція для купівлі токена."""
    print(Fore.CYAN + f"Спроба купити токен {token_address} на суму {AMOUNT_TO_BUY_BNB} BNB...")
    try:
        tx = router.functions.swapExactETHForTokens(
            0,  # amountOutMin
            [WBNB_ADDRESS, token_address],
            WALLET_CHECKSUM_ADDRESS,
            int(time.time()) + 10 * 60  # Дедлайн 10 хвилин
        ).build_transaction({
            'from': WALLET_CHECKSUM_ADDRESS,
            'value': w3.to_wei(AMOUNT_TO_BUY_BNB, 'ether'),
            'gas': GAS_LIMIT,
            'gasPrice': w3.to_wei(GAS_PRICE_GWEI, 'gwei'),
            'nonce': w3.eth.get_transaction_count(WALLET_CHECKSUM_ADDRESS),
        })

        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw)
        print(Fore.YELLOW + f"Транзакція відправлена, хеш: {w3.to_hex(tx_hash)}")
        
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt.status == 1:
            print(Fore.GREEN + f"Купівля на {AMOUNT_TO_BUY_BNB} BNB успішна!")
            return True
        else:
            print(Fore.RED + "Транзакція купівлі не вдалася.")
            return False
    except Exception as e:
        print(Fore.RED + f"Помилка під час купівлі: {e}")
        return False

def sell_token(token_address):
    """Функція для продажу токена."""
    print(Fore.CYAN + f"Спроба продати токен {token_address}...")
    try:
        token_contract = w3.eth.contract(address=token_address, abi=TOKEN_ABI)
        balance = token_contract.functions.balanceOf(WALLET_CHECKSUM_ADDRESS).call()

        if balance == 0:
            print(Fore.YELLOW + "Баланс токена нульовий, нічого продавати.")
            return True # Вважаємо, що продаж успішний, бо токена вже немає

        # 1. Схвалення (Approve)
        approve_tx = token_contract.functions.approve(ROUTER_ADDRESS, balance).build_transaction({
            'from': WALLET_CHECKSUM_ADDRESS,
            'gas': GAS_LIMIT,
            'gasPrice': w3.to_wei(GAS_PRICE_GWEI, 'gwei'),
            'nonce': w3.eth.get_transaction_count(WALLET_CHECKSUM_ADDRESS),
        })
        signed_approve_tx = w3.eth.account.sign_transaction(approve_tx, private_key=PRIVATE_KEY)
        approve_tx_hash = w3.eth.send_raw_transaction(signed_approve_tx.raw)
        print(Fore.YELLOW + f"Транзакція схвалення відправлена, хеш: {w3.to_hex(approve_tx_hash)}")
        w3.eth.wait_for_transaction_receipt(approve_tx_hash, timeout=300)
        print(Fore.GREEN + "Токен успішно схвалено для продажу.")

        # 2. Продаж (Swap)
        sell_tx = router.functions.swapExactTokensForETH(
            balance,
            0, # amountOutMin
            [token_address, WBNB_ADDRESS],
            WALLET_CHECKSUM_ADDRESS,
            int(time.time()) + 10 * 60
        ).build_transaction({
            'from': WALLET_CHECKSUM_ADDRESS,
            'gas': GAS_LIMIT,
            'gasPrice': w3.to_wei(GAS_PRICE_GWEI, 'gwei'),
            'nonce': w3.eth.get_transaction_count(WALLET_CHECKSUM_ADDRESS),
        })
        signed_sell_tx = w3.eth.account.sign_transaction(sell_tx, private_key=PRIVATE_KEY)
        sell_tx_hash = w3.eth.send_raw_transaction(signed_sell_tx.raw)
        print(Fore.YELLOW + f"Транзакція продажу відправлена, хеш: {w3.to_hex(sell_tx_hash)}")
        
        receipt = w3.eth.wait_for_transaction_receipt(sell_tx_hash, timeout=300)
        if receipt.status == 1:
            print(Fore.GREEN + "Продаж токена успішний!")
            return True
        else:
            print(Fore.RED + "Транзакція продажу не вдалася.")
            return False

    except Exception as e:
        print(Fore.RED + f"Помилка під час продажу: {e}")
        return False

def analyze_and_decide(token_address, purchase_price_bnb):
    """Аналізує ринкову ситуацію і приймає рішення."""
    print(Fore.BLUE + f"Аналіз токена {token_address}...")
    
    # Тут можна додати логіку збору даних про транзакції, але поки що спростимо
    buys = 10 # Припустимо
    sells = 5  # Припустимо
    total_volume_bnb = 1.5 # Припустимо
    
    current_price_bnb_decimal = get_token_price_in_bnb(token_address)
    if current_price_bnb_decimal is None:
        print(Fore.YELLOW + "Не вдалося отримати ціну токена для аналізу.")
        return "ТРИМАТИ"
        
    current_price_bnb = float(current_price_bnb_decimal)
    purchase_price_bnb = float(purchase_price_bnb)
    
    profit_loss_percent = ((current_price_bnb / purchase_price_bnb) - 1) * 100

    # Формуємо розширений промпт для ШІ
    prompt = f"""
Ти — просунутий аналітик для торгового бота на PancakeSwap.
Твоя задача — на основі наданих даних про транзакції токена дати чітку команду: КУПИТИ, ПРОДАТИ або ТРИМАТИ.

Ось дані про токен:
- Адреса токена: {token_address}
- Поточна ціна (в BNB): {current_price_bnb} BNB
- Ціна нашої першої покупки: {purchase_price_bnb} BNB
- Поточний прибуток/збиток: {profit_loss_percent:.2f}%
- Кількість купівель за останні 5 хвилин: {buys}
- Кількість продажів за останні 5 хвилин: {sells}
- Загальний обсяг торгів (в BNB) за 5 хвилин: {total_volume_bnb} BNB

Твоя логіка для прийняття рішень:
1.  **ПРОДАТИ:**
    - Якщо поточний прибуток перевищує 25% (наприклад, +30%, +50%, +100%). Це фіксація прибутку.
    - Якщо кількість продажів значно перевищує кількість купівель (наприклад, 20 продажів і 3 покупки). Це ознака паніки.
    - Якщо ціна різко впала нижче ціни покупки (наприклад, збиток -30% і більше). Це стоп-лосс.

2.  **КУПИТИ:**
    - Якщо токен показує стабільний ріст (наприклад, прибуток +5% до +15%).
    - Якщо кількість купівель значно перевищує кількість продажів, а ціна зростає. Це ознака позитивного тренду.
    - Якщо ти вважаєш, що можна докупити невелику частину, щоб усереднити позицію або посилити її.

3.  **ТРИМАТИ:**
    - Якщо ситуація невизначена (приблизно однакова кількість купівель і продажів).
    - Якщо прибуток/збиток незначний (від -5% до +5%).
    - Якщо обсяги торгів дуже низькі.

Проаналізуй надані цифри і дай ОДНУ з трьох команд: КУПИТИ, ПРОДАТИ, ТРИМАТИ. Без додаткових пояснень. Просто одне слово.
"""
    try:
        message = HumanMessage(content=prompt)
        response = llm.invoke([message])
        decision = response.content.strip().upper()
        print(Fore.MAGENTA + f"Рішення ШІ: {decision} (Прибуток: {profit_loss_percent:.2f}%)")
        return decision
    except Exception as e:
        print(Fore.RED + f"Помилка під час звернення до OpenAI: {e}")
        return "ТРИМАТИ" # Якщо ШІ не відповів, краще нічого не робити

def monitor_token(token_address):
    """Слідкує за купленим токеном і приймає рішення."""
    purchase_info = purchased_tokens.get(token_address)
    if not purchase_info:
        return

    purchase_price = purchase_info['price']
    
    while True:
        decision = analyze_and_decide(token_address, purchase_price)
        
        if decision == "ПРОДАТИ":
            if sell_token(token_address):
                del purchased_tokens[token_address] # Видаляємо токен після успішного продажу
                break # Виходимо з циклу моніторингу
        elif decision == "КУПИТИ":
            buy_token(token_address) # Докупляємо ще
        # Якщо рішення "ТРИМАТИ", просто продовжуємо цикл
        
        time.sleep(45) # Пауза між перевірками

def handle_event(event):
    """Обробляє подію створення нової пари."""
    token0 = event['args']['token0']
    token1 = event['args']['token1']
    
    new_token_address = None
    if token0 == WBNB_ADDRESS:
        new_token_address = token1
    elif token1 == WBNB_ADDRESS:
        new_token_address = token0

    if new_token_address and new_token_address not in purchased_tokens:
        print(Style.BRIGHT + Fore.WHITE + f"\nЗнайдено новий токен: {new_token_address}")
        
        price = get_token_price_in_bnb(new_token_address)
        if price is None:
            print(Fore.YELLOW + "Не вдалося отримати ціну, ігноруємо токен.")
            return

        if buy_token(new_token_address):
            purchased_tokens[new_token_address] = {'price': float(price)}
            print(Fore.CYAN + f"Починаю моніторинг токена {new_token_address}...")
            monitor_token(new_token_address) # Запускаємо моніторинг в основному потоці

def main():
    """Головна функція, яка запускає бота."""
    print(Fore.YELLOW + "Запуск бота... Очікування нових пар на PancakeSwap...")
    event_filter = factory.events.PairCreated.create_filter(from_block='latest')

    
    while True:
        try:
            for event in event_filter.get_new_entries():
                handle_event(event)
            time.sleep(2)
        except Exception as e:
            print(Fore.RED + f"Сталася помилка в головному циклі: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
