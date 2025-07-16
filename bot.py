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

# --- 2. ЗАВАНТАЖЕННЯ ABI ---
try:
    with open("PancakeRouterABI.json", "r") as f:
        ROUTER_ABI = json.load(f)
    with open("PancakeFactoryABI.json", "r") as f:
        FACTORY_ABI = json.load(f)
except FileNotFoundError as e:
    print(Fore.RED + f"Помилка: Не знайдено ABI файл: {e.filename}")
    exit()

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
# Сума в BNB для першої, автоматичної купівлі.
AMOUNT_TO_BUY_BNB = 0.001
# Сума в BNB для докупки за рішенням AI.
AMOUNT_TO_DOUBLEDOWN_BNB = 0.002 # Може бути більшою за першу.

# --- 6. ЛОГІКА ТОРГІВЛІ ---

def buy_token(token_address, amount_bnb):
    """Виконує купівлю токена на вказану суму BNB."""
    print(Fore.CYAN + f"Спроба купити токен {token_address} на суму {amount_bnb} BNB...")
    try:
        amount_in_wei = w3.to_wei(amount_bnb, 'ether')
        token_checksum = w3.to_checksum_address(token_address)

        tx = router.functions.swapExactETHForTokens(
            0, [WBNB_ADDRESS, token_checksum], w3.to_checksum_address(WALLET_ADDRESS),
            int(time.time()) + 10 * 60
        ).build_transaction({
            'from': w3.to_checksum_address(WALLET_ADDRESS), 'value': amount_in_wei,
            'gas': 300000, 'gasPrice': w3.eth.gas_price,
            'nonce': w3.eth.get_transaction_count(w3.to_checksum_address(WALLET_ADDRESS)),
        })

        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        print(Fore.GREEN + f"Транзакція купівлі відправлена! Хеш: {w3.to_hex(tx_hash)}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        if receipt.status == 1:
            print(Fore.GREEN + f"Купівля на {amount_bnb} BNB успішна!")
            return True
        else:
            print(Fore.RED + "Транзакція купівлі не вдалася.")
            return False
    except Exception:
        print(Fore.RED + f"Помилка під час купівлі {token_address}:")
        traceback.print_exc()
        return False

def get_decision_from_ai(token_address, purchase_price, current_price, total_invested):
    """Запитує у AI, що робити далі."""
    if not llm: return "HOLD"

    profit = ((current_price - purchase_price) / purchase_price) * 100
    
    # ОНОВЛЕНИЙ ПРОМПТ З ТРЬОМА ОПЦІЯМИ
    prompt = (
        f"Ти — автоматизований трейдинг-бот. Твоя задача — дати одну з трьох команд: SELL, HOLD, або BUY_MORE. "
        f"Жодних пояснень, лише команда. "
        f"Токен: {token_address}. "
        f"Середня ціна входу: {purchase_price:.18f} WBNB. "
        f"Поточна ціна: {current_price:.18f} WBNB. "
        f"Поточний прибуток/збиток: {profit:.2f}%. "
        f"Вже інвестовано: {total_invested} BNB. "
        f"Якщо ціна росте і є потенціал, ти можеш дати команду BUY_MORE. "
        f"Якщо ризик високий або прибуток достатній, давай команду SELL. "
        f"В іншому випадку — HOLD. Яка твоя команда?"
    )
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        decision = response.content.strip().upper()
        if "BUY_MORE" in decision: return "BUY_MORE"
        if "SELL" in decision: return "SELL"
        return "HOLD"
    except Exception:
        return "HOLD"

def monitor_and_manage_position(token_address):
    """Моніторить позицію і керує нею за допомогою AI."""
    print(Fore.YELLOW + f"Починаю моніторинг токена {token_address}.")
    
    total_invested_bnb = AMOUNT_TO_BUY_BNB
    has_doubled_down = False # Прапорець, щоб докупити лише один раз

    try:
        amounts_out = router.functions.getAmountsOut(w3.to_wei(1, 'ether'), [w3.to_checksum_address(token_address), WBNB_ADDRESS]).call()
        avg_purchase_price = w3.from_wei(amounts_out[-1], 'ether')
    except Exception:
        print(Fore.RED + "Не вдалося отримати початкову ціну, продаж неможливий.")
        return

    while True:
        time.sleep(45) # Перевіряємо кожні 45 секунд
        try:
            amounts_out = router.functions.getAmountsOut(w3.to_wei(1, 'ether'), [w3.to_checksum_address(token_address), WBNB_ADDRESS]).call()
            current_price = w3.from_wei(amounts_out[-1], 'ether')
            
            print(f"Моніторинг {token_address}: поточна ціна {float(current_price):.18f} WBNB")

            decision = get_decision_from_ai(token_address, float(avg_purchase_price), float(current_price), total_invested_bnb)
            print(Fore.MAGENTA + f"Рішення AI: {decision}")

            if decision == "SELL":
                print(Fore.GREEN + "AI дав команду на продаж! Продаємо...")
                # sell_token(token_address) # Тут буде ваша функція продажу
                break 
            
            elif decision == "BUY_MORE" and not has_doubled_down:
                print(Fore.CYAN + "AI дав команду докупити! Виконую...")
                if buy_token(token_address, AMOUNT_TO_DOUBLEDOWN_BNB):
                    has_doubled_down = True # Встановлюємо прапорець
                    total_invested_bnb += AMOUNT_TO_DOUBLEDOWN_BNB
                    # Тут можна було б перерахувати середню ціну входу, але для спрощення залишаємо початкову
                    print(Fore.CYAN + f"Успішно докуплено. Загальна інвестиція: {total_invested_bnb} BNB.")
                else:
                    print(Fore.RED + "Спроба докупити не вдалася.")

        except Exception:
            print(Fore.YELLOW + f"Не вдалося оновити ціну для {token_address}. Продовжуємо моніторинг.")
            time.sleep(60)

def main():
    """Головний цикл."""
    print("Запуск бота... Очікування нових пар...")
    event_filter = factory.events.PairCreated.create_filter(from_block='latest')

    while True:
        try:
            for event in event_filter.get_new_entries():
                token0, token1 = event['args']['token0'], event['args']['token1']
                token_to_buy = None
                if token0.lower() == WBNB_ADDRESS.lower(): token_to_buy = token1
                elif token1.lower() == WBNB_ADDRESS.lower(): token_to_buy = token0
                
                if token_to_buy:
                    print(Fore.CYAN + f"\nЗнайдено новий токен: {token_to_buy}")
                    if buy_token(token_to_buy, AMOUNT_TO_BUY_BNB):
                        monitor_and_manage_position(token_to_buy)
            
            time.sleep(10)
        except Exception:
            print(Fore.RED + f"Критична помилка в головному циклі:")
            traceback.print_exc()
            time.sleep(60)

if __name__ == "__main__":
    main()
