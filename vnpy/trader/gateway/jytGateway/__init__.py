# encoding: UTF-8

from __future__ import absolute_import
from vnpy.trader import vtConstant
from .jytGateway import JytGateway

gatewayClass = JytGateway
gatewayName = 'JYT'
gatewayDisplayName = '交易通'
gatewayType = vtConstant.GATEWAYTYPE_EQUITY
gatewayQryEnabled = False  #是否每秒轮询执行gateway中的queryInfo()
