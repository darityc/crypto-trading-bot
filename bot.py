import os
import json
import time
import traceback
from web3 import Web3
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage
from colorama import Fore, init

# Ініціалізація
init(autoreset=True)
load_dotenv()

# --- 1. НАЛАШТУВАННЯ WEB3 ---
RPC_URL = os.getenv("RPC")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDR")

if not all([RPC_URL, PRIVATE_KEY, WALLET_ADDRESS]):
    print(Fore.RED + "Помилка: Перевірте наявність RPC, PRIVATE_KEY, WALLET_ADDR в .env файлі.")
    exit()

w3 = Web3(Web3.HTTPProvider(RPC_URL))
print(Fore.GREEN + "Успішно підключено до вузла BSC.")
WALLET_CHECKSUM_ADDRESS = w3.to_checksum_address(WALLET_ADDRESS)

# --- 2. ЗАВАНТАЖЕННЯ ABI ---
try:
    with open("PancakeRouterABI.json", "r") as f:
        ROUTER_ABI = json.load(f)

    with open("PancakeFactoryABI.json", "r") as f:
        FACTORY_ABI = json.load(f)
        
# --- 3. НАЛАШТУВАННЯ КОНТРАКТІВ І АДРЕС ---
ROUTER_ADDR = os.getenv("ROUTER_ADDR")
WBNB_ADDR = os.getenv("WBNB_ADDR")
if not all([ROUTER_ADDR, WBNB_ADDR]):
    print(Fore.RED + "Помилка: Перевірте ROUTER_ADDR, WBNB_ADDR в .env файлі.")
    exit()

ROUTER_ADDRESS = w3.to_checksum_address(ROUTER_ADDR)
WBNB_ADDRESS = w3.to_checksum_address(WBNB_ADDR)
router = w3.eth.contract(address=ROUTER_ADDRESS, abi=ROUTER_ABI)
factory_address = router.functions.factory().call()
factory = w3.eth.contract(address=factory_address, abi=FACTORY_ABI)

# --- 4. НАЛАШТУВАННЯ AI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
llm = ChatOpenAI(openai_api_key=OPENAI_API_KEY, model_name="gpt-4", temperature=0.7) if OPENAI_API_KEY else None

# --- 5. КОНФІГУРАЦІЯ ТОРГІВЛІ ---
AMOUNT_TO_BUY_BNB = 0.001
AMOUNT_TO_DOUBLEDOWN_BNB = 0.002

# --- 6. ЛОГІКА ТОРГІВЛІ ---

def send_transaction(tx):
    """Підписує та відправляє транзакцію."""
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
    print(Fore.YELLOW + f"Транзакція відправлена, хеш: {w3.to_hex(tx_hash)}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    return receipt.status == 1

def buy_token(token_address, amount_bnb):
    # ... (код цієї функції залишається без змін)
    print(Fore.CYAN + f"Спроба купити токен {token_address} на суму {amount_bnb} BNB...")
    try:
        amount_in_wei = w3.to_wei(amount_bnb, 'ether')
        token_checksum = w3.to_checksum_address(token_address)

        tx = router.functions.swapExactETHForTokens(
            0, [WBNB_ADDRESS, token_checksum], WALLET_CHECKSUM_ADDRESS,
            int(time.time()) + 10 * 60
        ).build_transaction({
            'from': WALLET_CHECKSUM_ADDRESS, 'value': amount_in_wei,
            'gas': 300000, 'gasPrice': w3.eth.gas_price,
            'nonce': w3.eth.get_transaction_count(WALLET_CHECKSUM_ADDRESS),
        })
        
        if send_transaction(tx):
            print(Fore.GREEN + f"Купівля на {amount_bnb} BNB успішна!")
            return True
        else:
            print(Fore.RED + "Транзакція купівлі не вдалася.")
            return False
    except Exception:
        print(Fore.RED + f"Помилка під час купівлі {token_address}:")
        traceback.print_exc()
        return False

# НОВА ФУНКЦІЯ ПРОДАЖУ
def sell_token(token_address):
    """Продає всі токени вказаної адреси."""
    print(Fore.CYAN + f"Спроба продати токен {token_address}...")
    token_checksum = w3.to_checksum_address(token_address)
    token_contract = w3.eth.contract(address=token_checksum, abi=TOKEN_ABI)

    try:
        # 1. Перевіряємо баланс токенів
        balance = token_contract.functions.balanceOf(WALLET_CHECKSUM_ADDRESS).call()
        if balance == 0:
            print(Fore.YELLOW + "Баланс токенів 0. Нічого продавати.")
            return False
        
        print(f"Баланс: {w3.from_wei(balance, 'ether')} токенів.")

        # 2. Схвалення (Approve)
        print("Крок 1: Схвалення (Approve) токенів для PancakeSwap...")
        approve_tx = token_contract.functions.approve(
            ROUTER_ADDRESS,
            balance # Схвалюємо весь баланс
        ).build_transaction({
            'from': WALLET_CHECKSUM_ADDRESS,
            'gas': 100000,
            'gasPrice': w3.eth.gas_price,
            'nonce': w3.eth.get_transaction_count(WALLET_CHECKSUM_ADDRESS),
        })

        if not send_transaction(approve_tx):
            print(Fore.RED + "Транзакція схвалення не вдалася.")
            return False
        
        print(Fore.GREEN + "Схвалення пройшло успішно!")
        time.sleep(10) # Даємо невелику паузу між схваленням і продажем

        # 3. Обмін (Swap)
        print("Крок 2: Обмін (Swap) токенів на BNB...")
        swap_tx = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
            balance, # Кількість токенів для продажу
            0, # Мінімальна кількість BNB, яку ми хочемо отримати (0 = будь-яка)
            [token_checksum, WBNB_ADDRESS], # Шлях обміну
            WALLET_CHECKSUM_ADDRESS, # Адреса, куди прийдуть BNB
            int(time.time()) + 10 * 60 # Дедлайн
        ).build_transaction({
            'from': WALLET_CHECKSUM_ADDRESS,
            'gas': 300000,
            'gasPrice': w3.eth.gas_price,
            'nonce': w3.eth.get_transaction_count(WALLET_CHECKSUM_ADDRESS),
        })

        if send_transaction(swap_tx):
            print(Fore.GREEN + "Продаж успішний! Токени обміняно на BNB.")
            return True
        else:
            print(Fore.RED + "Транзакція продажу не вдалася.")
            return False

    except Exception:
        print(Fore.RED + f"Помилка під час продажу {token_address}:")
        traceback.print_exc()
        return False


def get_decision_from_ai(token_address, purchase_price, current_price, total_invested):
    # ... (код цієї функції залишається без змін)
    if not llm: return "HOLD"
    profit = ((current_price - purchase_price) / purchase_price) * 100
    prompt = (
        f"Ти — автоматизований трейдинг-бот. Твоя задача — дати одну з трьох команд: SELL, HOLD, або BUY_MORE. "
        f"Жодних пояснень, лише команда. "
        f"Токен: {token_address}. "
        f"Середня ціна входу: {purchase_price:.18f} WBNB. "
        f"Поточна ціна: {current_price:.18f} WBNB. "
        f"Поточний прибуток/збиток: {profit:.2f}%. "
        f"Вже інвестовано: {total_invested} BNB. "
        f"Якщо ціна росте і є потенціал, ти можеш дати команду BUY_MORE. "
        f"Якщо ризик високий або прибуток достатній, давай к
