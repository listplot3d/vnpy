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

from vnpy.event import Event
from vnpy.trader.event import EVENT_TIMER

import json
import tushare as ts
import pandas as pd
import traceback

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
    broker_logined=False

    def __init__(self, event_engine):
        """Constructor"""
        super(JYTGateway, self).__init__(event_engine, "JYT")

        self.jytWsApi = JYTWebsocketApi(self)

    def connect(self, setting: dict):
        #load config from file
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

        #connect to JYT
        self.jytWsApi.connect()

        #connect to tushare
        self.tsPro=ts.pro_api(self.TUSHARE_TOKEN)

        #turn on periodical query
        self.init_query()


    def subscribe(self, req: SubscribeRequest):
        self.jytWsApi.subscribe(req)

    def send_order(self, req: OrderRequest):
        return self.jytWsApi.tx_commitOrder_req(req)

    def cancel_order(self, req: CancelRequest):
        self.jytWsApi.cancelOrder(req)

    def query_account(self):
        self.jytWsApi.tx_queryAccount_req()

    def query_position(self):
        self.jytWsApi.tx_queryPosition_req()

    def query_history(self, req: HistoryRequest):
        return self.rest_api.query_history(req)

    def close(self):
        self.jytWsApi.stop()

    def update_fr_tushare(self):

    # 挪到subuscribe里面去了，意味着jyt和ts还是没分开
    # TODO： jyt和TS应该分开
    # if self.queryTimes is 0:
        #     self.jytWsApi.on_contract()

        self.jytWsApi.on_depth()

    def update_fr_jyt(self):
        # 轮训交易通的连接状态
        keepalive_secs = 30
        if self.queryTimes % keepalive_secs == 0:
                self.jytWsApi.tx_CheckStatus()

        #如果有好几条消息都timeout,则认为通讯出问题了要重连接
        timout_tolerance_cnt = 3
        if self.jytWsApi.respHdlrs_count_timeout() >=timout_tolerance_cnt:
            self.write_log("超时消息太多，开始重新连接")
            self.jytWsApi.connect()
            #todo: 如果发单的时候通讯断了如何处理？

        #因此，断线后触发重连的最慢时间为
        # (基础的timout时间+keepalive_secs*timout_tolerance_cnt)


    def process_timer_event(self, event: Event):
        self.update_fr_jyt() #没交易的时候刷出来的也是固定的，不用刷
        self.update_fr_tushare()
        self.queryTimes = self.queryTimes +1
        # self.check_jyt_resp_timeout()


    def init_query(self):
        # self.query_contract()
        # self.query_trade()
        # self.query_order()
        # self.query_position() #放到login后面串行调用了
        # self.query_account() #放到login后面串行调用了

        self.queryTimes=0
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)


class JYTWebsocketApi(WebsocketClient):
    """"""

    def __init__(self, gateway):
        """"""
        super(JYTWebsocketApi, self).__init__()

        self.gateway = gateway
        self.gateway_name = gateway.gateway_name

        self.orderID = 0

        self.loginTime=0

        self.ticks = {}
        self.accounts = {}
        self.orders = {}
        self.trades = set()

        self.last_rid = '0'

    #----------------------------------------------------------------------
    def comm_mute_period(self):
        """早上8:00-9:05之间券商服务器会断开"""
        h = datetime.now().hour
        m = datetime.now().minute

        if (h == 8) or (h == 9 and m < 5):
            return True
        else:
            return False
    #----------------------------------------------------------------------
    def unpackData(self, data):
        """重载"""
        s = data.replace('\n', '').replace('\r', '')
        return json.loads(s)
        # return json.loads(str,encoding='utf-8')

    #----------------------------------------------------------------------
    def sendText(self, text):
        print("===SENT====")
        print(text)
        super(self.__class__, self)._send_text(text)

    #----------------------------------------------------------------------
    def on_packet(self, jsonMsg: dict):
        print("---RCVD---")
        print(json.dumps(jsonMsg, ensure_ascii=False))

        #提取rid
        rid=str(jsonMsg.get('rid'))
        hldrs= self.respHandlers[self.respHandlers['rid']==rid]

        #检查异常
        if len(hldrs) is 0:
            self.gateway.write_log("Error: rcvd unexpected rid "+str(rid))
            return

        #提取并调用callback
        hldr = hldrs.head(1)

        if jsonMsg.get('event') is not None:
            event_callback = hldr.iloc[0]['event_callback']
            if event_callback is not None:
                event_callback(jsonMsg)
                self.respHdlrs_remove_by_rid(rid)

        elif jsonMsg.get('ret') is not None :
            ret_callback = hldr.iloc[0]['ret_callback']
            if ret_callback is not None :
                ret_callback(jsonMsg)
                self.respHdlrs_remove_by_rid(rid) #放里面确保调用callback之后才会清掉
        else:
            self.gateway.write_log("Error: rcvd unexpected msg type")



    #----------------------------------------------------------------------
    def on_error(self, exception_type: type, exception_value: Exception, tb):
        """"""
        msg = f"触发异常，状态码：{exception_type}，信息：{exception_value}"
        self.gateway.write_log(msg)

        sys.stderr.write(self.exception_detail(
            exception_type, exception_value, tb))

    # ----------------------------------------------------------------------

    def new_rid(self):
        self.last_rid = int(self.last_rid) + 1
        return str(self.last_rid)

    # ----------------------------------------------------------------------
    def init_respHandlers(self):

        # 维护一张类似下面的表，用来收到消息后找callback
        # 注意，ret_callback和event_callback一旦调用就会从表中移除rid，因此会导致另一个callback失效（如果有的话)
        # rid     |    timeout_stamp     |     ret_callback    |    event_callback
        # --------------------------------------------------------------------------
        #  1      |  2019-06-09 09:15:00 |    this.donothing   |    jtyApi.connect
        #  2      |  2019-06-09 09:16:00 |    this.donothing   |    jtyApi.Login

        self.respHandlers = pd.DataFrame({'rid': [],
                                          'timeout_stamp': [],
                                          'ret_callback': [],
                                          'event_callback': []})

    # ----------------------------------------------------------------------

    def respHdlrs_add(
            self, rid='0', timeoutSecs=5, ret_callback=None, event_callback=None):
        """ add item into response message waiting list"""

        timeout_stamp = datetime.now() + timedelta(seconds=timeoutSecs)

        d = {'rid': rid,
             'timeout_stamp': timeout_stamp,
             'ret_callback': ret_callback,
             'event_callback': event_callback}

        self.respHandlers = \
            self.respHandlers.append(d, ignore_index=True)

        # self.gateway.write_log("ADDed. callbacks:" + str(len(self.respHandlers)))

    # ----------------------------------------------------------------------
    def respHdlrs_remove_by_rid(self, rid):
        """ remove item into response message waiting list"""

        self.respHandlers=self.respHandlers.loc[self.respHandlers['rid'] != rid]

        # self.respHandlers.drop(
        #     self.respHandlers[
        #         self.respHandlers['rid'] == rid].index, inplace=True)
        # # self.gateway.write_log("RMed.remaining callbacks:" + str(len(self.respHandlers)))

    def respHdlrs_remove_by_index(self, hldr_index):
        ''''
        警告：如果用这个函数,会导致并发收到packet时，删pandas的row串线。即，找出来index后，删row之前，index其实已经变了
        '''

        #
        # self.respHandlers=self.respHandlers.drop(hldr_index, inplace=True)
        #
        # self.gateway.write_log("RMed.remaining callbacks:" + str(len(self.respHandlers)))
        # # print(self.respHandlers[['rid','timeout_stamp']])
        pass
    # ----------------------------------------------------------------------
    def respHdlrs_count_timeout(self):
        timeout_rows = self.respHandlers['timeout_stamp'] > datetime.now()
        return len(timeout_rows)
    # ----------------------------------------------------------------------
    def connect(self):
        """"""
        self.init_respHandlers()
        self.gateway.write_log(u'开始连接交易通...')
        # 若无法建立连接,应该
        # 2.用网页版的websocket试试
        # 1.用jupyter notebook试试
        # 3.确认阿里云的防火墙设置没问题
        rid = self.new_rid()
        req = "ws://" + self.gateway.TRADE_API_IP_PORT + "?rid=" + rid + "&flag=1"
        self.init(req)
        self.start()

    # ----------------------------------------------------------------------

    def on_connected(self):
        """"""
        self.gateway.write_log("Websocket连接成功")
        self.tx_TradeInit_req()

    # ----------------------------------------------------------------------
    def on_disconnected(self):
        """"""
        self.gateway.write_log("Websocket连接断开")

    # ----------------------------------------------------------------------
    def subscribe(self, req: SubscribeRequest):
        """
        Subscribe to tick data update.
        """
        tick = TickData(
            symbol=req.symbol,
            exchange=req.exchange,
            name=req.symbol,
            datetime=datetime.now(),
            gateway_name=self.gateway_name,
        )
        self.ticks[req.symbol] = tick
        #tick数据的更新由on_tick()去做

        #TODO: contract列表应当自动搜索出来。
        # 而现在每次要手工subscribe之后才能执行策略,导致没法命令行执行
        # 否则策略报错说找不到contract
        self.add_contract(req.symbol)

    #----------------------------------------------------------------------
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

    #----------------------------------------------------------------------
    def tx_CheckStatus(self):
        if self.loginTime is 0: #用户登录之后才能查状态
            return
        if self.comm_mute_period():
            print("处于通讯休息时段")
            return

        rid = self.new_rid()
        msg = ('{"req":"Trade_CheckStatus","rid":'+rid+', '
               '"para":{"Server": 2}}')

        self.respHdlrs_add(rid=rid, ret_callback=self.on_CheckStatus_resp)
        self.sendText(msg)

    #----------------------------------------------------------------------
    def on_CheckStatus_resp(self,jsonMsg):
        if jsonMsg.get('data').get('Status') == 0:
            # self.gateway.write_log("交易连接正常")
            print("交易连接正常")
            pass
        else:
            self.gateway.write_log("交易连接异常")
            if not(self.comm_mute_period()):
                self._reconnect()

    #----------------------------------------------------------------------
    def tx_TradeInit_req(self):
        rid = self.new_rid()
        msg = ('{"req":"Trade_Init","rid":'+rid+', '
               '"para":{"Broker" : ' + self.gateway.BROKER_ID + ','
                '"Net" : 0,'
                '"Server" : 2,'
                '"ClientVer" : "",'
                '"GetCount" : 32,'
                '"JsonType" : 0,'
                '"Core" : 1}}')

        self.respHdlrs_add(rid=rid, event_callback=self.on_TradeInit_resp)
        self.sendText(msg)



    #----------------------------------------------------------------------
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
        rid = self.new_rid()
        msg=('{"req":"Trade_Login","rid":'+rid+','
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

        # self.msgRespCallback = self.on_Login_resp
        self.respHdlrs_add(rid=rid, event_callback=self.on_Login_resp)
        self.sendText(msg)

    #----------------------------------------------------------------------
    def on_Login_resp(self, jsonMsg):
        """"""

        # event是券商发给交易通,然后返回的
        # ret是交易通直接返回的券商配置
        if not(jsonMsg.get('event')):
            return

        if jsonMsg.get('data').get('Result')==0:
            self.gateway.broker_logined=True
            self.gateway.write_log("登录券商服务器成功")
            self.loginTime=int(time.time()*1000)
            self.tx_queryAccount_req()
            self.tx_queryPosition_req()


        else:
            self.gateway.write_log("登录券商服务器失败")
            self.loginTime=0
            # self.stop()
            if not(self.comm_mute_period()):
                self._reconnect()



    #----------------------------------------------------------------------
    def tx_queryAccount_req(self):
        """"""
        if self.loginTime is 0: #用户登录之后才能查账户
            return

        rid=self.new_rid()
        msg = '{"req":"Trade_QueryData","rid":'+rid+',"para":{"JsonType" : 0,"QueryType" : 1}}'
        self.respHdlrs_add(rid=rid, ret_callback=self.on_queryAccount_resp)
        self.sendText(msg)

    #----------------------------------------------------------------------
    def on_queryAccount_resp(self, jsonMsg):
        """查询账户资金"""
        if jsonMsg.get('event'):
            return

        accountid = str(self.gateway.BROKER_ACCNT)
        account = self.accounts.get(accountid, None)
        if not account:
            account = AccountData(accountid=self.gateway.BROKER_ACCNT,
                                  gateway_name=self.gateway_name)
            self.accounts[accountid] = account

        account.balance = float(jsonMsg.get(u'data')[0].get(u'总资产'))
        account.available = float(jsonMsg.get(u'data')[0].get(u'可用资金'))
        account.frozen = account.balance - account.available

        self.gateway.on_account(copy(account))
        self.gateway.write_log("查询账户完成")
    #----------------------------------------------------------------------

    def find_exchange(self, symbol):
        if symbol[0] > '3':
            exchange = Exchange.SSE
        else:
            exchange = Exchange.SZSE
        return exchange

    #----------------------------------------------------------------------
    def tx_queryPosition_req(self):
        """查询持仓明细"""
        if self.loginTime is 0: #用户登录之后才能查仓位
            return

        rid = self.new_rid()
        msg = '{"req":"Trade_QueryData","rid":'+rid+',"para":{"JsonType" : 0,"QueryType" : 2}}'
        # self.msgRespCallback = self.on_queryPosition_resp
        self.respHdlrs_add(rid=rid, ret_callback=self.on_queryPosition_resp)
        self.sendText(msg)

    def on_queryPosition_resp(self, jsonMsg):
        """"""

        data = jsonMsg.get('data')

        if data == None:
            return

        rows = len(data)

        for row in data:
            s=row.get(u'证券代码')
            position = PositionData(
                symbol=s,
                exchange=self.find_exchange(s),
                direction=Direction.LONG,
                volume=float(row.get(u'证券数量')),
                frozen=float(row.get(u'冻结数量')),
                gateway_name=self.gateway_name,
                price=float(row.get(u'成本价')),
                pnl=float(row.get(u'浮动盈亏'))
            )

            self.gateway.on_position(position)
        self.gateway.write_log("查询持仓完成")



    #----------------------------------------------------------------------
    def tx_commitOrder_req(self, orderReq:OrderRequest):# type: (VtOrderReq)->str
        """"""
        self.orderID += 1
        orderID = str(self.loginTime + self.orderID)
        vtOrderID = '.'.join([self.gateway_name, orderID])

        if Direction.LONG == orderReq.direction :
            direction_type='1'
        else:
            direction_type='2'

        if self.find_exchange(orderReq.symbol)=='SZSE':
            marketType = 1
        else:
            marketType = 2

        rid = self.new_rid()
        msg = ('{"req":"Trade_CommitOrder","rid":"'+rid+'",'
               '"para":[{'
               '"Code" : '+orderReq.symbol+','  # 品种代码
               '"Count" : '+str(orderReq.volume)+','  # 数量
               '"EType" : '+str(marketType)+','
               '"OType" : '+str(direction_type)+','  # 1买,2卖
               '"PType" : 1,'
               '"Price" : "'+str(orderReq.price) +'"' # 价格
               '}]}"')

        # self.msgRespCallback = self.on_commitOrder_resp
        self.respHdlrs_add(rid=rid, event_callback = self.on_commitOrder_resp)
        self.sendText(msg)

        return vtOrderID

    #----------------------------------------------------------------------
    def on_commitOrder_resp(self, jsonMsg):
        """"""
        #TODO 发短信通知成功或错误
        msgStr=json.dumps(jsonMsg, ensure_ascii=False)
        if msgStr.find(u'错误')>=0 or msgStr.find(u'失败')>=0:
            errStr=json.dumps(jsonMsg.get('data'),ensure_ascii=False)
            self.gateway.write_log(errStr)
        elif msgStr.find(u'event')>=0:
            self.gateway.write_log('委托成功')

    #----------------------------------------------------------------------
    def to_float(self, value):
        if value == "":
            return 0.0  # 9:00之后集合竞价之前volume会刷成0
        else:
            return float(value)

    #----------------------------------------------------------------------
    def on_tick(self):
        """"""
        try:
            for symbol in self.ticks:
                tick = self.ticks.get(symbol, None)
                df = ts.get_realtime_quotes(symbol)

                tick.last_price = self.to_float(df["price"][0])
                timestamp=df['date'][0]+' '+df['time'][0]
                tick.datetime = datetime.strptime(
                    timestamp, "%Y-%m-%d %H:%M:%S")
                self.gateway.on_tick(copy(tick))
        except:
            self.gateway.write_log('Error: on_tick 价格更新异常')
            print(df)

    #----------------------------------------------------------------------

    def on_depth(self):
        """"""

        #TODO 改成只query一次，然后本地解析数据
        try:
            for symbol in self.ticks:
                tick = self.ticks.get(symbol, None)
                df = ts.get_realtime_quotes(symbol)

                tick.name=str(df['name'][0])
                tick.last_price=self.to_float(df['price'][0])
                tick.open_price=self.to_float(df['open'][0])
                tick.high_price=self.to_float(df['high'][0])
                tick.low_price=self.to_float(df['low'][0])
                tick.pre_close=self.to_float(df['pre_close'][0])
                tick.volume=self.to_float(df['volume'][0])


                for n in range(1, 6):
                    #set bid prices
                    bprice =self.to_float(df.__getattr__("b%s_p" % n)[0])
                    bvolume = self.to_float(df.__getattr__("b%s_v" % n)[0])

                    tick.__setattr__("bid_price_%s" % n, bprice)
                    tick.__setattr__("bid_volume_%s" % n, bvolume)

                    #set ask prices
                    price =self.to_float(df.__getattr__("a%s_p" % n)[0])
                    volume=self.to_float(df.__getattr__("a%s_v" % n)[0])
                    tick.__setattr__("ask_price_%s" % n, price)
                    tick.__setattr__("ask_volume_%s" % n, volume)

                    #set timestamp
                    timestamp = df['date'][0] + ' ' + df['time'][0]
                    tick.datetime = datetime.strptime(
                        timestamp, "%Y-%m-%d %H:%M:%S")

                #update to UI
                self.gateway.on_tick(copy(tick))
        except:
            self.gateway.write_log('Error: on_depth 价格更新异常')
            print("Unexpected error:", traceback.format_exc())

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

    def add_contract(self, symbol):
        """"""
        contract = ContractData(
            symbol=symbol,
            exchange=self.find_exchange(symbol),
            name=symbol,
            product=Product.EQUITY,
            pricetick=0.001,
            size=100,
            stop_supported=False,
            net_position=False,
            history_data=False,
            gateway_name=self.gateway_name,
        )
        self.gateway.on_contract(contract)
