
#%%
from logging import FATAL
import ccxt 
import pprint
import pytz
from rich import console
from rich.console import Console
from rich.table import Column, Table
import time
from datetime import datetime
import sys
from pytz import timezone
import websocket
import telegram



## -----------------------------------------------------------
## 텔레그램 설정 
## -----------------------------------------------------------

chat_token = ""
chat_id = ""
bot = telegram.Bot(token = chat_token)


## -----------------------------------------------------------
## 바이낸스 API 섧정
## -----------------------------------------------------------

api_key = ""
secret  = ""

binance = ccxt.binance(config={
    'apiKey': api_key, 
    'secret': secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',
    }
})


#%%
## -----------------------------------------------------------
## 주요변수 지정 - 콘솔에서 입력한 파라미터 저장
## -----------------------------------------------------------

FLAG_BAT_CNT = 0 #물타기 플래그

TRADE_SYMBOL = sys.argv[1]
TRADE_SYMBOL_SIMPLE = TRADE_SYMBOL.replace("/", "")
TRADE_AMOUNT = sys.argv[2]
TRADE_SIDE = sys.argv[3]

FLAG_BAT_CNT= int(sys.argv[4])

ROE_RANGE_1 = int(sys.argv[5])
ROE_RANGE_2 = int(sys.argv[6])
ROE_RANGE_3 = int(sys.argv[7])

GET_PORFIT = float(sys.argv[8])

LOSS_CUT = 0.05 #로스컷 비율

SOCKET = f"wss://stream.binance.com:9443/ws/{TRADE_SYMBOL_SIMPLE.lower()}@kline_1m"


#%%
## -----------------------------------------------------------
## 열려있는 주문을 테이블로 그리기
## -----------------------------------------------------------
def make_table(pos_lev, pos_side, pos_entry, pos_amt, pos_pnl, pos_roe):
    positonTable = Table(
        show_header=True, 
        header_style="default #FFFA50", 
        title=f"POSITION : {pos_side}", 
        title_justify="Left", 
        title_style=("#E73138" if pos_side == "SHORT" else ( "#33CE55" if pos_side == "LONG" else "white")))

    positonTable.add_column("Symbol", justify="center")
    positonTable.add_column("Lev.", justify="center")
    positonTable.add_column("Ent Pri", justify="right")
    positonTable.add_column("Size", justify="right")
    positonTable.add_column("PNL(USDT)", justify="right", style=("#E73138" if pos_pnl < 0 else ( "#33CE55" if pos_pnl > 0 else "white")))
    positonTable.add_column("ROE(%)", justify="right", style=("#E73138" if pos_pnl < 0 else ( "#33CE55" if pos_pnl > 0 else "white")))

    positonTable.add_row(
                            f"{TRADE_SYMBOL}",
                            f"{pos_lev}" if pos_amt > 0 else "-",
                            f"{pos_entry}" if pos_amt > 0 else "-",
                            f"{pos_amt}" if pos_amt > 0 else "-",
                            f"{pos_pnl}" if pos_amt > 0 else "-",
                            f"{pos_roe}" if pos_amt > 0 else "-"
                )
    console = Console()
    console.print(positonTable, end="")




#%%
## -----------------------------------------------------------
## 열려있는 대기주문 취소
## -----------------------------------------------------------
def make_cancel_order(TRADE_SIDE):
    open_orders = binance.fetch_open_orders(symbol=TRADE_SYMBOL)

    for order_info in open_orders:
        order = order_info['info']

        if(order['symbol'] == TRADE_SYMBOL_SIMPLE):
            if(order['positionSide'] == TRADE_SIDE):
                id = order['orderId']
                print(f"OPEN ORDER CANCELED")
                print("---------------------------------------------------------------------------")
                binance.cancel_order(id, TRADE_SYMBOL_SIMPLE)

#%%
## -----------------------------------------------------------
## 시장가 주문 함수
## -----------------------------------------------------------

def make_orders(pos_amt):
    global FLAG_BAT_CNT
    
    print(f"MAKE POSITION {TRADE_SIDE}| CURRENT BAT : {FLAG_BAT_CNT}")
    print("---------------------------------------------------------------------------")

    if FLAG_BAT_CNT == 0:
        make_amt = TRADE_AMOUNT

    elif FLAG_BAT_CNT == 1:
        make_cancel_order(TRADE_SIDE)
        make_amt = pos_amt * 2

    elif FLAG_BAT_CNT == 2:
        make_cancel_order(TRADE_SIDE)
        make_amt = pos_amt * 2

    elif FLAG_BAT_CNT == 3:
        make_cancel_order(TRADE_SIDE)
        make_amt = pos_amt * 2



    params = {
        'positionSide': TRADE_SIDE
    }

    try:
        make_order = binance.create_order(
            symbol = TRADE_SYMBOL,
            type = "MARKET",
            side = "BUY" if TRADE_SIDE == "LONG" else "SELL", #포지션에 따른 주문 설정
            amount = make_amt,
            params = params
        )

    except Exception as e:
        print(e) 

    pos_lev, pos_side, pos_amt_a, pos_entry_a, pos_pnl, pos_roe = get_opened_postion()

    print(f"MAKE POSITION COMPLETE : {TRADE_SIDE} | BAT : {FLAG_BAT_CNT}")
    print("---------------------------------------------------------------------------")
    take_profit_order(pos_entry_a, FLAG_BAT_CNT, pos_amt_a)
    FLAG_BAT_CNT += 1

    ### 메신저 알림 보내기
    symbol = make_order['info']['symbol']
    entry_position = make_order['info']['positionSide']

    get_time = int(make_order['info']['updateTime'])/1000
    order_time = datetime.fromtimestamp(get_time, timezone('Asia/Tokyo'))
    entry_time = order_time.strftime("%Y-%m-%d %H:%M:%S (%Z)")

    entry_price = make_order['info']['avgPrice']
    order_qty = make_order['info']['executedQty']
    usdt_qty = make_order['info']['cumQuote']

    text = f"[Position Start]\n----------------------------\n{symbol} / {entry_position} / BAT: {FLAG_BAT_CNT}\n{entry_time} \nPRICE : {entry_price} \nQTY : {order_qty} \nUSDT : {usdt_qty}"

    bot.sendMessage(chat_id = chat_id, text=text)



#%%
## -----------------------------------------------------------
## 익절가 지정 함수
## -----------------------------------------------------------

def take_profit_order(pos_entry, bat_cnt, pos_amt):
    print(f"MAKE Take Profit Position : {TRADE_SIDE}")
    print("---------------------------------------------------------------------------")

    st_price = pos_entry * (1 + GET_PORFIT) if TRADE_SIDE == "LONG" else pos_entry * (1 - GET_PORFIT) #롱포지션이면 진입가보가 높게 / 숏포지션이면 진입가보다 낮게 / 정해진 익절 비율에 따라
    set_side = "SELL"  if TRADE_SIDE == "LONG" else "BUY" #롱포지션은 팔기로 익절 / 숏포지션이라면 사기로 익절

    print(f"ENTRY PRICE : {pos_entry} | StopPrice : {st_price} | SetSide : {set_side}")
    print("---------------------------------------------------------------------------")

    params = {
        'positionSide': TRADE_SIDE,
        'stopPrice': st_price,
        'closePostion': True
    }

    try:
        binance.create_order(
            symbol = TRADE_SYMBOL, 
            type = 'TAKE_PROFIT_MARKET', 
            side = set_side, 
            amount = pos_amt, 
            price=None, 
            params=params
        )

    except Exception as e:
        print(e)

    # 마지막 배팅인 경우에 손절가도 같이 지정
    if FLAG_BAT_CNT == 4:
        print(f"LAST BAT : {FLAG_BAT_CNT} | MAKE LOSS CUT")
        print("---------------------------------------------------------------------------")
        
        st_price = pos_entry * (1 - LOSS_CUT)  if TRADE_SIDE == "LONG" else pos_entry * (1 + LOSS_CUT) #손절가격 설정
        set_side = "SELL" if TRADE_SIDE == "LONG" else "BUY" 

        print(f"ENTRY PRICE : {pos_entry} | StopPrice : {st_price} | SetSide : {set_side}")
        print("---------------------------------------------------------------------------")

        params = {
            'positionSide': TRADE_SIDE,
            'stopPrice': st_price,
            'closePostion': True
        }

        try:
            binance.create_order(
                symbol = TRADE_SYMBOL, 
                type = 'STOP', 
                side = set_side, 
                amount = pos_amt, 
                price=None, 
                params=params
            )

        except Exception as e:
            print(e)
#%%
## -----------------------------------------------------------
## 현재 열려있는 포지션 가져오기
## -----------------------------------------------------------       
def get_opened_postion():
    balance = binance.fetch_balance()
    positions = balance['info']['positions']

    ticker_fetch = binance.fetch_ticker(TRADE_SYMBOL)
    mk_price = round(float(ticker_fetch["close"]),5)

    for position in positions:
        if (position["symbol"] == TRADE_SYMBOL_SIMPLE):
            if(position["positionSide"] == TRADE_SIDE):
                pos_lev = int(position["leverage"])
                pos_side = position["positionSide"]
                pos_amt = abs(float(position["positionAmt"]))
                pos_entry = round(float(position["entryPrice"]),5)
                pos_pnl = round(float(position["unrealizedProfit"]),2)
                pos_roe = ((mk_price/pos_entry-1)*pos_lev*100 if pos_entry > 0 else 0) if TRADE_SIDE == "LONG" else ((1-mk_price/pos_entry)*pos_lev*100 if pos_entry > 0 else 0)
                pos_roe = round(float(pos_roe),2)

    return pos_lev, pos_side, pos_amt, pos_entry, pos_pnl, pos_roe


#%%
## -----------------------------------------------------------
## 현재 열려있는 대기주문 가져오기
## -----------------------------------------------------------
def check_open_order():
    open_orders = binance.fetch_open_orders(symbol=TRADE_SYMBOL)

    for order_info in open_orders:
        order = order_info['info']

        if(order['symbol'] == TRADE_SYMBOL_SIMPLE):
            if(order['positionSide'] == TRADE_SIDE):
                order_side = order['positionSide']
                order_id = order['orderId']
                order_size = order['origQty']
                order_price = order['stopPrice']
                print("OPENED ORDER")
                print(f"BAT: {FLAG_BAT_CNT} | ROE_RAGE: {ROE_RANGE_1}/{ROE_RANGE_2}/{ROE_RANGE_3} | SIDE: {order_side} | Amt: {order_size}")
                print("---------------------------------------------------------------------------")

#%%
## -----------------------------------------------------------
## 포지션별 자동매매
## -----------------------------------------------------------
def auto_tarade():
    global FLAG_BAT_CNT

    pos_lev, pos_side, pos_amt, pos_entry, pos_pnl, pos_roe = get_opened_postion()

    print("")
    make_table(pos_lev, pos_side, pos_entry, pos_amt, pos_pnl, pos_roe)
    check_open_order()

    if pos_amt == 0:
        print("---------------------------------------------------------------------------")
        print("THERE IS NO OPENED ORDERS")
        print(f"MAKE NEW POSIOTION : {TRADE_SIDE}")
        print("---------------------------------------------------------------------------")
        
        FLAG_BAT_CNT= 0
        print(f"BAT COUNT RESET : {FLAG_BAT_CNT}")
        print("---------------------------------------------------------------------------")
        
        make_orders(pos_amt)

    elif pos_amt > 0:
        if(FLAG_BAT_CNT == 1):
            if pos_roe < ROE_RANGE_1:
                print(f"물타기 : ROE:{pos_roe} < {ROE_RANGE_1}")
                print("---------------------------------------------------------------------------")
                time.sleep(5)
                make_orders(pos_amt)

        elif(FLAG_BAT_CNT == 2):
            if pos_roe < ROE_RANGE_2:
                print(f"물타기 : ROE:{pos_roe} < {ROE_RANGE_2}")
                print("---------------------------------------------------------------------------")
                time.sleep(5)
                make_orders(pos_amt)

        elif(FLAG_BAT_CNT == 3):
            if pos_roe < ROE_RANGE_3:
                print(f"물타기 : ROE:{pos_roe} < {ROE_RANGE_3}")
                print("---------------------------------------------------------------------------")
                time.sleep(5)
                make_orders(pos_amt) 


#%%
## -----------------------------------------------------------
## 자동매매 실행
## -----------------------------------------------------------
def on_message(ws, message):
    balance = binance.fetch_balance()
    positions = balance['info']['positions']

    ticker_fetch = binance.fetch_ticker(TRADE_SYMBOL)
    mk_price = round(float(ticker_fetch["close"]),5)

    print("")
    print("")
    print("---------------------------------------------------------------------------")
    today = datetime.now(timezone('Asia/Tokyo'))

    print(today.strftime("%Y/%m/%d %H:%M:%S")) 
    print("---------------------------------------------------------------------------")

    auto_tarade()
    time.sleep(0)


#%%
def on_open(ws):
        print('====================== Opened Connection Ver.2.0 ======================= ')

def on_close(ws):
    print('Closed Connection')

#%%
ws = websocket.WebSocketApp(SOCKET, on_open=on_open, on_close=on_close, on_message=on_message)
ws.run_forever()