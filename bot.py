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
WALLET_ADDR = os.getenv("WALLET_ADDR")
ROUTER_ADDR = os.getenv("ROUTER_ADDR")
WBNB_ADDR = os.getenv("WBNB_ADDR")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Перевірка наявності всіх змінних
if not all([RPC_URL, PRIVATE_KEY, WALLET_ADDR, ROUTER_ADDR, WBNB_ADDR, OPENAI_API_KEY]):
    print(Fore.RED + "Помилка: Перевірте наявність всіх змінних (RPC_URL, PRIVATE_KEY, WALLET_ADDR, ROUTER_ADDR, WBNB_ADDR, OPENAI_API_KEY) у ваших налаштуваннях на Railway.")
    exit()

# --- 2. ПІДКЛЮЧЕННЯ ДО БЛОКЧЕЙНУ ---
w3 = Web3(Web3.HTTPProvider(RPC_URL))
print(Fore.GREEN + "Успішно підключено до вузла BSC.")
WALLET_CHECKSUM_ADDRESS = w3.to_checksum_address(WALLET_ADDR)

# --- 3. ЗАВАНТАЖЕННЯ ABI ---
try:
    with open("PancakeRouterABI.json", "r") as f:
        ROUTER_ABI = json.load(f)
    with open("PancakeFactoryABI.json", "r") as f:
        FACTORY_ABI = json.load(f)
except FileNotFoundError as e:
    print(Fore.RED + f"Помилка: Не знайдено ABI файл: {e.filename}")
    exit()

# Мінімальний ABI для approve і balanceOf
TOKEN_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"}, {"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"payable":false,"stateMutability":"nonpayable","type":"function"}]')

# --- 4. НАЛАШТУВАННЯ КОНТРАКТІВ І АДРЕС ---
ROUTER_ADDRESS = w3.to_checksum_address(ROUTER_ADDR)
WBNB_ADDRESS = w3.to_checksum_address(WBNB_ADDR)
router = w3.eth.contract(address=ROUTER_ADDRESS, abi=ROUTER_ABI)
factory_address = router.functions.factory().call()
factory = w3.eth.contract(address=factory_address, abi=FACTORY_ABI)

# --- 5. НАЛАШТУВАННЯ AI ---
llm = ChatOpenAI(openai_api_key=OPENAI_API_KEY, model_name="gpt-4", temperature=0.7)

# --- 6. КОНФІГУРАЦІЯ ТОРГІВЛІ ---
AMOUNT_TO_BUY_BNB = 0.001  # Сума BNB для купівлі
PROFIT_MARGIN_PERCENT = 200 # % прибутку для продажу (200% = x3)
STOP_LOSS_PERCENT = 50      # % збитку для продажу

# Словник для відстеження куплених токенів та їх початкової ціни
bought_tokens = {}

# --- 7. ОСНОВНІ ФУНКЦІЇ ---

def get_token_price_in_bnb(token_address):
    """Отримує ціну токена в BNB."""
    try:
        amount_out = router.functions.getAmountsOut(
            w3.to_wei(1, 'ether'),  # 1 токен
            [token_address, WBNB_ADDRESS]
        ).call()
        return w3.from_wei(amount_out[1], 'ether')
    except Exception:
        return None

def buy_token(token_address, amount_in_bnb):
    """Купує токен за вказану кількість BNB."""
    print(Fore.CYAN + f"Спроба купити токен {token_address} на суму {amount_in_bnb} BNB...")
    try:
        amount_in_wei = w3.to_wei(amount_in_bnb, 'ether')
        
        tx = router.functions.swapExactETHForTokens(
            0,  # amountOutMin
            [WBNB_ADDRESS, token_address],
            WALLET_CHECKSUM_ADDRESS,
            int(time.time()) + 10 * 60  # deadline
        ).build_transaction({
            'from': WALLET_CHECKSUM_ADDRESS,
            'value': amount_in_wei,
            'gas': 300000,
            'gasPrice': w3.to_wei('5', 'gwei'),
            'nonce': w3.eth.get_transaction_count(WALLET_CHECKSUM_ADDRESS),
        })

        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        
        print(Fore.YELLOW + f"Транзакція купівлі відправлена, хеш: {w3.to_hex(tx_hash)}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=600)
        
        if receipt.status == 1:
            print(Fore.GREEN + f"Купівля {token_address} успішна!")
            return True
        else:
            print(Fore.RED + f"Транзакція купівлі не вдалася. Статус: {receipt.status}")
            return False
    except Exception as e:
        print(Fore.RED + f"Помилка під час купівлі: {e}")
        return False

def sell_token(token_address, amount_to_sell_wei):
    """Продає вказану кількість токена."""
    print(Fore.CYAN + f"Спроба продати токен {token_address}...")
    try:
        token_contract = w3.eth.contract(address=token_address, abi=TOKEN_ABI)
        
        # 1. Схвалення (Approve)
        approve_tx = token_contract.functions.approve(
            ROUTER_ADDRESS,
            amount_to_sell_wei
        ).build_transaction({
            'from': WALLET_CHECKSUM_ADDRESS,
            'gas': 100000,
            'gasPrice': w3.to_wei('5', 'gwei'),
            'nonce': w3.eth.get_transaction_count(WALLET_CHECKSUM_ADDRESS),
        })
        signed_approve_tx = w3.eth.account.sign_transaction(approve_tx, private_key=PRIVATE_KEY)
        approve_tx_hash = w3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)
        print(Fore.YELLOW + f"Транзакція схвалення відправлена, хеш: {w3.to_hex(approve_tx_hash)}")
        w3.eth.wait_for_transaction_receipt(approve_tx_hash, timeout=600)
        print(Fore.GREEN + "Схвалення успішне.")

        # 2. Продаж (Swap)
        sell_tx = router.functions.swapExactTokensForETH(
            amount_to_sell_wei,
            0, # amountOutMin
            [token_address, WBNB_ADDRESS],
            WALLET_CHECKSUM_ADDRESS,
            int(time.time()) + 10 * 60
        ).build_transaction({
            'from': WALLET_CHECKSUM_ADDRESS,
            'gas': 300000,
            'gasPrice': w3.to_wei('5', 'gwei'),
            'nonce': w3.eth.get_transaction_count(WALLET_CHECKSUM_ADDRESS),
        })
        signed_sell_tx = w3.eth.account.sign_transaction(sell_tx, private_key=PRIVATE_KEY)
        sell_tx_hash = w3.eth.send_raw_transaction(signed_sell_tx.rawTransaction)
        print(Fore.YELLOW + f"Транзакція продажу відправлена, хеш: {w3.to_hex(sell_tx_hash)}")
        receipt = w3.eth.wait_for_transaction_receipt(sell_tx_hash, timeout=600)

        if receipt.status == 1:
            print(Fore.GREEN + f"Продаж {token_address} успішний!")
            return True
        else:
            print(Fore.RED + f"Транзакція продажу не вдалася. Статус: {receipt.status}")
            return False
    except Exception as e:
        print(Fore.RED + f"Помилка під час продажу: {e}")
        return False

def get_ai_decision(token_address, current_price, initial_price):
    """Запитує в AI, що робити з токеном."""
    profit_percentage = ((current_price - initial_price) / initial_price) * 100
    
    prompt = f"""
    Ти — провідний аналітик з ризиків для автоматизованого крипто-трейдингового бота.
    Я щойно купив токен {token_address} за ціною {initial_price:.18f} BNB.
    Поточна ціна: {current_price:.18f} BNB.
    Мій поточний прибуток/збиток: {profit_percentage:.2f}%.

    Мої стандартні правила:
    - Продавати, якщо прибуток досягає {PROFIT_MARGIN_PERCENT}%.
    - Продавати (стоп-лосс), якщо збиток досягає {STOP_LOSS_PERCENT}%.
    - Докуповувати, якщо є сильні позитивні сигнали, а ціна ще не злетіла.

    Проаналізуй ситуацію. Чи є якісь новини про цей токен? Чи виглядає він як скам? Чи є потенціал для подальшого росту?
    На основі твого аналізу, дай мені одну з трьох команд: SELL, HOLD, або BUY_MORE.
    Не давай жодних пояснень, тільки одну команду.
    """
    
    try:
        message = HumanMessage(content=prompt)
        response = llm.invoke([message])
        decision = response.content.upper().strip()
        print(Fore.MAGENTA + f"Рішення AI для {token_address}: {decision} (Прибуток: {profit_percentage:.2f}%)")
        return decision
    except Exception as e:
        print(Fore.RED + f"Помилка при отриманні рішення від AI: {e}")
        return "HOLD" # За замовчуванням тримаємо, якщо AI не відповідає

def monitor_and_manage_position(token_address):
    """Стежить за купленим токеном і приймає рішення про продаж/докупівлю."""
    initial_price_bnb = get_token_price_in_bnb(token_address)
    if not initial_price_bnb:
        print(Fore.RED + f"Не вдалося отримати початкову ціну для {token_address}. Неможливо керувати позицією.")
        return

    bought_tokens[token_address] = {'initial_price': initial_price_bnb}
    print(Fore.CYAN + f"Починаю моніторинг {token_address} з початковою ціною {initial_price_bnb:.18f} BNB.")

    while True:
        time.sleep(60) # Перевіряти кожну хвилину
        
        current_price_bnb = get_token_price_in_bnb(token_address)
        if not current_price_bnb:
            print(Fore.YELLOW + f"Не вдалося отримати поточну ціну для {token_address}. Пропускаю перевірку.")
            continue

        initial_price = bought_tokens[token_address]['initial_price']
        
        # Логіка прийняття рішень
        decision = get_ai_decision(token_address, current_price_bnb, initial_price)

        if decision == "SELL":
            token_contract = w3.eth.contract(address=token_address, abi=TOKEN_ABI)
            balance_wei = token_contract.functions.balanceOf(WALLET_CHECKSUM_ADDRESS).call()
            if balance_wei > 0:
                if sell_token(token_address, balance_wei):
                    del bought_tokens[token_address]
                    break # Вийти з циклу моніторингу
            else:
                print(Fore.YELLOW + "Баланс токена нульовий, нічого продавати.")
                del bought_tokens[token_address]
                break

        elif decision == "BUY_MORE":
            # Можна додати логіку, щоб не докуповувати нескінченно
            buy_token(token_address, AMOUNT_TO_BUY_BNB)
            # Оновлюємо середню ціну входу (спрощена логіка)
            new_price = get_token_price_in_bnb(token_address)
            if new_price:
                bought_tokens[token_address]['initial_price'] = (initial_price + new_price) / 2

        # elif decision == "HOLD":
            # Нічого не робимо, продовжуємо моніторинг
            # print(f"AI радить тримати {token_address}. Наступна перевірка через хвилину.")

def handle_event(event):
    """Обробляє подію створення нової пари."""
    token_address_str = event['args']['pair']
    print(Fore.WHITE + Style.BRIGHT + f"\nЗнайдено нову пару: {token_address_str}")
    
    # Перевіряємо, чи це токен, а не, наприклад, LP-токен іншої пари
    # Проста перевірка: чи можна отримати його ціну відносно WBNB
    price = get_token_price_in_bnb(token_address_str)
    if price is None:
        print(Fore.YELLOW + "Не вдалося отримати ціну, можливо, це не токен або недостатня ліквідність. Ігноруємо.")
        return

    if buy_token(token_address_str, AMOUNT_TO_BUY_BNB):
        # Запускаємо моніторинг у фоновому режимі (для простоти - послідовно)
        monitor_and_manage_position(token_address_str)

def main():
    """Головна функція, яка запускає бота."""
    print(Fore.GREEN + "Запуск бота... Очікування нових пар на PancakeSwap...")
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
