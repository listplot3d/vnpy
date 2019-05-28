# encoding: UTF-8
"""
"""

import hashlib
import hmac
import sys
import time
from copy import copy
from datetime import datetime, timedelta
from threading import Lock
from urllib.parse import urlencode

from requests import ConnectionError

from vnpy.api.rest import Request, RestClient
from vnpy.api.websocket import WebsocketClient
from vnpy.trader.constant import (
    Direction,
    Exchange,
    OrderType,
    Product,
    Status,
    Offset,
    Interval
)
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    TickData,
    OrderData,
    TradeData,
    PositionData,
    AccountData,
    ContractData,
    BarData,
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
    HistoryRequest
)

import json
import tushare as ts

# REST_HOST = 'https://www.JYT.com'
# WEBSOCKET_HOST = "wss://www.bitmex.com/realtime"
#
# TESTNET_REST_HOST = "https://testnet.bitmex.com/api/v1"
# TESTNET_WEBSOCKET_HOST = "wss://testnet.bitmex.com/realtime"
#
# STATUS_BITMEX2VT = {
#     "New": Status.NOTTRADED,
#     "Partially filled": Status.PARTTRADED,
#     "Filled": Status.ALLTRADED,
#     "Canceled": Status.CANCELLED,
#     "Rejected": Status.REJECTED,
# }
#
# DIRECTION_VT2BITMEX = {Direction.LONG: "Buy", Direction.SHORT: "Sell"}
# DIRECTION_BITMEX2VT = {v: k for k, v in DIRECTION_VT2BITMEX.items()}
#
# ORDERTYPE_VT2BITMEX = {
#     OrderType.LIMIT: "Limit",
#     OrderType.MARKET: "Market",
#     OrderType.STOP: "Stop"
# }
# ORDERTYPE_BITMEX2VT = {v: k for k, v in ORDERTYPE_VT2BITMEX.items()}
#
# INTERVAL_VT2BITMEX = {
#     Interval.MINUTE: "1m",
#     Interval.HOUR: "1h",
#     Interval.DAILY: "1d",
# }
#
# TIMEDELTA_MAP = {
#     Interval.MINUTE: timedelta(minutes=1),
#     Interval.HOUR: timedelta(hours=1),
#     Interval.DAILY: timedelta(days=1),
# }



class JYTGateway(BaseGateway):
    """
    VN Trader Gateway for JYT connection.
    """

    # default_setting = {
    #     "ID": "",
    #     "Secret": "",
    #     "会话数": 3,
    #     "服务器": ["REAL", "TESTNET"],
    #     "代理地址": "",
    #     "代理端口": "",
    # }

    default_setting = {
        "TRADE_API_IP_PORT": "<ipaddr>:888", #交易通服务器地址

        "BROKER_ID": "55", #交易通中的券商ID 55,华泰证券
        "BROKER_SERVER_IP": "61.132.54.83", #券商IP
        "BROKER_SERVER_PORT": "7718", #券商IP Port
        "BROKER_ACCNT": "", #券商客户号
        "BROKER_IS_CREDIT_ACCNT": "0", #是否有融资融券功能
        "BROKER_ACCNT_PSWD": "",  #券商登录密码
        "BROKER_COMM_PSWD": "",    #券商通讯密码

        #用Tushare获取行情数据,下面是tushare Pro的token
        "TUSHARE_TOKEN": ""
    }
    #actual setting is here: ~/.vntrader/connect_jyt.json

    exchanges = [Exchange.SSE, Exchange.SZSE]

    def __init__(self, event_engine):
        """Constructor"""
        super(JYTGateway, self).__init__(event_engine, "JYT")

        self.ws_api = JYTWebsocketApi(self)
        self.ts = None #tushare的实例

    def connect(self, setting: dict):
        """"""
        try:
            self.TRADE_API_IP_PORT = str(setting['TRADE_API_IP_PORT'])

            self.BROKER_ID = str(setting['BROKER_ID'])
            self.BROKER_SERVER_IP = str(setting['BROKER_SERVER_IP'])
            self.BROKER_SERVER_PORT = str(setting['BROKER_SERVER_PORT'])
            self.BROKER_ACCNT = str(setting['BROKER_ACCNT'])
            self.BROKER_IS_CREDIT_ACCNT = str(setting['BROKER_IS_CREDIT_ACCNT'])
            self.BROKER_ACCNT_PSWD = str(setting['BROKER_ACCNT_PSWD'])
            self.BROKER_COMM_PSWD = str(setting['BROKER_COMM_PSWD'])

            self.TUSHARE_TOKEN = str(setting['TUSHARE_TOKEN'])
        except KeyError:
            self.gateway.write_log("连接配置缺少字段，请检查")

        self.ws_api.connect()


    def subscribe(self, req: SubscribeRequest):
        """"""
        self.ws_api.subscribe(req)

    def send_order(self, req: OrderRequest):
        """"""
        return self.wsApi.tx_commitOrder_req(req)

    def cancel_order(self, req: CancelRequest):
        """"""
        self.wsApi.cancelOrder(req)

    def query_account(self):
        """"""
        pass

    def query_position(self):
        """"""
        pass

    def query_history(self, req: HistoryRequest):
        """"""
        return self.rest_api.query_history(req)

    def close(self):
        """"""
        self.ws_api.stop()



class JYTWebsocketApi(WebsocketClient):
    """"""

    def __init__(self, gateway):
        """"""
        super(JYTWebsocketApi, self).__init__()

        self.gateway = gateway
        self.gateway_name = gateway.gateway_name

        self.orderID = 0
        self.msgRespCallback = None

        # self.callbacks = {
        #     "trade": self.on_tick,
        #     "orderBook10": self.on_depth,
        #     "execution": self.on_trade,
        #     "order": self.on_order,
        #     "position": self.on_position,
        #     "margin": self.on_account,
        #     "instrument": self.on_contract,
        # }

        self.ticks = {}
        self.accounts = {}
        self.orders = {}
        self.trades = set()

    def unpackData(self, data):
        """重载"""
        s = data.replace('\n', '').replace('\r', '')
        return json.loads(s)
        # return json.loads(str,encoding='utf-8')


    def connect(self):
        """"""
        self.gateway.write_log(u'开始连接交易通...')
        #若无法建立连接,应该
        # 2.用网页版的websocket试试
        # 1.用jupyter notebook试试
        # 3.确认阿里云的防火墙设置没问题

        req="ws://"+self.gateway.TRADE_API_IP_PORT+"?rid=1&flag=1"
        self.init(req)
        self.start()

    def subscribe(self, req: SubscribeRequest):
        # """
        # Subscribe to tick data upate.
        # """
        # tick = TickData(
        #     symbol=req.symbol,
        #     exchange=req.exchange,
        #     name=req.symbol,
        #     datetime=datetime.now(),
        #     gateway_name=self.gateway_name,
        # )
        # self.ticks[req.symbol] = tick
        pass

    def on_connected(self):
        """"""
        self.gateway.write_log("Websocket API连接成功")
        self.tx_TradeInit_req()

    def on_disconnected(self):
        """"""
        self.gateway.write_log("Websocket API连接断开")

    def sendText(self, text):
        print("===SENT====")
        print(text)
        super(self.__class__, self)._send_text(text)

    def on_packet(self, jsonMsg: dict):
        print("---RCVD---")
        print(json.dumps(jsonMsg, ensure_ascii=False))

        """数据回调"""
        if self.msgRespCallback is not None:
            self.msgRespCallback(jsonMsg)

    def on_error(self, exception_type: type, exception_value: Exception, tb):
        """"""
        msg = f"触发异常，状态码：{exception_type}，信息：{exception_value}"
        self.gateway.write_log(msg)

        sys.stderr.write(self.exception_detail(
            exception_type, exception_value, tb))

    def subscribe_topic(self):
        pass
        # """
        # Subscribe to all private topics.
        # """
        # req = {
        #     "op": "subscribe",
        #     "args": [
        #         "instrument",
        #         "trade",
        #         "orderBook10",
        #         "execution",
        #         "order",
        #         "position",
        #         "margin",
        #     ],
        # }
        # self.send_packet(req)

    def tx_TradeInit_req(self):
        msg = ('{"req":"Trade_Init","rid":"2", '
               '"para":{"Broker" : ' + self.gateway.BROKER_ID + ','
                '"Net" : 0,'
                '"Server" : 2,'
                '"ClientVer" : "",'
                '"GetCount" : 32,'
                '"JsonType" : 0,'
                '"Core" : 1}}')

        self.msgRespCallback = self.on_TradeInit_resp
        self.sendText(msg)

    def on_TradeInit_resp(self, jsonMsg):
        if jsonMsg.get('event'):#event是券商发给交易通,然后返回的
            if jsonMsg.get('data').get('Result')==0:
                self.gateway.write_log("初始化交易通后端服务成功")
                self.tx_Login_req()
            else:
                self.gateway.write_log("初始化交易通后端服务失败")
                self.stop()

        elif jsonMsg.get('ret'):#ret是交易通直接返回的券商配置
            pass


    #----------------------------------------------------------------------
    def tx_Login_req(self):
        """"""
    # 登录券商服务器
        msg=('{"req":"Trade_Login","rid":"3",'
                '"para":{"Server" : 2,'
                '"AccountMode" : 9,'
                '"Broker" : '+self.gateway.BROKER_ID+','
                '"CreditAccount" : '+self.gateway.BROKER_IS_CREDIT_ACCNT+','
                '"DeptID" : "2",'
                '"Encode" : 0,'
                '"ReportSuccess" : 1000,'
                '"TryConn" : 3,'
                '"IP" : "'+self.gateway.BROKER_SERVER_IP+'",'
                '"LoginID" : "'+self.gateway.BROKER_ACCNT+'",'
                '"LoginPW" : "'+self.gateway.BROKER_ACCNT_PSWD+'",'
                '"Port" : '+self.gateway.BROKER_SERVER_PORT+','
                '"TradeID" : "'+self.gateway.BROKER_ACCNT+'",'
                '"Name" : "",'
                '"CommPW" : "'+self.gateway.BROKER_COMM_PSWD+'"}}')

        self.msgRespCallback = self.on_Login_resp
        self.sendText(msg)

    def on_Login_resp(self, jsonMsg):
        """"""
        if jsonMsg.get('event'):#event是券商发给交易通,然后返回的
            if jsonMsg.get('data').get('Result')==0:
                self.gateway.write_log("登录券商服务器成功")
                self.loginTime=int(time.time()*1000)
            else:
                self.gateway.write_log("登录券商服务器失败")
                self.stop()

        elif jsonMsg.get('ret'):#ret是交易通直接返回的券商配置
            pass

    #----------------------------------------------------------------------
    def tx_queryAccount_req(self):
        """"""
        msg = '{"req":"Trade_QueryData","rid":"5","para":{"JsonType" : 0,"QueryType" : 1}}'
        self.msgRespCallback = self.on_queryAccount_resp
        self.sendText(msg)

    def on_queryAccount_resp(self, jsonMsg):
        """"""
        accountid = str(d["account"])
        account = self.accounts.get(accountid, None)
        if not account:
            account = AccountData(accountid=self.gateway.BROKER_ACCNT,
                                  gateway_name=self.gateway_name)
            self.accounts[accountid] = account

        account.balance = float(jsonMsg.get(u'data')[0].get(u'总资产'))
        account.available = float(jsonMsg.get(u'data')[0].get(u'可用资金'))
        account.frozen = account.balance - account.available

        self.gateway.on_account(copy(account))
    #----------------------------------------------------------------------

    def find_exchange(self, symbol):
        #TODO 到底exchange用什么?
        if symbol[0] > '3':
            exchange = Exchange.SSE
        else:
            exchange = Exchange.SZSE
        return exchange

    #----------------------------------------------------------------------
    def tx_queryPosition_req(self):
        """"""
        msg = '{"req":"Trade_QueryData","rid":"6","para":{"JsonType" : 0,"QueryType" : 2}}'
        self.msgRespCallback = self.on_queryPosition_resp
        self.sendText(msg)

    def on_queryPosition_resp(self, jsonMsg):
        """"""
        data = jsonMsg.get('data')

        if data==None:
            return

        rows = len(data)

        for row in data:
            position = PositionData()
            position.gateway_name = self.gateway_name

            position.symbol = row.get(u'证券代码')

            position.exchange = self.find_exchange(position.symbol)

            position.volume = row.get(u'证券数量')
            position.frozen=row.get(u'冻结数量')
            position.price = row.get(u'成本价')
            position.pnl = row.get(u'浮动盈亏')

            self.gateway.on_position(position)

        self.gateway.on_position(position)


    #----------------------------------------------------------------------
    def tx_commitOrder_req(self, orderReq:OrderRequest):# type: (VtOrderReq)->str
        """"""
        self.orderID += 1
        orderID = str(self.loginTime + self.orderID)
        vtOrderID = '.'.join([self.gatewayName, orderID])

        if OrderRequest.direction.LONG == orderReq.direction :
            direction_type='1'
        else:
            direction_type='2'

        if orderReq.symbol[0] <= '3': #深圳和上海市场的判断
            marketType=1
        else:
            marketType=2

        msg = ('{"req":"Trade_CommitOrder","rid":"'+orderID+'",'
               '"para":[{'
               '"Code" : '+orderReq.symbol+','  # 品种代码
               '"Count" : '+str(orderReq.volume)+','  # 数量
               '"EType" : '+str(marketType)+','
               '"OType" : '+str(direction_type)+','  # 1买,2卖
               '"PType" : 1,'
               '"Price" : "'+str(orderReq.price) +'"' # 价格
               '}]}"')

        self.msgRespCallback = self.on_commitOrder_resp
        self.sendText(msg)

        return vtOrderID

    def on_commitOrder_resp(self, jsonMsg):
        """"""
        #TODO 发短信通知成功或错误
        msgStr=json.dumps(jsonMsg, ensure_ascii=False)
        if msgStr.find(u'错误')>=0 or msgStr.find(u'失败')>=0:
            errStr=json.dumps(jsonMsg.get('data'),ensure_ascii=False)
            self.gateway.write_log(errStr)
        elif msgStr.find(u'event')>=0:
            self.gateway.write_log('委托成功')


    def on_tick(self, d):
        """"""
        symbol = d["symbol"]
        tick = self.ticks.get(symbol, None)
        if not tick:
            return

        tick.last_price = d["price"]
        tick.datetime = datetime.strptime(
            d["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ")
        self.gateway.on_tick(copy(tick))

    def on_depth(self, d):
        """"""
        symbol = d["symbol"]
        tick = self.ticks.get(symbol, None)
        if not tick:
            return

        for n, buf in enumerate(d["bids"][:5]):
            price, volume = buf
            tick.__setattr__("bid_price_%s" % (n + 1), price)
            tick.__setattr__("bid_volume_%s" % (n + 1), volume)

        for n, buf in enumerate(d["asks"][:5]):
            price, volume = buf
            tick.__setattr__("ask_price_%s" % (n + 1), price)
            tick.__setattr__("ask_volume_%s" % (n + 1), volume)

        tick.datetime = datetime.strptime(
            d["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ")
        self.gateway.on_tick(copy(tick))

    def on_trade(self, d):
        """"""
        # # Filter trade update with no trade volume and side (funding)
        # if not d["lastQty"] or not d["side"]:
        #     return
        #
        # tradeid = d["execID"]
        # if tradeid in self.trades:
        #     return
        # self.trades.add(tradeid)
        #
        # if d["clOrdID"]:
        #     orderid = d["clOrdID"]
        # else:
        #     orderid = d["orderID"]
        #
        # trade = TradeData(
        #     symbol=d["symbol"],
        #     exchange=Exchange.BITMEX,
        #     orderid=orderid,
        #     tradeid=tradeid,
        #     direction=DIRECTION_BITMEX2VT[d["side"]],
        #     price=d["lastPx"],
        #     volume=d["lastQty"],
        #     time=d["timestamp"][11:19],
        #     gateway_name=self.gateway_name,
        # )
        #
        # self.gateway.on_trade(trade)

    # def on_order(self, d):
    #     """"""
    #     if "ordStatus" not in d:
    #         return
    #
    #     sysid = d["orderID"]
    #     order = self.orders.get(sysid, None)
    #     if not order:
    #         if d["clOrdID"]:
    #             orderid = d["clOrdID"]
    #         else:
    #             orderid = sysid
    #
    #         # time = d["timestamp"][11:19]
    #
    #         order = OrderData(
    #             symbol=d["symbol"],
    #             exchange=Exchange.BITMEX,
    #             type=ORDERTYPE_BITMEX2VT[d["ordType"]],
    #             orderid=orderid,
    #             direction=DIRECTION_BITMEX2VT[d["side"]],
    #             price=d["price"],
    #             volume=d["orderQty"],
    #             time=d["timestamp"][11:19],
    #             gateway_name=self.gateway_name,
    #         )
    #         self.orders[sysid] = order
    #
    #     order.traded = d.get("cumQty", order.traded)
    #     order.status = STATUS_BITMEX2VT.get(d["ordStatus"], order.status)
    #
    #     self.gateway.on_order(copy(order))

    def on_position(self, d):
        """"""
        position = PositionData(
            symbol=d["symbol"],
            exchange=Exchange.BITMEX,
            direction=Direction.NET,
            volume=d["currentQty"],
            gateway_name=self.gateway_name,
        )

        self.gateway.on_position(position)

    def on_account(self, d):
        """"""
        accountid = str(d["account"])
        account = self.accounts.get(accountid, None)
        if not account:
            account = AccountData(accountid=accountid,
                                  gateway_name=self.gateway_name)
            self.accounts[accountid] = account

        account.balance = d.get("marginBalance", account.balance)
        account.available = d.get("availableMargin", account.available)
        account.frozen = account.balance - account.available

        self.gateway.on_account(copy(account))

    def on_contract(self, d):
        """"""
        if "tickSize" not in d:
            return

        if not d["lotSize"]:
            return

        contract = ContractData(
            symbol=d["symbol"],
            exchange=Exchange.BITMEX,
            name=d["symbol"],
            product=Product.FUTURES,
            pricetick=d["tickSize"],
            size=d["lotSize"],
            stop_supported=True,
            net_position=True,
            history_data=True,
            gateway_name=self.gateway_name,
        )

        self.gateway.on_contract(contract)
