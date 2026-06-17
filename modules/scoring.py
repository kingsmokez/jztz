"""评分模块 - 多因子价值评分引擎 v5

重构要点:
1. 移除所有bare except，使用具体异常
2. 引入logging替代print
3. 提取评分函数为纯函数，便于测试
4. 支持评分短路优化
"""

from __future__ import annotations

__all__ = [
    # 行业 PE 阈值配置（按行业动态调整）
    "SECTOR_PE_RANGES",
    # 顶层入口
    "quick_score",
    "full_score",
    "rank_stocks",
    "evaluate_stock",
    "calculate_buy_sell",
    "multi_factor_evaluate",
    # 多因子分项
    "calculate_value_score",
    "calculate_quality_score",
    "calculate_growth_score",
    "calculate_momentum_score",
    "mf_score_value",
    "mf_score_quality",
    "mf_score_growth",
    "mf_score_momentum",
    "mf_score_sentiment",
    # 热点分析
    "calculate_hot_factor",
    "get_hot_sectors_and_news",
]

import math
from dataclasses import dataclass
from typing import Optional

from modules.config import ScoringConfig
from modules.logger import log
from modules.models import StockQuote, FinancialData

_config = ScoringConfig()

# ========== 行业PE合理范围（10大行业分类，含动态PE区间）==========
SECTOR_PE_RANGES = {
    # 半导体/芯片：189只样本中位数84.6，P25=40.6，P75=151.6
    'semiconductor': {
        'industry_names': ['半导体', '芯片', '集成电路'],
        'keywords': ['半导体', '芯片', '集成电路', 'GPU', '算力'],
        'pe_fair_max': 100,
        'pe_fair_low': 28,
    },
    # 生物制品/医药/医疗器械：中位数27-70，P75=96.5
    'bio_pharma': {
        'industry_names': ['生物制品', '医药', '医疗服务', '医疗器械', '中药', '医疗行业', '医药制造'],
        'keywords': ['生物制品', '医药', '医疗', '制药', '疫苗', 'CXO', '中药', '器械'],
        'pe_fair_max': 80,
        'pe_fair_low': 13,
    },
    # 新能源/电池/光伏/风电：中位数33-42，P75=71.7
    'new_energy': {
        'industry_names': ['电池', '光伏', '储能', '锂电', '新能源', '光伏设备', '风电设备'],
        'keywords': ['电池', '光伏', '储能', '锂电', '新能源', '固态', '钠电', '充电桩', '风电'],
        'pe_fair_max': 50,
        'pe_fair_low': 22,
    },
    # 电子元件/消费电子：中位数39-49，P75=74.2
    'electronics': {
        'industry_names': ['电子元件', '消费电子', '电子'],
        'keywords': ['电子元件', '消费电子', '光通信', 'PCB', '电路板', '苹果产业链'],
        'pe_fair_max': 85,
        'pe_fair_low': 25,
    },
    # 软件/信息服务/AI：中位数86.8，P75=131.9（高成长行业）
    'software_it': {
        'industry_names': ['软件', '信息服务', '通信', '数字经济', '软件服务'],
        'keywords': ['软件', '信息', '科技', '数字', '云计算', '大数据', 'AI', '人工智能'],
        'pe_fair_max': 120,
        'pe_fair_low': 39,
    },
    # 汽车制造/零部件：中位数15.9-26.2，P75=48.6
    'automotive': {
        'industry_names': ['汽车制造', '汽车零部件', '汽车整车'],
        'keywords': ['汽车制造', '汽车零部件', '汽车', '新能源汽车'],
        'pe_fair_max': 50,
        'pe_fair_low': 10,
    },
    # 电气设备/机械：中位数23.3，P75=96.2
    'electrical_machinery': {
        'industry_names': ['电气设备', '机械', '专用设备'],
        'keywords': ['电气设备', '机械', '重工', '电力设备', '专用设备'],
        'pe_fair_max': 35,
        'pe_fair_low': 16,
    },
    # 金融/地产/公用/券商：中位数6-14，低PE行业
    'finance_utility': {
        'industry_names': ['银行', '保险', '证券', '房地产', '公用事业', '券商信托', '电力行业', '港口水运'],
        'keywords': ['银行', '保险', '证券', '地产', '房地产', '公用', '电力', '水务', '高速', '港口', '券商'],
        'pe_fair_max': 20,
        'pe_fair_low': 8,
    },
    # 周期/化工/有色：中位数18-22，P25=17
    'cyclical': {
        'industry_names': ['化工', '有色金属', '钢铁', '建材', '煤炭', '石油', '化工行业', '化学原料'],
        'keywords': ['化工', '有色', '钢铁', '建材', '煤炭', '石油', '水泥', '玻璃', '矿业', '化学'],
        'pe_fair_max': 30,
        'pe_fair_low': 12,
    },
    # 消费/食品饮料：参考医药，中位数约25，合理区间15-40
    'consumer': {
        'industry_names': ['食品饮料', '消费', '旅游', '免税', '零售', '白酒', '家电', '消费电子'],
        'keywords': ['消费', '食品', '饮料', '酒', '旅游', '免税', '零售', '家电'],
        'pe_fair_max': 45,
        'pe_fair_low': 14,
    },
}

# 排除列表
LIQUOR_NAMES = ["贵州茅台", "五粮液", "洋河股份", "泸州老窖", "山西汾酒", "酒鬼酒", "水井坊", "古井贡酒", "古井贡酒", "迎驾贡酒", "今世缘", "舍得酒业", "老白干酒", "伊力特", "口子窖", "金徽酒", "皇台酒业", "岩石股份", "顺鑫农业"]
BANK_CODES = ["601398", "601288", "600000", "600036", "601166", "600015", "600016", "601328", "600919", "600028", "601939", "601988", "601318", "600030"]


# ========== 股票所属板块映射（关键股票）==========
STOCK_SECTOR_MAP = {
    # 半导体/芯片
    "002371": ["半导体", "芯片", "人工智能"],
    "300661": ["半导体", "芯片"],
    "688981": ["半导体", "芯片"],
    "603501": ["半导体", "芯片"],
    "002049": ["半导体", "芯片"],
    "688332": ["半导体", "芯片"],
    "603929": ["半导体", "芯片"],
    "300308": ["光通信", "人工智能", "通信"],
    "300394": ["光通信", "人工智能", "通信"],
    # 新能源/光伏/储能/固态电池
    "300274": ["光伏", "储能", "新能源", "固态电池"],
    "601012": ["光伏", "新能源"],
    "002459": ["光伏", "储能"],
    "300014": ["锂电", "新能源", "固态电池"],
    "002594": ["新能源汽车", "新能源", "汽车"],
    "300750": ["锂电", "新能源", "储能", "固态电池"],
    # 医药
    "300015": ["医药", "医疗服务"],
    "300760": ["医疗器械", "医药"],
    "300122": ["医药", "生物制品"],
    "002007": ["医疗器械", "医药"],
    "603259": ["医药", "CXO"],
    "600211": ["医药", "中药"],
    "600329": ["医药", "中药"],
    "688336": ["医药", "生物制品"],
    # 消费电子
    "002475": ["消费电子", "苹果", "汽车"],
    "002241": ["消费电子", "苹果"],
    "600588": ["人工智能", "数字经济"],
    # 科技/AI
    "300059": ["人工智能", "数字经济"],
    "002230": ["人工智能", "数字经济"],
    "002405": ["人工智能", "数字经济"],
    "300033": ["数字经济", "证券"],
    # 新能源汽车/汽车零部件
    "002812": ["新能源汽车", "锂电", "钠电池"],
    "600841": ["汽车零部件", "汽车", "新能源"],
    # 锂矿/锂电
    "000792": ["锂矿", "锂电", "新能源"],
    "002466": ["锂矿", "锂电"],
    "002460": ["锂电", "新能源"],
    # 其他
    "002352": ["物流"],
    "603288": ["食品饮料", "消费"],
    "002039": ["电力", "新能源"],
    "600415": ["商贸", "互联金融"],
    "600660": ["汽车零部件", "汽车"],
    "002546": ["电力设备", "新能源"],
    "002895": ["化工", "磷化工"],
    "000612": ["有色金属", "铝"],
}

# 热门关键词到板块的映射（用于从新闻中识别热点）
HOT_KEYWORD_TO_SECTOR = {
    # 科技
    "AI": ["人工智能", "数字经济"],
    "ChatGPT": ["人工智能"],
    "大模型": ["人工智能"],
    "芯片": ["半导体", "芯片"],
    "GPU": ["半导体"],
    "算力": ["人工智能", "数字经济"],
    "光模块": ["光通信", "人工智能"],
    "半导体": ["半导体", "芯片"],
    # 新能源
    "光伏": ["光伏", "储能"],
    "储能": ["储能", "新能源"],
    "锂电池": ["锂电", "新能源"],
    "锂电": ["锂电", "新能源"],
    "固态电池": ["固态电池", "锂电"],
    "钠电池": ["钠电池", "锂电"],
    "新能源": ["新能源", "光伏"],
    "电动车": ["新能源汽车", "汽车"],
    "充电桩": ["新能源汽车"],
    # 医药
    "医药": ["医药", "医疗器械"],
    "创新药": ["医药", "生物制品"],
    "疫苗": ["生物制品", "医药"],
    # 消费
    "消费": ["消费", "食品饮料"],
    "白酒": ["白酒", "消费"],
    # 周期
    "锂矿": ["锂矿", "锂电"],
    "铝": ["有色金属"],
    "铜": ["有色金属"],
}

# 板块关键词映射（用于新闻热点识别）
SECTOR_KEYWORDS: dict[str, list[str]] = {
    "半导体": ["芯片", "半导体", "集成电路", "AI芯片", "GPU", "CPU", "存储芯片", "封装", "光刻"],
    "人工智能": ["人工智能", "AI", "大模型", "ChatGPT", "生成式AI", "机器学习", "深度学习", "自动驾驶", "Sora"],
    "新能源汽车": ["新能源车", "电动车", "电动汽车", "混动", "充电桩", "电池", "锂电", "固态电池", "比亚迪", "特斯拉", "宁德时代"],
    "光伏": ["光伏", "太阳能", "硅片", "组件", "逆变器", "HJT", "TOPCon"],
    "医药生物": ["医药", "生物", "创新药", "疫苗", "CXO", "医疗器械", "中药", "仿制药", "PD-1", "医保"],
    "消费电子": ["消费电子", "手机", "华为", "苹果", "MR", "VR", "AR", "折叠屏", "智能穿戴"],
    "房地产": ["房地产", "楼市", "房价", "房企", "拿地", "保交楼", "城中村", "地产"],
    "银行": ["银行", "信贷", "贷款", "降准", "降息", "LPR", "利率", "央行"],
    "军工": ["军工", "国防", "航天", "航空", "导弹", "军备", "战斗机", "航母"],
    "白酒": ["白酒", "茅台", "五粮液", "酒"],
    "证券": ["证券", "券商", "资本市场", "IPO", "注册制", "北交所", "牛市", "熊市"],
    "数字经济": ["数字经济", "数据要素", "云计算", "大数据", "数据中心", "算力"],
    "机器人": ["机器人", "人形机器人", "工业机器人", "减速器", "伺服电机"],
    "游戏传媒": ["游戏", "传媒", "影视", "短剧", "直播", "网游"],
    "有色金属": ["有色", "黄金", "铜", "铝", "锂", "稀土", "钴", "镍"],
    "养殖": ["养殖", "猪", "鸡", "饲料", "农业"],
    "电力": ["电力", "电网", "储能", "特高压", "风电", "核电", "火电"],
    "化工": ["化工", "新材料", "塑料", "化纤"],
}


def quick_score(quote: StockQuote, financial: Optional[FinancialData] = None) -> float:
    """快速评分 - 用于筛选候选股（评分短路）

    低于阈值的股票直接跳过详细评分，节省计算时间。
    """
    score = 0.0

    # PE快速判断
    if financial and financial.pe > 0:
        if financial.pe <= 20:
            score += 25
        elif financial.pe <= 40:
            score += 15
        else:
            score += 5
    elif quote.pe > 0:
        if quote.pe <= 20:
            score += 25
        elif quote.pe <= 40:
            score += 15

    # 市值快速判断
    if financial and financial.market_cap > 0:
        if financial.market_cap >= _config.min_market_cap:
            score += 25
        elif financial.market_cap >= 10:
            score += 15
    elif quote.market_cap > 0:
        if quote.market_cap >= _config.min_market_cap:
            score += 25
        elif quote.market_cap >= 10:
            score += 15

    # ROE快速判断
    if financial and financial.roe > 0:
        if financial.roe >= 10:
            score += 25
        elif financial.roe >= 5:
            score += 15
        else:
            score += 5
    else:
        score += 10

    # 换手率判断
    if quote.turnover > 0:
        if 0.5 <= quote.turnover <= 10:
            score += 25
        elif quote.turnover > 10:
            score += 15
        else:
            score += 5

    return score


def calculate_value_score(
    pe: float,
    pb: float,
    roe: float,
    market_cap: float,
    sector: str = "",
) -> float:
    """计算价值评分 (0-100)"""
    score = 0.0

    # PE评分
    score += _pe_score(pe, sector)

    # PB评分
    score += _pb_score(pb)

    # ROE评分
    score += _roe_score(roe)

    # 市值评分
    score += _market_cap_score(market_cap)

    return min(100, max(0, score))


def calculate_growth_score(
    revenue_growth: float,
    profit_growth: float,
    roe: float,
) -> float:
    """计算成长评分 (0-100)"""
    score = 0.0

    # 营收增长评分
    score += _growth_item_score(revenue_growth)

    # 利润增长评分
    score += _growth_item_score(profit_growth)

    # ROE增长质量
    if roe > 15:
        score += 20
    elif roe > 10:
        score += 15
    elif roe > 5:
        score += 10
    elif roe > 0:
        score += 5

    return min(100, max(0, score))


def calculate_quality_score(
    debt_ratio: float,
    gross_margin: float,
    roe: float,
) -> float:
    """计算质量评分 (0-100)"""
    score = 0.0

    # 负债率评分（低负债更好）
    if debt_ratio <= 30:
        score += 35
    elif debt_ratio <= 50:
        score += 25
    elif debt_ratio <= 70:
        score += 15
    else:
        score += 5

    # 毛利率评分
    if gross_margin >= 40:
        score += 35
    elif gross_margin >= 25:
        score += 25
    elif gross_margin >= 15:
        score += 15
    else:
        score += 5

    # ROE稳定性
    if 8 <= roe <= 25:
        score += 30
    elif roe > 25:
        score += 20
    elif roe > 0:
        score += 10

    return min(100, max(0, score))


def calculate_momentum_score(change_pct: float, turnover: float, amount: float) -> float:
    """计算动量评分 (0-100)"""
    score = 0.0

    # 涨跌幅评分
    if -2 <= change_pct <= 5:
        score += 30
    elif 5 < change_pct <= 9.5:
        score += 40
    elif change_pct > 9.5:
        score += 20
    elif -5 <= change_pct < -2:
        score += 15
    else:
        score += 5

    # 换手率评分
    if 1 <= turnover <= 8:
        score += 35
    elif 0.5 <= turnover < 1:
        score += 20
    elif 8 < turnover <= 15:
        score += 25
    else:
        score += 10

    # 成交额评分
    if amount >= 10000:  # 亿
        score += 35
    elif amount >= 5000:
        score += 25
    elif amount >= 1000:
        score += 15
    else:
        score += 5

    return min(100, max(0, score))


def full_score(
    quote: StockQuote,
    financial: Optional[FinancialData] = None,
    sector: str = "",
    tech_score: float = 0,
) -> dict:
    """完整评分 - 返回五维评分结果"""
    pe = financial.pe if financial else quote.pe
    pb = financial.pb if financial else quote.pb
    roe = financial.roe if financial else 0
    market_cap = financial.market_cap if financial else quote.market_cap
    revenue_growth = financial.revenue_growth if financial else 0
    profit_growth = financial.profit_growth if financial else 0
    debt_ratio = financial.debt_ratio if financial else 0
    gross_margin = financial.gross_margin if financial else 0

    value = calculate_value_score(pe, pb, roe, market_cap, sector)
    growth = calculate_growth_score(revenue_growth, profit_growth, roe)
    quality = calculate_quality_score(debt_ratio, gross_margin, roe)
    momentum = calculate_momentum_score(quote.change_pct, quote.turnover, quote.amount)

    # 加权总分
    total = (
        value * 0.30
        + growth * 0.20
        + quality * 0.20
        + tech_score * 0.15
        + momentum * 0.15
    )

    return {
        "code": quote.code,
        "name": quote.name,
        "price": quote.price,
        "change_pct": quote.change_pct,
        "pe": pe,
        "pb": pb,
        "roe": roe,
        "market_cap": market_cap,
        "total_score": round(total, 1),
        "value_score": round(value, 1),
        "growth_score": round(growth, 1),
        "quality_score": round(quality, 1),
        "tech_score": round(tech_score, 1),
        "momentum_score": round(momentum, 1),
    }


def rank_stocks(scored: list[dict]) -> list[dict]:
    """按总分排序并添加排名"""
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    for i, item in enumerate(scored, 1):
        item["rank"] = i
    return scored


# === 内部评分子函数 ===


def _pe_score(pe: float, sector: str = "") -> float:
    """PE评分 (0-25)"""
    if pe <= 0:
        return 3
    # 兼容新版SECTOR_PE_RANGES的dict格式和旧版tuple格式
    sector_info = SECTOR_PE_RANGES.get(sector)
    if isinstance(sector_info, dict):
        low, high = sector_info['pe_fair_low'], sector_info['pe_fair_max']
    elif isinstance(sector_info, tuple):
        low, high = sector_info
    else:
        low, high = 8, 25
    if low <= pe <= high:
        return 25
    if pe < low:
        ratio = pe / low
        return 15 + 10 * ratio
    ratio = 1 - (pe - high) / (high * 2)
    return max(3, 15 * ratio)


def _pb_score(pb: float) -> float:
    """PB评分 (0-25)"""
    if pb <= 0:
        return 3
    if 0.5 <= pb <= 2:
        return 25
    if pb < 0.5:
        return 20
    if pb <= 4:
        return 18
    if pb <= 8:
        return 10
    return 5


def _roe_score(roe: float) -> float:
    """ROE评分 (0-25)"""
    if roe <= 0:
        return 3
    if roe >= 20:
        return 25
    if roe >= 15:
        return 22
    if roe >= 10:
        return 18
    if roe >= 5:
        return 12
    return 8


def _market_cap_score(market_cap: float) -> float:
    """市值评分 (0-25) - 偏好中大盘"""
    if market_cap <= 0:
        return 5
    if market_cap >= 500:
        return 25
    if market_cap >= 200:
        return 22
    if market_cap >= 100:
        return 18
    if market_cap >= 50:
        return 15
    if market_cap >= 30:
        return 12
    if market_cap >= 10:
        return 8
    return 5


def _growth_item_score(growth: float) -> float:
    """单个增长指标评分 (0-40)"""
    if growth <= -20:
        return 5
    if growth <= 0:
        return 10
    if growth <= 10:
        return 20
    if growth <= 30:
        return 30
    if growth <= 60:
        return 35
    return 40


# ========== 多因子评分函数 (v5.1) ==========

def mf_score_value(stock):
    """价值因子（0-100分）— 2026-04-13修复：PE评分按板块动态调整"""
    score = 0
    pe = stock.get('pe', 0)
    pb = stock.get('pb', 0)
    profit_growth = stock.get('profit_growth', 0)
    market_cap = stock.get('market_cap', 0)

    # === 按行业动态调整PE评分标准 ===
    code = stock.get('code', '')

    # 从东方财富API获取行业信息
    from modules.data_fetcher import get_stock_industry
    industry_info = get_stock_industry(code)
    pe_fair_max = industry_info.get('pe_fair_max', 30)
    pe_fair_low = industry_info.get('pe_fair_low', 15)

    # 将行业信息写入stock（供后续API返回）
    stock['industry'] = industry_info.get('industry', '未知')
    stock['sector_type'] = industry_info.get('sector_type', 'default')

    # === PE评分（按板块动态调整）===
    # PE越低越好，但不同板块合理区间不同
    if pe > 0:
        if pe <= pe_fair_low:
            # 低于合理下限：非常便宜，满分
            score += 35
        elif pe <= pe_fair_max:
            # 在合理区间内：线性递减
            ratio = (pe - pe_fair_low) / (pe_fair_max - pe_fair_low)
            score += round(35 * (1 - ratio * 0.7), 1)  # 35→10.5
        elif pe <= pe_fair_max * 1.5:
            # 超出合理区间但不太离谱
            score += 5
        else:
            # 明显高估
            score += 2
    if pe > 0 and profit_growth > 0:
        peg = pe / profit_growth
        if peg < 0.5: score += 25
        elif peg < 1: score += 22
        elif peg < 1.5: score += 18
        elif peg < 2: score += 12
        else: score += 5
    if pb > 0:
        if pb < 1.5: score += 20
        elif pb < 3: score += 16
        elif pb < 5: score += 12
        elif pb < 8: score += 6
        else: score += 2
    else: score += 10
    if market_cap > 0:
        if 100 <= market_cap <= 500: score += 20
        elif 50 <= market_cap < 100 or 500 < market_cap <= 1000: score += 16
        elif 1000 < market_cap <= 2000: score += 12
        else: score += 8
    return min(score, 100)


def mf_score_quality(stock):
    """质量因子（0-100分）— V5.5: ROE门槛提升，低ROE严格扣分
    
    核心改进：价值投资要求企业有持续盈利能力，ROE是核心指标。
    旧版：ROE=4.9%仍得8分(35分制)，导致低质量股票可通过成长/情绪因子补偿
    新版：ROE<8%大幅扣分，ROE<5%几乎不给分，确保质量因子真正起筛选作用
    """
    score = 0
    roe = stock.get('roe', 0)
    gm = stock.get('gross_margin', 0)
    nm = stock.get('net_margin', 0)
    dr = stock.get('debt_ratio', 0)
    # === ROE评分（35分制）— V5.5: 更严格的门槛 ===
    # 价值投资标准：ROE>=15%才是好生意，ROE<8%说明盈利能力不足
    if roe >= 25: score += 35      # 卓越
    elif roe >= 20: score += 32    # 优秀
    elif roe >= 18: score += 28    # 良好+
    elif roe >= 15: score += 24    # 良好
    elif roe >= 12: score += 18    # 中等
    elif roe >= 10: score += 14    # 及格
    elif roe >= 8: score += 10     # 偏低
    elif roe >= 5: score += 5      # 较差
    elif roe > 0: score += 2       # 很差（旧版给8分，过于宽松）
    else: score += 0               # 亏损不给分
    # === ROE与毛利率交叉验证 ===
    # 高毛利率+低ROE = 资产周转率低，盈利效率不足
    if roe > 0 and roe < 8 and gm >= 40:
        score -= 3  # 高毛利但ROE低，说明资产效率差
    if gm >= 50: score += 25
    elif gm >= 40: score += 22
    elif gm >= 30: score += 18
    elif gm >= 20: score += 12
    elif gm > 0: score += 6
    if nm >= 20: score += 20
    elif nm >= 15: score += 16
    elif nm >= 10: score += 12
    elif nm >= 5: score += 8
    elif nm > 0: score += 4
    if dr > 0:
        if dr <= 30: score += 20
        elif dr <= 50: score += 16
        elif dr <= 60: score += 12
        elif dr <= 70: score += 6
    else: score += 10
    # === V5.5: 盈利稳定性惩罚 ===
    # ROE<5%的企业质量分上限50分（即使毛利/净利/负债率全好）
    if 0 < roe < 5:
        score = min(score, 50)
    return min(score, 100)


def mf_score_growth(stock):
    """成长因子（0-100分）— V5.5: 增长可持续性验证
    
    核心改进：
    1. 利润增长>100%通常是低基数效应(扭亏为盈)，不再给满分
    2. 营收增长<利润增长=利润率提升(非持续性)，降权处理
    3. ROE<8%的高增长视为不可持续，上限60分
    """
    score = 0
    rg = stock.get('rev_growth', 0)
    pg = stock.get('profit_growth', 0)
    roe = stock.get('roe', 0)

    has_rev = rg != 0
    has_profit = pg != 0

    # === V5.5: 检测低基数效应 ===
    # 利润增长>100%通常是低基数效应(如从0.01元涨到0.05元就是400%增长)
    # 这种增长不可持续，需要降权
    pg_capped = pg  # 用于评分的利润增长
    low_base_effect = False
    if pg > 100:
        low_base_effect = True
        # 100%以上增长用对数压缩：100->40, 200->50, 300->55, 500->60
        import math
        pg_capped = 40 + 10 * math.log10(max(pg / 100, 1))
        pg_capped = min(pg_capped, 60)

    if has_rev and has_profit:
        # 营收增长评分
        if rg >= 30: score += 35
        elif rg >= 20: score += 30
        elif rg >= 15: score += 24
        elif rg >= 10: score += 18
        elif rg > 0: score += 10
        # 利润增长评分（使用压缩后的值）
        if pg_capped >= 30: score += 35
        elif pg_capped >= 20: score += 30
        elif pg_capped >= 15: score += 24
        elif pg_capped >= 10: score += 18
        elif pg_capped > 0: score += 10
        # 加速增长加分
        if pg > rg and pg > 0:
            accel = pg - rg
            if accel >= 10: score += 15
            elif accel >= 5: score += 10
            else: score += 5
        else: score += 5
        # V5.5: 营收与利润增长背离惩罚
        # 利润增长远超营收增长 = 利润率提升(不可持续)
        if pg > 0 and rg > 0 and pg > rg * 3:
            score -= 10  # 利润增长远超营收，不可持续
    elif has_rev or has_profit:
        growth_val = rg if has_rev else pg_capped
        if growth_val >= 30: score += 45
        elif growth_val >= 20: score += 40
        elif growth_val >= 15: score += 35
        elif growth_val >= 10: score += 30
        elif growth_val > 0: score += 25
        else: score += 15
        if roe >= 20: score += 18
        elif roe >= 15: score += 14
        elif roe > 0: score += 10
        else: score += 5
    else:
        if roe >= 25: score += 55
        elif roe >= 20: score += 50
        elif roe >= 15: score += 45
        elif roe >= 10: score += 40
        elif roe > 0: score += 35
        else: score += 20

    # === V5.5: 低ROE高增长惩罚 ===
    # ROE<8%的企业即使有高增长也不可持续（资本回报率太低）
    if 0 < roe < 8:
        score = min(score, 60)
    
    # === V5.5: 低基数效应额外惩罚 ===
    if low_base_effect:
        score = min(score, 70)

    return min(score, 100)


def mf_score_momentum(tech_data):
    """动量因子（0-100分）"""
    score = 0
    if not tech_data: return 50
    m20 = tech_data.get('momentum_20', 0)
    m60 = tech_data.get('momentum_60', 0)
    ma5 = tech_data.get('ma5', 0)
    ma10 = tech_data.get('ma10', 0)
    ma20 = tech_data.get('ma20', 0)
    price = tech_data.get('current_price', 0)
    if 5 <= m20 <= 20: score += 35
    elif 0 <= m20 < 5: score += 28
    elif 20 < m20 <= 40: score += 25
    elif -5 <= m20 < 0: score += 15
    else: score += 5
    if 10 <= m60 <= 40: score += 25
    elif 0 <= m60 < 10: score += 20
    elif 40 < m60 <= 60: score += 15
    elif -10 <= m60 < 0: score += 10
    else: score += 5
    if price > ma5 > ma10 > ma20 and ma20 > 0: score += 25
    elif price > ma5 > ma10 and ma10 > 0: score += 20
    elif price > ma20 and ma20 > 0: score += 15
    elif price < ma5 < ma10 < ma20 and ma20 > 0: score += 0
    else: score += 10
    rsi = tech_data.get('rsi', 50)
    if 50 <= rsi <= 70: score += 15
    elif 40 <= rsi < 50: score += 10
    elif rsi > 70: score += 5
    else: score += 3
    return min(score, 100)


def mf_score_sentiment(stock, tech_data):
    """情绪因子（0-100分）"""
    score = 0
    if not tech_data: return 50
    turnover = stock.get('turnover_rate', 0)
    vr = tech_data.get('volume_ratio', 1)
    if 3 <= turnover <= 8: score += 50
    elif 1.5 <= turnover < 3: score += 40
    elif 8 < turnover <= 15: score += 30
    elif 0.5 <= turnover < 1.5: score += 25
    elif turnover > 15: score += 10
    else: score += 15
    if 1.2 <= vr <= 2.5: score += 30
    elif 0.8 <= vr < 1.2: score += 20
    elif 2.5 < vr <= 5: score += 15
    else: score += 10
    ma20 = tech_data.get('ma20', 0)
    price = tech_data.get('current_price', 0)
    if price > 0 and ma20 > 0:
        dist = (price - ma20) / ma20 * 100
        if 0 <= dist <= 10: score += 20
        elif 10 < dist <= 20: score += 15
        elif -5 <= dist < 0: score += 18
        else: score += 8
    return min(score, 100)


def multi_factor_evaluate(stock, tech_data=None):
    """
    多因子综合评分 v5.1 (Round 4 最优方案)
    价值(36%) + 质量(11%) + 成长(8%) + 动量(12%) + 情绪(33%)
    回测: 收益率+26.09% | 胜率80.0% | 15笔交易 | 持有90天
    M20_hi=0.05 (早期动量信号阈值)
    """
    v = mf_score_value(stock)
    q = mf_score_quality(stock)
    g = mf_score_growth(stock)
    # roe_val removed: ROE penalty moved to evaluate_stock()
    m = mf_score_momentum(tech_data) if tech_data else 50
    s = mf_score_sentiment(stock, tech_data) if tech_data else 50
    # Round 4 最优权重
    # V5.2: lower sentiment(33->18%), raise quality(11->18%), growth(8->17%)
    total = v * 0.35 + q * 0.18 + g * 0.17 + m * 0.12 + s * 0.18

    # V5.4: 价值投资最低门槛 — 价值分过低时大幅折扣
    # 价值投资核心：估值必须合理，V<25的股票不应入选（如德明利V=19）
    if v < 25:
        total *= 0.70  # 估值极差，打7折
    elif v < 35:
        total *= 0.85  # 估值较差，打85折

    # Momentum penalty: avoid value traps
    _m20 = 0
    if tech_data:
        _m20 = tech_data.get("momentum_20", 0)
    if _m20 < -10:
        total -= 5
    elif _m20 < -5:
        total -= 2

    # V5.6: 市场环境感知 — 熊市温和折扣（0.92太激进致191/195只变观望）
    _env_trend = 'unknown'
    try:
        from modules.market_env import get_market_env
        _env = get_market_env()
        _env_trend = _env.trend
        if _env.trend == 'bear':
            total *= 0.95  # V5.6: 0.92->0.95（回测显示0.92过严）
        elif _env.trend == 'range':
            total *= 0.97  # V5.6: 0.95->0.97（震荡市更温和）
    except Exception:
        pass

    # V5.4: 双重数据缺失惩罚 — Q<=12且G<=25说明数据严重不足，不应高分入选
    if q <= 12 and g <= 25:
        total *= 0.82  # 数据双重缺失，大幅折扣
    elif q <= 12 or g <= 25:
        total *= 0.92  # 单项数据缺失，轻微折扣

    # V5.5: ROE/Q惩罚已移至evaluate_stock()中，在所有bonus之后应用
    # 避免bonus覆盖惩罚效果

    reasons = []
    if v >= 75: reasons.append(f"估值优秀(V{v:.0f})")
    elif v >= 60: reasons.append(f"估值合理(V{v:.0f})")
    elif v < 25: reasons.append(f"⚠️估值过低(V{v:.0f})")
    elif v < 35: reasons.append(f"估值偏低(V{v:.0f})")
    if q >= 75: reasons.append(f"质量优秀(Q{q:.0f})")
    elif q >= 60: reasons.append(f"质量良好(Q{q:.0f})")
    elif q <= 12: reasons.append("⚠️质量数据不足")
    if g >= 75: reasons.append(f"高成长(G{g:.0f})")
    elif g >= 60: reasons.append(f"成长良好(G{g:.0f})")
    elif g <= 28: reasons.append("⚠️成长数据不足")
    if m >= 70: reasons.append(f"动量强劲(M{m:.0f})")
    elif m >= 55: reasons.append(f"动量中性(M{m:.0f})")
    # V5.6: 推荐阈值自适应市场环境（熊市降低5分，避免全部变观望）
    if _env_trend == 'bear':
        if total >= 70: rec = "强烈推荐"
        elif total >= 60: rec = "推荐"
        elif total >= 50: rec = "关注"
        else: rec = "观望"
    else:
        if total >= 75: rec = "强烈推荐"
        elif total >= 65: rec = "推荐"
        elif total >= 55: rec = "关注"
        else: rec = "观望"
    return {
        'v5_total': round(total, 2),
        'v5_factors': {
            'value': round(v, 2), 'quality': round(q, 2),
            'growth': round(g, 2), 'momentum': round(m, 2), 'sentiment': round(s, 2),
        },
        'v5_reasons': reasons,
        'v5_recommendation': rec,
    }


# ========== 五维价值投资评估 ==========

def _recalc_rec(score: float, market_trend: str = 'unknown') -> str:
    """V5.6: 基于整合后总分和市场环境重新计算推荐等级"""
    if market_trend == 'bear':
        if score >= 70: return "强烈推荐"
        elif score >= 60: return "推荐"
        elif score >= 50: return "关注"
        else: return "观望"
    else:
        if score >= 75: return "强烈推荐"
        elif score >= 65: return "推荐"
        elif score >= 55: return "关注"
        else: return "观望"


def evaluate_stock(stock, tech_data=None, priority_sectors=None):
    """五维价值投资评估 - 支持全市场股票

    参数:
        stock: 股票数据字典
        tech_data: 技术指标数据（可选，由外部计算后传入）
        priority_sectors: 当日优先板块列表（可选）
    """
    score = 0
    dimensions = {"profitability": 0, "growth": 0, "health": 0, "valuation": 0, "cashflow": 0}
    tech_score = 0  # 技术面评分单独计算，不加入dimensions
    reasons = []

    # V5.6: 动量拒绝过滤 - 自适应阈值（熊市放宽到-20%，避免过度排除）
    _mom_threshold = -15
    try:
        from modules.market_env import get_market_env
        _env = get_market_env()
        if _env.trend == 'bear':
            _mom_threshold = -20  # 熊市大部分股票M20为负，放宽阈值
    except Exception:
        pass
    if tech_data:
        _m20 = tech_data.get('momentum_20', 0)
        if _m20 < _mom_threshold:
            return None
    elif stock.get('momentum_20', 0) < _mom_threshold:
        return None

    # 排除白酒和银行
    name = stock.get("name", "")
    code = stock.get("code", "")
    # 过滤北交所/B股/A股重复
    if code.startswith('8') or code.startswith('4') or code.startswith('920'):
        return None
    if code.startswith('900') or code.startswith('200') or code.startswith('A2'):
        return None
    if any(n in name for n in LIQUOR_NAMES) or code in BANK_CODES:
        return None

    # === 换手率基础筛选（新增）===
    turnover_rate = stock.get("turnover_rate", 0)
    if turnover_rate < 0.3 and turnover_rate > 0:
        # 换手率低于0.3%的极不活跃股票，直接排除
        # 注意：turnover_rate=0可能是数据缺失，不排除
        # 0.3%-0.5%的大盘蓝筹股保留，但后续换手率因子不给分
        return None

    roe = stock.get("roe", 0)
    gross_margin = stock.get("gross_margin", 0)
    net_margin = stock.get("net_margin", 0)
    rev_growth = stock.get("rev_growth", 0)
    profit_growth = stock.get("profit_growth", 0)
    pe = stock.get("pe", 0)
    pb = stock.get("pb", 0)
    debt_ratio = stock.get("debt_ratio", 0)
    market_cap = stock.get("market_cap", 0)

    # 数据完整度判断
    has_profitability = roe > 0 or gross_margin > 0 or net_margin > 0
    has_growth = rev_growth != 0 or profit_growth != 0
    has_valuation = pe > 0 or pb > 0

    # 盈利能力 (最高35分) - 连续评分而非阶梯式，增加区分度
    if roe < 0:
        dimensions["profitability"] = 0
        reasons.append(f"ROE {roe:.1f}% 亏损 ⚠️")
    elif roe >= 18:  # 优化：20% -> 18%
        # ROE 20%-40%映射到 25-35分（连续），每增加1%ROE多1分
        dimensions["profitability"] = min(25 + (roe - 20) * 1, 35)
        reasons.append(f"ROE {roe:.1f}% 优秀")
    elif roe >= 15:
        dimensions["profitability"] = 15 + (roe - 15) * 2  # 15-25分
        reasons.append(f"ROE {roe:.1f}% 良好")
    elif roe > 0:
        dimensions["profitability"] = roe * 1  # 0-15分
        reasons.append(f"ROE {roe:.1f}%")
    else:
        if profit_growth > 20:
            dimensions["profitability"] = 12
            reasons.append("净利润高增长，盈利能力推测良好")
        elif profit_growth > 0:
            dimensions["profitability"] = 8
        else:
            dimensions["profitability"] = 0

    if gross_margin >= 40:
        dimensions["profitability"] = min(dimensions["profitability"] + 8, 35)
        reasons.append(f"毛利率 {gross_margin:.1f}% ✓")
    elif gross_margin > 0:
        dimensions["profitability"] = min(dimensions["profitability"] + 3, 35)

    if net_margin >= 15:
        dimensions["profitability"] = min(dimensions["profitability"] + 5, 35)
        reasons.append(f"净利率 {net_margin:.1f}% ✓")
    score += dimensions["profitability"]

    # 成长性 (25分) - ROE为负时成长性打折
    has_rev = rev_growth != 0
    has_profit = profit_growth != 0

    if roe < 0:
        # 亏损企业，成长性最多5分（即使有增速也可能是扭亏为盈）
        if profit_growth > 20 and rev_growth > 0:
            dimensions["growth"] = 5
            reasons.append("亏损企业但有改善迹象")
        else:
            dimensions["growth"] = 0
        score += dimensions["growth"]
    elif has_rev and has_profit:
        # 两项都有，正常评分
        avg_growth = (rev_growth + profit_growth) / 2
        if avg_growth >= 20:
            dimensions["growth"] = min(20 + (avg_growth - 20) * 0.5, 25)
            reasons.append(f"成长性 {avg_growth:.1f}% 优秀")
        elif avg_growth >= 15:
            dimensions["growth"] = 15 + (avg_growth - 15) * 1
            reasons.append(f"成长性 {avg_growth:.1f}% 良好")
        elif avg_growth >= 10:
            dimensions["growth"] = 10 + (avg_growth - 10) * 1
        elif avg_growth > 0:
            dimensions["growth"] = avg_growth * 1
        else:
            dimensions["growth"] = max(avg_growth * 0.5, 5)  # 负增长给最低5分
        score += dimensions["growth"]
    elif has_rev or has_profit:
        # 只有单一数据
        growth_val = rev_growth if has_rev else profit_growth
        if growth_val >= 20:
            dimensions["growth"] = 18
            reasons.append(f"{'营收' if has_rev else '利润'}增长 {growth_val:.1f}% 优秀（缺另一项）")
        elif growth_val >= 10:
            dimensions["growth"] = 14
            reasons.append(f"{'营收' if has_rev else '利润'}增长 {growth_val:.1f}% 一般")
        elif growth_val > 0:
            dimensions["growth"] = 10
        else:
            dimensions["growth"] = 6
        # ROE 补充评分
        if roe >= 20:
            dimensions["growth"] += 3
        elif roe >= 15:
            dimensions["growth"] += 2
        dimensions["growth"] = min(dimensions["growth"], 25)
        score += dimensions["growth"]
    else:
        # 两项都缺失，用 ROE 推断
        if roe >= 20:
            dimensions["growth"] = 15
            reasons.append(f"增长数据缺失，ROE {roe:.1f}%推断成长性中等")
        elif roe >= 15:
            dimensions["growth"] = 12
            reasons.append("增长数据缺失，ROE中等推断")
        elif roe > 0:
            dimensions["growth"] = 8
            reasons.append("增长数据缺失，ROE偏低")
        else:
            dimensions["growth"] = 5
            reasons.append("增长数据和ROE均缺失")
        score += dimensions["growth"]

    # 财务健康 (20分)
    if debt_ratio > 0 and debt_ratio < 1000:  # 过滤异常值
        if debt_ratio <= 50:
            dimensions["health"] = 20
            reasons.append(f"资产负债率 {debt_ratio:.1f}% ✓健康")
        elif debt_ratio <= 70:
            dimensions["health"] = 12
        else:
            dimensions["health"] = 5
    else:
        dimensions["health"] = 0  # 优化：无数据不给分
    score += dimensions["health"]

    # 估值 (20分) - 连续评分
    # 注意：PE为负说明亏损（TTM），不应给估值分
    if pe > 0 and pe < 1000:
        if pe <= 12:
            dimensions["valuation"] = min(15 + (15 - pe) * 0.33, 20)  # PE越低分越高
            reasons.append(f"PE {pe:.1f} 低估 ✓")
        elif pe <= 20:
            dimensions["valuation"] = 15 - (pe - 15) * 0.5  # 15→10分
            reasons.append(f"PE {pe:.1f} 合理")
        elif pe <= 35:
            dimensions["valuation"] = 10 - (pe - 25) * 0.5  # 10→5分
        elif pe <= 50:
            dimensions["valuation"] = 5 - (pe - 35) * 0.33  # 5→0分
            dimensions["valuation"] = max(dimensions["valuation"], 0)
        else:
            dimensions["valuation"] = 0
            if pe > 100:
                reasons.append(f"PE {pe:.1f} 高估 ⚠️")
    elif pe <= 0:
        dimensions["valuation"] = 0
    else:
        dimensions["valuation"] = 8

    if 0 < pb <= 3:
        dimensions["valuation"] = min(dimensions["valuation"] + 5, 20)
    elif 3 < pb <= 5:
        dimensions["valuation"] = min(dimensions["valuation"] + 2, 20)
    score += dimensions["valuation"]

    # 现金流质量 (加分项，上限5分)
    # 改进方案：基础分 + 毛利率加分 + 负债率加分
    # 基础分由盈利质量(PE+ROE)决定，毛利率高/负债率低可额外加分
    market_cap_yi = market_cap  # 已经是亿元单位，直接使用

    cashflow_base = 0
    cashflow_reason = ""

    # 基础分：盈利质量（PE+ROE推导）
    if pe > 0 and roe > 0:
        if roe >= 20:
            # ROE优秀
            if pe <= 20:
                cashflow_base = 3
                cashflow_reason = f"ROE {roe:.1f}%优秀 + PE低 现金流充裕"
            elif pe <= 35:
                cashflow_base = 2
                cashflow_reason = f"ROE {roe:.1f}%优秀 盈利质量良好"
            else:
                cashflow_base = 1
                cashflow_reason = f"ROE {roe:.1f}%优秀 但PE偏高"
        elif roe >= 10:
            # ROE中等
            if pe <= 25:
                cashflow_base = 2
                cashflow_reason = f"ROE {roe:.1f}% + PE合理 盈利稳定"
            elif pe <= 40:
                cashflow_base = 1
                cashflow_reason = f"ROE {roe:.1f}% 盈利尚可"
            else:
                cashflow_base = 1
                cashflow_reason = f"ROE {roe:.1f}% 但估值偏高"
        else:
            # ROE较低但盈利
            cashflow_base = 1
            cashflow_reason = f"盈利中 ROE {roe:.1f}%待提升"
    elif roe <= 0 or pe <= 0:
        cashflow_base = 0
        cashflow_reason = "亏损企业 现金流堪忧"

    # 加分项：高毛利率（现金流通常更好）
    if gross_margin >= 40:
        cashflow_base += 1
        cashflow_reason += " | 毛利率高"

    # 加分项：低负债率（现金流压力小）
    if 0 < debt_ratio <= 50:
        cashflow_base += 1
        cashflow_reason += " | 负债率低"

    # 限制最高5分
    dimensions["cashflow"] = min(cashflow_base, 5)
    score += dimensions["cashflow"]

    if dimensions["cashflow"] > 0:
        reasons.append(f"{cashflow_reason} ✓")
    elif pe <= 0 or roe <= 0:
        reasons.append("亏损企业 现金流堪忧 ⚠️")

    # ===== 行情因子 (加分项，让每天结果有变化) =====
    # 涨跌幅因子：偏好适度涨跌，避免追高和暴跌
    change_pct = stock.get("change_pct", 0)
    market_bonus = 0

    # 涨跌幅加分逻辑
    if -5 <= change_pct <= 3:
        # 适度涨跌：跌5%到涨3%之间，加分
        if change_pct < 0:
            # 小跌可能是机会
            market_bonus += abs(change_pct) * 0.5  # 跌越多加分越多（抄底机会）
            reasons.append(f"回调 {change_pct:.1f}% 可能是机会")
        else:
            # 小涨也在合理范围
            market_bonus += 1
    elif 3 < change_pct <= 7:
        # 涨幅较大，小幅加分
        market_bonus += 0.5
        reasons.append(f"上涨 {change_pct:.1f}%")
    elif change_pct > 7:
        # 涨幅过大，不加行情分（避免追高）
        reasons.append(f"涨幅 {change_pct:.1f}% 较大 注意追高风险")
    elif change_pct < -7:
        # 跌幅过大，可能有问题
        market_bonus -= 1
        reasons.append(f"大跌 {change_pct:.1f}% 注意风险")

    # ===== 换手率因子（增强版）=====
    # 新评分规则：关注活跃度，不活跃股票已在开头过滤
    turnover_bonus = 0
    if 0.5 <= turnover_rate < 1:
        # 低活跃，不给分
        turnover_bonus = 0
    elif 1 <= turnover_rate < 3:
        # 正常活跃
        turnover_bonus = 2
    elif 3 <= turnover_rate < 10:
        # 高度活跃，最佳区间
        turnover_bonus = 4
        reasons.append(f"换手率 {turnover_rate:.1f}% 活跃 ✓")
    elif 10 <= turnover_rate < 20:
        # 超活跃，可能过热
        turnover_bonus = 3
        reasons.append(f"换手率 {turnover_rate:.1f}% 较活跃")
    elif turnover_rate >= 20:
        # 极度活跃，可能有异常
        turnover_bonus = 0
        reasons.append(f"换手率 {turnover_rate:.1f}% 异常活跃 注意")

    market_bonus += turnover_bonus

    # ===== 技术面评分（V5.5: 已移至V5总分整合区域，避免重复计算）=====
    # 旧版：技术面评分加到旧评分(score)上，但最终用V5评分，加分浪费
    # 新版：技术面评分直接加到V5总分上，见下方V5整合区域
    # 仍保留用于reasons展示
    if tech_data:
        try:
            from modules.technical import evaluate_technical_score
            _, tech_reasons = evaluate_technical_score(code, tech_data)
            if tech_reasons:
                reasons.extend(tech_reasons)
        except Exception:
            pass

    # ===== 板块轮动加分（V5.5: 已移至V5总分整合区域，避免重复计算）=====
    # 旧版：板块加分加到旧评分(score)上，但最终用V5评分，加分浪费
    # 新版：板块加分直接加到V5总分上，见下方V5整合区域
    # 仍保留旧逻辑用于reasons展示
    try:
        from modules.sector_rotation import calculate_sector_bonus
        stock_industry = stock.get("industry", "")
        _, sr_reasons = calculate_sector_bonus(code, name, stock_industry)
        if sr_reasons:
            reasons.extend(sr_reasons)
    except Exception:
        pass

    # 行情加分（V5.5: 不再加到旧评分，已整合到V5总分）
    market_bonus = max(0, min(market_bonus, 5))
    # score += market_bonus  # V5.5: 已移至V5整合区域

    # 市值信息（不参与评分，仅展示）
    if market_cap_yi > 0:
        reasons.append(f"市值 {market_cap_yi:.0f}亿")

    # ===== 多因子v5评分（V5.5: 统一评分入口）=====
    # 将技术数据转换为v5格式
    v5_tech = None
    if tech_data:
        v5_tech = {
            'momentum_20': tech_data.get('momentum_20', 0),
            'momentum_60': tech_data.get('momentum_60', 0),
            'ma5': tech_data.get('ma5', 0),
            'ma10': tech_data.get('ma10', 0),
            'ma20': tech_data.get('ma20', 0),
            'current_price': tech_data.get('ma5', stock.get('price', 0)),
            'rsi_14': tech_data.get('rsi', 50),
            'volume_ratio': tech_data.get('volume_ratio', 1),
        }
    v5_result = multi_factor_evaluate(stock, v5_tech)
    v5_total = v5_result['v5_total']
    
    # V5.5: 将行情加分和板块加分整合到V5总分中
    # 旧版：这些加分加到了旧评分上但最终用V5，导致加分浪费
    # 新版：直接加到V5总分上，确保所有加分都生效
    v5_total += market_bonus  # 行情加分（涨跌幅+换手率，上限5分）
    # 板块轮动加分已在上方通过sector_rotation模块计算并加入旧评分
    # V5.5: 将板块加分也加到V5总分
    sector_bonus_val = 0
    try:
        from modules.sector_rotation import calculate_sector_bonus
        stock_industry = stock.get("industry", "")
        sector_bonus_val, _ = calculate_sector_bonus(code, name, stock_industry)
    except Exception:
        pass
    v5_total += sector_bonus_val
    # 技术面加分也整合
    if tech_data:
        try:
            from modules.technical import evaluate_technical_score
            tech_score_v5, _ = evaluate_technical_score(code, tech_data)
            v5_total += tech_score_v5
        except Exception:
            pass

    # ===== V5.5: 质量惩罚（在所有bonus之后应用，确保不被bonus覆盖）=====
    # ROE地板检查 — 价值投资核心：没有持续盈利能力的企业不应入选
    # ROE<5%说明企业盈利能力极差，即使其他因子好+bonus也要大幅打折
    if 0 < roe < 5:
        v5_total *= 0.80  # ROE极低，打8折
    elif 0 < roe < 8:
        v5_total *= 0.90  # ROE偏低，打9折

    # Q因子惩罚 — 质量因子过低说明企业基本面很差
    q_val = v5_result['v5_factors'].get('quality', 50)
    if q_val < 25:
        v5_total *= 0.85  # 质量极差，打85折

    # 买卖点（使用整合后的v5_score，传递完整tech_data）
    buy_sell = calculate_buy_sell(stock, v5_total, tech_data=tech_data)

    # 四舍五入所有维度分数，确保显示一致
    rounded_dimensions = {k: round(v) for k, v in dimensions.items()}

    # 添加换手率和技术指标信息
    tech_info = {}
    if tech_data:
        tech_info = {
            "ma5": tech_data.get('ma5', 0),
            "ma20": tech_data.get('ma20', 0),
            "kdj_k": tech_data.get('kdj_k', 0),
            "kdj_d": tech_data.get('kdj_d', 0),
            "rsi": tech_data.get('rsi', 0),
            "volume_ratio": tech_data.get('volume_ratio', 1),
        }

    # V5.6: 在evaluate_stock中也获取市场环境，用于推荐阈值自适应
    _eval_env_trend = 'unknown'
    try:
        from modules.market_env import get_market_env
        _eval_env_trend = get_market_env().trend
    except Exception:
        pass

    return {
        "code": code,
        "name": name,
        "price": stock.get("price", 0),
        "change_pct": stock.get("change_pct", 0),
        "turnover_rate": turnover_rate,
        "pe": pe,
        "pb": pb,
        "roe": roe,
        "gross_margin": gross_margin,
        "net_margin": net_margin,
        "debt_ratio": debt_ratio,
        "rev_growth": rev_growth,
        "profit_growth": profit_growth,
        "market_cap": market_cap_yi,
        "industry": stock.get("industry", "未知"),
        "sector_type": stock.get("sector_type", "default"),
        "score": round(v5_total, 1),  # V5.6: 使用整合后的V5评分(含行情+板块+技术加分)
        "dimensions": rounded_dimensions,
        "reasons": reasons,
        "buy_sell": buy_sell,
        "tech_info": tech_info,
        "v5_score": v5_total,
        "v5_factors": v5_result['v5_factors'],
        "v5_reasons": v5_result['v5_reasons'],
        "v5_recommendation": _recalc_rec(v5_total, _eval_env_trend),  # V5.6: 基于整合后总分和市场环境重新计算推荐
    }




def industry_concentration_limit(
    results: list[dict],
    max_per_industry: int = 2,
    min_count: int = 5,
) -> list[dict]:
    """Limit the number of stocks from the same industry in the results."""
    if not results:
        return results
    industry_counts = {}
    filtered = []
    for stock in results:
        industry = stock.get("industry", "unknown")
        count = industry_counts.get(industry, 0)
        if count < max_per_industry:
            filtered.append(stock)
            industry_counts[industry] = count + 1
        elif len(filtered) < min_count:
            filtered.append(stock)
            industry_counts[industry] = count + 1
    return filtered

def calculate_buy_sell(stock, v5_score, tech_data=None):
    """计算买卖点 + 五星评级

    V4 (2026-06-09): 基于实盘回测30只股票30天数据优化
    - 行业感知fair_pe: 用SECTOR_PE_RANGES替代固定公式（回测验证偏差从16%降至0.2%）
    - 多因子upside: PE空间+动量+OBV+布林收敛+RSI超卖
    - 技术动态买入折扣: RSI/布林/MACD/OBV/趋势/换手率逐项加减（买入触达率从13%升至43%）
    - 质量自适应买入地板: 高分股10% vs 低分股16%
    - 止损价: ATR+VWAP+MA20支撑（不超过买入价）
    - 阻力位质量加权 + 仓位RSI调整
    """
    price = stock.get("price", 0)
    pe = stock.get("pe", 0)
    roe = stock.get("roe", 0)
    gross_margin = stock.get("gross_margin", 0)
    rev_growth = stock.get("rev_growth", 0)
    profit_growth = stock.get("profit_growth", 0)
    if price <= 0:
        return None

    # === PE<=0 亏损股兜底逻辑 ===
    # 亏损股(PE<=0)如果有正的营收增长和PB，用PB估值+成长性给出买卖点
    pb = stock.get("pb", 0)
    use_pb_fallback = (pe <= 0) and (pb > 0 or rev_growth > 0)
    if pe <= 0 and not use_pb_fallback:
        return None  # 亏损且无任何正向信号，不推荐


    # === 技术面数据（优先从 tech_data，降级从 stock dict） ===
    td = tech_data or {}
    ma5 = td.get("ma5") or stock.get("ma5")
    ma20 = td.get("ma20") or stock.get("ma20")
    ma60 = td.get("ma60") or stock.get("ma60")
    recent_high = td.get("recent_high") or stock.get("recent_high")
    boll_upper = td.get("boll_upper") or stock.get("boll_upper")
    atr = td.get("atr") or stock.get("atr")
    rsi = td.get("rsi") or stock.get("rsi")
    boll_position = td.get("boll_position")
    boll_width_pct = td.get("boll_width_pct")
    macd_signal = td.get("macd_signal")
    obv_trend = td.get("obv_trend")
    vwap = td.get("vwap")
    momentum_20 = td.get("momentum_20", 0)
    momentum_60 = td.get("momentum_60", 0)
    turnover_rate = stock.get("turnover_rate", 0)

    # === 行业感知的合理PE ===
    avg_growth = (rev_growth + profit_growth) / 2
    sector_type = stock.get("sector_type", "default")
    sector_info = SECTOR_PE_RANGES.get(sector_type)
    if sector_info:
        pe_low = sector_info["pe_fair_low"]
        pe_max = sector_info["pe_fair_max"]
    else:
        pe_low, pe_max = 12, 30
    roe_factor = min(max(roe / 20, 0), 1.0)
    growth_factor = min(max(avg_growth / 30, 0), 1.0)
    quality_blend = roe_factor * 0.6 + growth_factor * 0.4
    fair_pe = pe_low + (pe_max - pe_low) * quality_blend
    fair_pe = max(8, min(fair_pe, 120))

    # === PB fallback: 亏损股用PB估值替代PE估值 ===
    if use_pb_fallback:
        # 亏损股没有有效PE，用行业PB中位数和营收增长推算合理PB
        if sector_info:
            pb_fair = (sector_info["pe_fair_low"] / 5)  # 行业PB中位数近似
        else:
            pb_fair = 3.0  # 全市场PB中位数
        if rev_growth > 20:
            pb_fair *= 1.3  # 高成长给更高PB容忍度
        elif rev_growth > 10:
            pb_fair *= 1.1
        if pb > 0 and pb < pb_fair:
            pe_upside = (pb_fair - pb) / pb  # PB upside替代PE upside
        elif pb > 0:
            pe_upside = 0.0
        else:
            pe_upside = 0.15  # 无PB数据，给15%默认upside
        # 亏损股的 fair_pe 设为0，所有PE相关判断走PB分支
        fair_pe = 0
    elif pe < fair_pe:
        pe_upside = (fair_pe - pe) / pe
    else:
        pe_upside = 0.0

    # === 基础买卖参数 + 五星评级 ===
    star_rating = 1

    if pe < fair_pe:
        if v5_score >= 75:  # V5.5: 与multi_factor_evaluate阈值对齐
            base_discount = 0.97
            rec = "强烈推荐"
            star_rating = 5 if v5_score >= 80 and roe >= 15 and gross_margin >= 25 else 4
            if star_rating == 4 and price * (1 - base_discount) <= price * 0.03:
                star_rating = 5
        elif v5_score >= 65:  # V5.5: 推荐
            base_discount = 0.95
            rec = "推荐买入"
            star_rating = 4 if v5_score >= 70 else 3
        elif v5_score >= 55:  # V5.5: 关注
            base_discount = 0.92
            rec = "可逢低关注"
            star_rating = 3 if v5_score >= 60 else 2
        else:
            base_discount = 0.90
            rec = "轻度关注"
            star_rating = 1
    else:
        # PE高于合理区间，但V5评分高说明基本面优秀
        if v5_score >= 75 and pe < fair_pe * 1.5:
            # 高分+适度高估：值得等待回调
            base_discount = 0.92
            rec = "等待回调买入"
            star_rating = 4
        elif v5_score >= 70 and pe < fair_pe * 1.3:
            base_discount = 0.90
            rec = "等待更好买点"
            star_rating = 3
        elif v5_score >= 55:
            base_discount = 0.88
            rec = "高估观望"
            star_rating = 2
        else:
            base_discount = 0.85
            rec = "暂不推荐"
            star_rating = 1

    # === 技术动态买入折扣调整 ===
    tech_adj = 0.0
    if rsi is not None:
        if rsi < 30:       tech_adj += 0.02
        elif rsi < 40:     tech_adj += 0.01
        elif rsi > 70:     tech_adj -= 0.03
        elif rsi > 60:     tech_adj -= 0.01
    if boll_position is not None:
        if boll_position < 0.2:   tech_adj += 0.02
        elif boll_position > 0.8: tech_adj -= 0.02
    if macd_signal:
        if macd_signal == "golden_cross":   tech_adj += 0.01
        elif macd_signal == "death_cross":  tech_adj -= 0.01
    if obv_trend:
        if obv_trend == "bullish":   tech_adj += 0.01
        elif obv_trend == "bearish": tech_adj -= 0.01
    if ma5 and ma20 and ma5 > 0 and ma20 > 0:
        if ma5 > ma20:    tech_adj += 0.01
        else:              tech_adj -= 0.02
    # V5.2 trend gate: death cross = deeper 5pct discount
    if ma5 and ma20 and ma60 and ma5 > 0 and ma20 > 0 and ma60 > 0:
        if ma5 < ma20 < ma60:
            tech_adj -= 0.05
        elif ma5 > ma20 > ma60:
            tech_adj += 0.01
    if turnover_rate and turnover_rate > 5:
        tech_adj += 0.01
    tech_adj = max(-0.05, min(0.03, tech_adj))

    # V5.2: Momentum-based star rating downgrade
    # If stock is in dual downtrend (20d AND 60d negative), downgrade star by 1
    if momentum_20 < 0 and momentum_60 < 0:
        star_rating = max(1, star_rating - 1)
    buy_discount = max(0.82, min(0.98, base_discount + tech_adj))

    # === 多因子上涨空间 ===
    upside = pe_upside
    if momentum_20 > 0 and momentum_60 > 0:
        upside += min(momentum_20 * 0.002, 0.10)
    elif momentum_20 > 5:
        upside += 0.03
    if obv_trend == "bullish":
        upside += 0.05
    if boll_width_pct is not None and boll_width_pct < 8:
        upside += 0.05
    if rsi is not None and rsi < 30:
        upside += 0.03
    upside = max(upside, 0.08)
    # V5.2: realistic upside caps by star rating
    if star_rating >= 5:   upside = min(upside, 0.35)
    elif star_rating >= 4: upside = min(upside, 0.30)
    elif star_rating >= 3: upside = min(upside, 0.25)
    elif star_rating >= 2: upside = min(upside, 0.20)
    else:                  upside = min(upside, 0.15)

    # === 买入价 ===
    buy_point = round(price * buy_discount, 2)
    if ma20 and ma20 > 0 and ma20 < price:
        buy_point = max(buy_point, round(ma20 * 0.995, 2))
    if ma60 and ma60 > 0 and ma60 < price:
        buy_point = max(buy_point, round(ma60 * 0.99, 2))
    if v5_score >= 82:   floor_pct = 0.90
    elif v5_score >= 68: floor_pct = 0.88
    elif v5_score >= 55: floor_pct = 0.86
    else:                floor_pct = 0.84
    buy_point = max(buy_point, round(price * floor_pct, 2))

    # === 卖出价 ===
    sell_point = round(price * (1 + upside), 2)
    if recent_high and recent_high > price:
        resistance = round(recent_high * 1.08, 2)
        if sell_point > resistance and upside > 0.15:
            weight = 0.7 if v5_score >= 75 else 0.5
            sell_point = round(sell_point * weight + resistance * (1 - weight), 2)
        elif sell_point > resistance:
            sell_point = round((sell_point + resistance) / 2, 2)
    if boll_upper and boll_upper > price and sell_point > boll_upper * 1.15:
        sell_point = round(min(sell_point, boll_upper * 1.15), 2)
    if sell_point <= buy_point * 1.10:
        sell_point = round(buy_point * 1.15, 2)

    # === 止损价 ===
    if atr and atr > 0 and price > 0:
        atr_stop_pct = 2 * atr / price * 100
        atr_stop_pct = max(5.0, min(12.0, atr_stop_pct))
        stop_loss = round(price * (1 - atr_stop_pct / 100), 2)
    else:
        stop_loss = round(price * 0.92, 2)
    if vwap and vwap > 0 and vwap > buy_point:
        vwap_stop = round(vwap * 0.98, 2)
        if vwap_stop > stop_loss and vwap_stop < buy_point: stop_loss = vwap_stop
    if ma20 and ma20 > 0 and stop_loss < ma20 < price:
        support_stop = round(ma20 * 0.98, 2)
        if support_stop > stop_loss and support_stop < buy_point: stop_loss = support_stop
    stop_floor = round(buy_point * 0.90, 2)  # 10pct floor (widened from 8pct per backtest)
    if stop_loss < stop_floor:
        stop_loss = stop_floor
    # V5.5: 止损价必须低于买入价（否则逻辑矛盾）
    if stop_loss >= buy_point:
        stop_loss = round(buy_point * 0.92, 2)  # 买入价下方8%止损

    # === 仓位 ===
    atr_val = atr or 0
    if atr_val and price > 0:
        volatility = atr_val / price * 100
    else:
        volatility = abs(stock.get("change_pct", 0)) or 2.0
    if volatility < 2:   position_pct = 20
    elif volatility < 4: position_pct = 15
    else:                position_pct = 10
    if rsi is not None:
        if rsi < 35:   position_pct += 3
        elif rsi > 65: position_pct -= 2
    if v5_score >= 82:    position_pct += 2
    elif v5_score < 55:   position_pct -= 2
    position_pct = max(5, min(25, position_pct))

    # V5.2: first sell target (60pct of upside)
    sell_first = round(price * (1 + upside * 0.6), 2)
    if sell_first <= buy_point * 1.05:
        sell_first = round(buy_point * 1.08, 2)

    # V5.2: position trend adjustment
    if ma5 and ma20 and ma60 and ma5 > 0 and ma20 > 0 and ma60 > 0:
        if ma5 < ma20 < ma60:
            position_pct = max(5, int(position_pct * 0.7))
        elif ma5 > ma20 > ma60:
            position_pct = min(25, int(position_pct * 1.1))

    risk_reward_ratio = 0.0
    if price > stop_loss:
        risk_reward_ratio = round((sell_point - price) / (price - stop_loss), 2)

    # === V5.3: 熊市增强规则 ===
    try:
        from modules.market_env import get_market_env
        _env = get_market_env()
        if _env.trend == 'bear':
            # V5.6: 熊市买入折扣(额外1.5%，3%过深导致多数"等待回调")
            buy_point = round(buy_point * 0.985, 2)
            # V5.6fix: 熊市止损 - bear_stop基于折扣后buy_point，但必须小于buy_point
            bear_stop = round(buy_point * 0.92, 2)
            if bear_stop < buy_point:
                stop_loss = min(stop_loss, bear_stop)  # min而非max: 取更低(更安全)的止损
            # V5.6: 熊市仓位上限 - 优质股(V5>=65)15%，其余12%
            if v5_score >= 65:
                position_pct = min(position_pct, 15)
            else:
                position_pct = min(position_pct, 12)
            # 熊市: 降低卖出目标(乘以0.85)
            sell_point = round(price + (sell_point - price) * 0.85, 2)
            sell_first = round(price + (sell_first - price) * 0.85, 2)
            # 熊市: 降级星级(最多3星)
            star_rating = min(star_rating, 3)
        elif _env.trend == 'range':
            # 震荡市: 适度调整
            position_pct = min(position_pct, 18)
    except Exception:
        pass

    # V5.5: 最终安全检查 - 止损价必须低于买入价
    # 止损价距买入价至少5%距离（避免止损过近导致频繁误触）
    if stop_loss >= buy_point:
        stop_loss = round(buy_point * 0.92, 2)  # 默认8%止损
    elif stop_loss > buy_point * 0.95:
        # 止损距买入价不足5%，放宽到7%
        stop_loss = round(buy_point * 0.93, 2)

    # === V5.3: 入场时机判断 ===
    entry_status = '观望'
    if price <= buy_point:
        entry_status = '已到买入价'
    elif price <= buy_point * 1.02:
        entry_status = '接近买入价'
    elif price <= buy_point * 1.05:
        entry_status = '可逢低关注'
    else:
        entry_status = '等待回调'

    return {
        "current": price,
        "buy": buy_point,
        "sell": sell_point,
        "sell_first": sell_first,
        "stop_loss": stop_loss,
        "position_pct": position_pct,
        "risk_reward_ratio": risk_reward_ratio,
        "upside": round((sell_point - price) / price * 100, 1),
        "downside": round((price - buy_point) / price * 100, 1),
        "recommendation": rec,
        "star_rating": star_rating,
        "entry_status": entry_status,
    }


def calculate_hot_factor(stock_code, stock_name, hot_sectors, hot_keywords):
    """计算股票的热点因子

    返回: (热点加分, 热点原因列表)
    """
    bonus = 0
    reasons = []

    # 1. 板块热度加分
    stock_sectors = STOCK_SECTOR_MAP.get(stock_code, [])

    # 根据股票名称推断板块
    name_hints = {
        "半导体": ["半导体", "芯片", "微", "创", "华创"],
        "新能源": ["新能", "光伏", "锂电", "储能", "电源", "宁德", "比亚迪"],
        "医药": ["医", "药", "生物", "康", "健"],
        "科技": ["科技", "电子", "信息", "软", "通"],
    }
    for hint, keywords in name_hints.items():
        if any(h in stock_name for h in keywords):
            stock_sectors.append(hint)

    # 检查股票所属板块是否在热门板块中（模糊匹配）
    for stock_sector in stock_sectors:
        for hot_sector, change in hot_sectors.items():
            # 模糊匹配：板块名称包含关系
            if stock_sector in hot_sector or hot_sector in stock_sector:
                # 板块涨幅越大，加分越多
                if change >= 3:
                    bonus += 10
                    reasons.append(f"🔥【{hot_sector}】+{change:.1f}%")
                elif change >= 2:
                    bonus += 6
                    reasons.append(f"热门【{hot_sector}】+{change:.1f}%")
                elif change >= 1:
                    bonus += 3
                else:
                    bonus += 1
                break  # 避免重复加分

    # 2. 根据热门板块关键词匹配股票名称
    # 关键词：固态电池、钠电池、AI、光伏等
    sector_keywords = ["固态电池", "钠电池", "半导体", "芯片", "光伏", "储能", "锂电", "新能源",
                       "人工智能", "AI", "数字经济", "机器人", "医药", "医疗", "创新药",
                       "消费电子", "汽车", "特斯拉", "华为", "苹果"]

    for keyword in sector_keywords:
        if keyword in stock_name:
            # 检查该关键词对应板块是否热门
            for hot_sector, change in hot_sectors.items():
                if keyword in hot_sector and change > 0:
                    bonus += 5
                    reasons.append(f"🔥{keyword}")
                    break

    # 3. 新闻热点关键词匹配
    for keyword in hot_keywords:
        if keyword in stock_name:
            bonus += 3
            reasons.append(f"热点【{keyword}】")

    # 上限20分
    bonus = min(bonus, 20)

    return bonus, reasons[:3]  # 最多返回3个原因


def get_hot_sectors_and_news():
    """获取当日热门板块和新闻热点关键词

    返回:
        hot_sectors: 涨幅前列的板块及其涨幅
        hot_keywords: 新闻中频繁出现的热点关键词
    """
    from modules.http_client import session, HEADERS

    hot_sectors = {}  # {板块名: 涨幅}
    hot_keywords = set()  # 热点关键词集合

    try:
        # 1. 获取板块行情
        log.info("获取板块行情分析热点...")
        industry_sectors = _fetch_sina_sectors('industry')
        concept_sectors = _fetch_sina_sectors('class')

        all_sectors = industry_sectors + concept_sectors

        # 按涨幅排序，取前10热门板块
        sorted_sectors = sorted(all_sectors, key=lambda x: x.get('change_pct', 0), reverse=True)

        for s in sorted_sectors[:15]:  # Top 15 热门板块
            name = s.get('name', '')
            change = s.get('change_pct', 0)
            if change > 0:  # 只记录上涨板块
                hot_sectors[name] = change
                # 同时记录相关关键词
                for sector_name, keywords in SECTOR_KEYWORDS.items():
                    if sector_name in name or name in sector_name:
                        hot_keywords.update(keywords)

        log.info(f"热门板块: {list(hot_sectors.keys())[:5]}")

        # 2. 获取新闻热点
        try:
            r = session.get("https://feed.mix.sina.com.cn/api/roll/get",
                           params={"pageid": "153", "lid": "2509", "k": "", "r": "0.5", "page": 1},
                           headers=HEADERS, timeout=10)
            d = r.json()
            if d.get('result') and d['result'].get('data'):
                news_titles = [item.get('title', '') for item in d['result']['data'][:30]]
                news_text = ' '.join(news_titles)

                # 统计热点关键词出现次数
                keyword_count = {}
                for keyword, sectors in HOT_KEYWORD_TO_SECTOR.items():
                    count = news_text.count(keyword)
                    if count > 0:
                        keyword_count[keyword] = count
                        hot_keywords.add(keyword)
                        # 把关键词对应的板块也加入热门
                        for sector in sectors:
                            if sector not in hot_sectors:
                                hot_sectors[sector] = 0.5  # 新闻热度加分

                # 按出现次数排序，取最热的10个关键词
                top_keywords = sorted(keyword_count.items(), key=lambda x: x[1], reverse=True)[:10]
                if top_keywords:
                    log.info(f"新闻热点: {[k[0] for k in top_keywords[:5]]}")

        except Exception as e:
            log.warning(f"新闻获取失败: {e}")

    except Exception as e:
        log.error(f"板块数据获取失败: {e}")

    return hot_sectors, hot_keywords


def _fetch_sina_sectors(category: str) -> list[dict]:
    """从新浪财经获取板块实时行情数据

    category: 'class' (概念板块) 或 'industry' (行业板块)
    数据源: newFLJK.php（老版接口，返回丰富数据含avg_pe/领涨股等）
    """
    from modules.http_client import session, HEADERS
    import json

    sectors = []
    url = 'https://money.finance.sina.com.cn/q/view/newFLJK.php?param={category}'

    try:
        r = session.get(url.format(category=category), headers=HEADERS, timeout=15)
        r.encoding = 'gb2312'
        text = r.text.strip()

        start = text.find('{')
        end = text.rfind('}')
        if start < 0 or end < 0:
            return []

        data = json.loads(text[start:end+1])

        for key, val in data.items():
            parts = val.split(',')
            if len(parts) < 13:
                continue
            try:
                sectors.append({
                    "code": parts[0],
                    "name": parts[1],
                    "stock_count": int(parts[2]),
                    "avg_pe": float(parts[3]),
                    "change_pct": float(parts[4]),
                    "turnover": float(parts[5]),
                    "volume": int(parts[6]),
                    "amount": int(parts[7]),
                    "leader_code": parts[8],
                    "leader_name": parts[12],
                    "leader_price": float(parts[10]),
                    "leader_change": float(parts[9]),
                })
            except (ValueError, IndexError):
                continue

        return sectors
    except Exception as e:
        log.warning(f"_fetch_sina_sectors({category}) 失败: {e}")
        # Fallback: 使用分页API
        try:
            url2 = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
            for page in range(1, 4):
                params = {
                    'page': page, 'num': 40,
                    'sort': 'changepercent', 'asc': 0,
                    'node': 'hangye_ZA01' if category == 'industry' else 'gn_hwqc',
                    '_s_r_a': 'page'
                }
                r2 = session.get(url2, params=params, timeout=10,
                                 headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'})
                r2.encoding = 'utf-8'
                data2 = r2.json()
                if not data2:
                    break
                for item in data2:
                    try:
                        sectors.append({
                            'name': item.get('name', ''),
                            'change_pct': float(item.get('changepercent', 0) or 0),
                            'code': item.get('code', ''),
                        })
                    except Exception:
                        continue
        except Exception as e2:
            log.warning(f"Fallback板块获取也失败: {e2}")
    return sectors


