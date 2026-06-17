"""
板块轮动策略模块 - 系统化板块相对强度与轮动阶段检测

替代 scoring.py 中基于 STOCK_SECTOR_MAP 和名称匹配的板块加分逻辑，
改为基于实时行情数据的板块相对强度排名、资金流持续性检测和轮动阶段分类。

核心能力:
1. 板块相对强度排名 - 按5/10/20日涨跌幅排名
2. 板块资金流持续性 - 连续3日净流入检测
3. 板块轮动阶段分类 - startup / accelerating / exhausting / declining
4. 统一入口 calculate_sector_bonus() - 返回 (bonus_points, reasons_list)
"""

from __future__ import annotations

import json
import threading
import time
from typing import Optional

from modules.http_client import session, HEADERS
from modules.logger import log
from modules.data_fetcher import get_stock_industry

__all__ = [
    "get_sector_rotation_data",
    "calculate_sector_bonus",
    "SectorPhase",
]


# ===========================================================================
# 板块轮动阶段常量
# ===========================================================================

class SectorPhase:
    """板块轮动阶段枚举"""
    STARTUP = "startup"            # 启动期：涨幅0-3%，量能放大
    ACCELERATING = "accelerating"  # 加速期：涨幅3-8%，持续放量
    EXHAUSTING = "exhausting"      # 末期：涨幅>8%，量能萎缩
    DECLINING = "declining"        # 下跌：涨幅<0
    UNKNOWN = "unknown"            # 数据不足，无法判断


# ===========================================================================
# 模块级缓存（线程安全，300秒刷新）
# ===========================================================================

_sector_cache: dict = {}
_sector_cache_time: float = 0
_sector_cache_lock = threading.Lock()
_SECTOR_CACHE_TTL = 300  # 5 minutes


# ===========================================================================
# 板块名称与股票行业之间的模糊匹配规则
# ===========================================================================

# 行业关键字到板块类别的映射（用于将股票行业名映射到板块数据中的板块名）
_INDUSTRY_KEYWORD_MAP: dict[str, list[str]] = {
    "半导体": ["半导体", "芯片", "集成电路"],
    "芯片": ["半导体", "芯片", "集成电路"],
    "集成电路": ["半导体", "芯片", "集成电路"],
    "人工智能": ["人工智能", "AI", "数字经济"],
    "AI": ["人工智能", "AI", "数字经济"],
    "数字经济": ["数字经济", "人工智能", "软件"],
    "新能源": ["新能源", "光伏", "储能", "锂电"],
    "光伏": ["光伏", "新能源", "储能"],
    "储能": ["储能", "新能源", "光伏"],
    "锂电": ["锂电", "新能源", "固态电池"],
    "固态电池": ["固态电池", "锂电", "新能源"],
    "医药": ["医药", "生物制品", "医疗器械"],
    "生物制品": ["生物制品", "医药"],
    "医疗器械": ["医疗器械", "医药"],
    "中药": ["中药", "医药"],
    "消费电子": ["消费电子", "电子", "苹果"],
    "电子": ["消费电子", "电子元件", "电子"],
    "汽车": ["汽车", "新能源汽车", "汽车零部件"],
    "新能源汽车": ["新能源汽车", "汽车", "锂电"],
    "汽车零部件": ["汽车零部件", "汽车"],
    "证券": ["证券", "券商", "金融"],
    "银行": ["银行", "金融"],
    "保险": ["保险", "金融"],
    "房地产": ["房地产", "地产"],
    "军工": ["军工", "国防", "航天"],
    "白酒": ["白酒", "消费", "食品饮料"],
    "食品饮料": ["食品饮料", "消费", "白酒"],
    "消费": ["消费", "食品饮料", "白酒"],
    "有色": ["有色金属", "有色", "矿业"],
    "有色金属": ["有色金属", "有色"],
    "化工": ["化工", "化学", "新材料"],
    "电力": ["电力", "新能源", "电网"],
    "游戏": ["游戏", "传媒", "游戏传媒"],
    "传媒": ["传媒", "游戏传媒"],
    "机器人": ["机器人", "人工智能"],
    "通信": ["通信", "光通信", "5G"],
    "软件": ["软件", "数字经济", "信息技术"],
    "煤炭": ["煤炭", "能源", "周期"],
    "钢铁": ["钢铁", "周期"],
    "建材": ["建材", "周期"],
    "养殖": ["养殖", "农业"],
}


# ===========================================================================
# 公开 API
# ===========================================================================


def get_sector_rotation_data(force_refresh: bool = False) -> dict:
    """获取板块轮动数据（带缓存）

    Returns:
        dict with:
        - sectors: list of sector dicts, each containing:
            - name: 板块名称
            - change_pct: 当日涨跌幅(%)
            - phase: 轮动阶段 (SectorPhase value)
            - volume_trend: 量能趋势 ("expanding" / "stable" / "shrinking" / "unknown")
            - relative_rank: 相对强度排名 (1=最强)
            - change_5d: 近5日涨跌幅(%), 可能为0
            - change_10d: 近10日涨跌幅(%), 可能为0
            - change_20d: 近20日涨跌幅(%), 可能为0
            - capital_flow_days: 连续净流入天数(正数)/净流出天数(负数)/0(无数据)
        - updated_at: 缓存更新时间戳
    """
    global _sector_cache, _sector_cache_time

    now = time.time()
    with _sector_cache_lock:
        if not force_refresh and _sector_cache and (now - _sector_cache_time) < _SECTOR_CACHE_TTL:
            return _sector_cache

    # 缓存过期或强制刷新，获取新数据
    try:
        raw_sectors = _fetch_sector_data()
    except Exception as e:
        log.error(f"板块数据获取失败: {e}")
        # 如果有旧缓存，继续用旧缓存
        with _sector_cache_lock:
            if _sector_cache:
                log.warning("板块数据获取失败，使用旧缓存")
                return _sector_cache
        return {"sectors": [], "updated_at": now}

    if not raw_sectors:
        with _sector_cache_lock:
            if _sector_cache:
                return _sector_cache
        return {"sectors": [], "updated_at": now}

    # 计算相对强度排名
    ranked_sectors = _calc_relative_strength(raw_sectors)

    # 分类轮动阶段
    for sector in ranked_sectors:
        sector["phase"] = _classify_sector_phase(
            sector.get("change_pct", 0),
            sector.get("volume_trend", "unknown"),
        )

    result = {
        "sectors": ranked_sectors,
        "updated_at": now,
    }

    with _sector_cache_lock:
        _sector_cache = result
        _sector_cache_time = now

    log.info(
        f"板块轮动数据已更新: {len(ranked_sectors)} 个板块, "
        f"启动期={sum(1 for s in ranked_sectors if s['phase'] == SectorPhase.STARTUP)}, "
        f"加速期={sum(1 for s in ranked_sectors if s['phase'] == SectorPhase.ACCELERATING)}, "
        f"末期={sum(1 for s in ranked_sectors if s['phase'] == SectorPhase.EXHAUSTING)}, "
        f"下跌={sum(1 for s in ranked_sectors if s['phase'] == SectorPhase.DECLINING)}"
    )

    return result



def calculate_sector_bonus(
    stock_code: str,
    stock_name: str,
    stock_industry: str,
    hot_sectors_data: Optional[dict] = None,
) -> tuple[float, list[str]]:
    """计算股票的板块轮动加分

    根据股票所属行业匹配板块轮动数据，结合板块相对强度排名、
    资金流持续性和轮动阶段计算加分值。

    Args:
        stock_code: 股票代码（如 "002371"）
        stock_name: 股票名称（如 "北方华创"）
        stock_industry: 股票所属行业（如 "半导体"）
        hot_sectors_data: 可选的热门板块数据，格式为 {板块名: 涨幅}。
            如果提供，将用于额外的热点匹配加分。

    Returns:
        (bonus_points, reasons_list)
        - bonus_points: 0-15 范围的加分值
        - reasons: 解释字符串列表
    """
    bonus = 0.0
    reasons: list[str] = []

    # 1. 获取板块轮动数据
    rotation_data = get_sector_rotation_data()
    sectors = rotation_data.get("sectors", [])

    if not sectors:
        # 没有板块数据时，如果提供了 hot_sectors_data，仍然尝试用旧逻辑
        if hot_sectors_data:
            return _fallback_hot_sector_bonus(stock_code, stock_name, stock_industry, hot_sectors_data)
        return (0.0, [])

    # 2. 确定股票的行业关键字（优先用 stock_industry，否则查 API）
    industry = stock_industry
    if not industry or industry in ("未知", "其他", ""):
        try:
            industry_info = get_stock_industry(stock_code)
            industry = industry_info.get("industry", "未知")
        except Exception:
            industry = "未知"

    # 3. 匹配股票所属的板块（模糊匹配）
    matched_sectors = _match_stock_to_sectors(industry, stock_name, sectors)

    if not matched_sectors:
        # 没有匹配到板块，尝试热门板块数据
        if hot_sectors_data:
            return _fallback_hot_sector_bonus(stock_code, stock_name, stock_industry, hot_sectors_data)
        return (0.0, [])

    # 4. 取最优匹配板块（排名最高的）
    best_sector = min(matched_sectors, key=lambda s: s.get("relative_rank", 999))
    phase = best_sector.get("phase", SectorPhase.UNKNOWN)
    rank = best_sector.get("relative_rank", 0)
    change_pct = best_sector.get("change_pct", 0)
    capital_flow_days = best_sector.get("capital_flow_days", 0)
    volume_trend = best_sector.get("volume_trend", "unknown")

    # 5. 轮动阶段加分（核心逻辑）
    phase_bonus = 0.0
    if phase == SectorPhase.STARTUP:
        # 启动期：最大加分，新的上升趋势刚形成
        phase_bonus = 8.0
        reasons.append(f"▶️【{best_sector['name']}】启动期 +{change_pct:.1f}% 量能放大")
    elif phase == SectorPhase.ACCELERATING:
        # 加速期：中等加分，趋势已确认但未过热
        phase_bonus = 5.0
        reasons.append(f"⚡【{best_sector['name']}】加速期 +{change_pct:.1f}% 持续放量")
    elif phase == SectorPhase.EXHAUSTING:
        # 末期：扣分，涨幅过大且量能萎缩，可能即将转势
        phase_bonus = -2.0
        reasons.append(f"⚠️【{best_sector['name']}】末期 +{change_pct:.1f}% 量能萎缩")
    elif phase == SectorPhase.DECLINING:
        # 下跌：不加分
        phase_bonus = 0.0
    else:
        phase_bonus = 0.0

    bonus += phase_bonus

    # 6. 相对强度排名加分（前10%加分，前25%小加分）
    total_sectors = len(sectors)
    if total_sectors > 0 and rank > 0:
        rank_pct = rank / total_sectors
        if rank_pct <= 0.10:
            # Top 10% 板块
            bonus += 3.0
            reasons.append(f"板块强度Top10% 排名第{rank}")
        elif rank_pct <= 0.25:
            # Top 25% 板块
            bonus += 1.5
            reasons.append(f"板块强度Top25% 排名第{rank}")
        elif rank_pct <= 0.50:
            bonus += 0.5

    # 7. 资金流持续性加分（连续3日以上净流入）
    if capital_flow_days >= 5:
        bonus += 3.0
        reasons.append(f"资金连续{capital_flow_days}日净流入")
    elif capital_flow_days >= 3:
        bonus += 2.0
        reasons.append(f"资金连续{capital_flow_days}日净流入")
    elif capital_flow_days >= 1:
        bonus += 0.5

    # 8. 热门板块数据额外加分（如果提供了 hot_sectors_data）
    if hot_sectors_data:
        hot_bonus, hot_reasons = _calc_hot_sector_supplement(
            stock_code, stock_name, industry, hot_sectors_data, best_sector
        )
        bonus += hot_bonus
        reasons.extend(hot_reasons)

    # 9. 上限15分，下限0分
    bonus = max(0.0, min(15.0, bonus))

    # 最多保留 4 条原因
    reasons = reasons[:4]

    return (round(bonus, 1), reasons)


# ===========================================================================
# 内部函数
# ===========================================================================


def _fetch_sector_data() -> list[dict]:
    """从新浪财经获取板块行情数据

    获取行业板块和概念板块的实时行情，
    包括涨跌幅、换手率、成交量等数据。
    同时尝试从东方财富获取板块资金流数据。

    数据源:
    1. 新浪 newFLJK.php（主要，返回丰富数据含 avg_pe/领涨股等）
    2. 新浪 Market_Center（备用，分页查询）
    3. 东方财富板块资金流（辅助，用于资金流分析）
    """
    sectors: list[dict] = []
    seen_names: set[str] = set()

    # --- 数据源1: 新浪 newFLJK.php ---
    for category in ("industry", "class"):
        try:
            raw = _fetch_sina_sectors_rich(category)
            for s in raw:
                name = s.get("name", "")
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                sectors.append({
                    "name": name,
                    "code": s.get("code", ""),
                    "change_pct": s.get("change_pct", 0),
                    "turnover": s.get("turnover", 0),
                    "stock_count": s.get("stock_count", 0),
                    "volume": s.get("volume", 0),
                    "amount": s.get("amount", 0),
                    "volume_trend": _infer_volume_trend(s),
                    "change_5d": 0,
                    "change_10d": 0,
                    "change_20d": 0,
                    "capital_flow_days": 0,
                })
        except Exception as e:
            log.warning(f"_fetch_sector_data({category}) 失败: {e}")

    # 如果主要接口没拿到数据，尝试备用接口
    if len(sectors) < 10:
        log.info("新浪主接口数据不足，尝试备用接口...")
        for category in ("industry", "class"):
            try:
                raw = _fetch_sina_sectors_fallback(category)
                for s in raw:
                    name = s.get("name", "")
                    if not name or name in seen_names:
                        continue
                    seen_names.add(name)
                    sectors.append({
                        "name": name,
                        "code": s.get("code", ""),
                        "change_pct": s.get("change_pct", 0),
                        "turnover": 0,
                        "stock_count": 0,
                        "volume": 0,
                        "amount": 0,
                        "volume_trend": "unknown",
                        "change_5d": 0,
                        "change_10d": 0,
                        "change_20d": 0,
                        "capital_flow_days": 0,
                    })
            except Exception as e:
                log.warning(f"备用板块接口({category})失败: {e}")

    # --- 辅助数据: 东方财富板块资金流 ---
    try:
        _enrich_capital_flow(sectors)
    except Exception as e:
        log.debug(f"资金流数据获取失败(非关键): {e}")

    # --- 辅助数据: 多日涨跌幅 ---
    try:
        _enrich_multi_day_change(sectors)
    except Exception as e:
        log.debug(f"多日涨跌幅获取失败(非关键): {e}")

    log.info(f"板块数据获取完成: {len(sectors)} 个板块")
    return sectors



def _fetch_sina_sectors_rich(category: str) -> list[dict]:
    """从新浪财经获取板块实时行情数据（丰富版）

    category: 'class' (概念板块) 或 'industry' (行业板块)
    数据源: newFLJK.php（老版接口，返回丰富数据含avg_pe/领涨股等）
    """
    sectors = []
    url = f"https://money.finance.sina.com.cn/q/view/newFLJK.php?param={category}"

    try:
        r = session.get(url, headers=HEADERS, timeout=15)
        r.encoding = "gb2312"
        text = r.text.strip()

        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0:
            return []

        data = json.loads(text[start:end + 1])

        for key, val in data.items():
            parts = val.split(",")
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
        log.warning(f"_fetch_sina_sectors_rich({category}) 失败: {e}")
        return []


def _fetch_sina_sectors_fallback(category: str) -> list[dict]:
    """备用新浪板块接口（分页查询）

    当主接口无法获取数据时，使用分页查询作为备用。
    """
    sectors = []
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    node_map = {"industry": "hangye_ZA01", "class": "gn_hwqc"}
    node = node_map.get(category, "gn_hwqc")

    for page in range(1, 4):
        try:
            params = {
                "page": page, "num": 40,
                "sort": "changepercent", "asc": 0,
                "node": node, "_s_r_a": "page",
            }
            r = session.get(
                url, params=params, timeout=10,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
            )
            r.encoding = "utf-8"
            data = r.json()
            if not data:
                break
            for item in data:
                try:
                    sectors.append({
                        "name": item.get("name", ""),
                        "change_pct": float(item.get("changepercent", 0) or 0),
                        "code": item.get("code", ""),
                    })
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            log.debug(f"备用板块接口第{page}页失败: {e}")
            break

    return sectors



def _infer_volume_trend(sector_data: dict) -> str:
    """根据板块数据推算量能趋势

    通过换手率和涨幅的关系来推算量能趋势：
    - 涨幅正且换手率高于正常值 → expanding（放量）
    - 涨幅正且换手率正常 → stable（稳定）
    - 涨幅正且换手率偏低 → shrinking（缩量）
    - 其他 → unknown
    """
    change_pct = sector_data.get("change_pct", 0)
    turnover = sector_data.get("turnover", 0)

    if change_pct <= 0:
        return "unknown"

    # 换手率阈值判断（当日换手率）
    if turnover >= 5.0:
        return "expanding"
    elif turnover >= 2.0:
        return "stable"
    elif turnover > 0:
        return "shrinking"
    else:
        return "unknown"


def _classify_sector_phase(change_pct: float, volume_trend: str) -> str:
    """根据涨幅和量能趋势判断板块阶段

    轮动阶段分类逻辑:
    - startup:     涨幅 0-3%, 量能放大 → 新的上升趋势刚形成
    - accelerating: 涨幅 3-8%, 量能稳定或放大 → 趋势已确认
    - exhausting:  涨幅 >8%, 量能萎缩 → 可能即将转势
    - declining:   涨幅 <0 → 下跌
    - unknown:     数据不足

    特殊情况:
    - 涨幅 >8% 但量能仍在放大 → 仍算 accelerating（强势延续）
    - 涨幅 3-8% 但量能萎缩 → 降级为 exhausting（动力不足）
    """
    if change_pct < 0:
        return SectorPhase.DECLINING

    if change_pct <= 3.0:
        # 0-3%: 启动期需要量能放大确认
        if volume_trend in ("expanding", "stable"):
            return SectorPhase.STARTUP
        elif volume_trend == "shrinking":
            return SectorPhase.UNKNOWN  # 量能不足，无法确认
        else:
            return SectorPhase.UNKNOWN
    elif change_pct <= 8.0:
        # 3-8%: 加速期，但如果量能萎缩则可能是末期
        if volume_trend == "shrinking":
            return SectorPhase.EXHAUSTING
        else:
            return SectorPhase.ACCELERATING
    else:
        # >8%: 末期，但如果量能仍在放大则强势延续
        if volume_trend == "expanding":
            return SectorPhase.ACCELERATING
        else:
            return SectorPhase.EXHAUSTING



def _calc_relative_strength(sector_data: list[dict]) -> list[dict]:
    """计算板块相对强度排名（5/10/20日）

    综合当日、近5日、近10日、近20日的涨跌幅计算加权排名分数：
    - 当日涨跌幅: 权重 0.4
    - 近5日涨跌幅: 权重 0.3
    - 近10日涨跌幅: 权重 0.2
    - 近20日涨跌幅: 权重 0.1

    排名分数越高，相对强度越大。
    """
    if not sector_data:
        return []

    # 计算综合强度分数
    for sector in sector_data:
        change = sector.get("change_pct", 0)
        change_5d = sector.get("change_5d", 0)
        change_10d = sector.get("change_10d", 0)
        change_20d = sector.get("change_20d", 0)

        # 如果多日数据不可用，用当日数据填充（降级处理）
        if change_5d == 0:
            change_5d = change * 3  # 估算：如果没有多日数据，粗略估算
        if change_10d == 0:
            change_10d = change_5d * 1.5
        if change_20d == 0:
            change_20d = change_10d * 1.3

        # 加权综合强度
        strength_score = (
            change * 0.4
            + change_5d * 0.3
            + change_10d * 0.2
            + change_20d * 0.1
        )
        sector["strength_score"] = round(strength_score, 4)

    # 按综合强度排序，赋予排名
    sorted_sectors = sorted(sector_data, key=lambda x: x.get("strength_score", 0), reverse=True)
    for rank_idx, sector in enumerate(sorted_sectors, 1):
        sector["relative_rank"] = rank_idx

    return sorted_sectors



def _match_stock_to_sectors(
    industry: str,
    stock_name: str,
    sectors: list[dict],
) -> list[dict]:
    """将股票匹配到相关板块

    匹配策略（按优先级）:
    1. 行业关键字精确匹配（行业名完全包含板块名或反之）
    2. 行业关键字模糊匹配（通过 _INDUSTRY_KEYWORD_MAP）
    3. 股票名称关键字匹配（股票名包含板块名）

    返回所有匹配到的板块数据列表。
    """
    matched = []
    matched_names = set()

    # 策略1: 行业名精确匹配
    for sector in sectors:
        sector_name = sector.get("name", "")
        if not sector_name:
            continue
        # 双向包含匹配
        if industry in sector_name or sector_name in industry:
            if sector_name not in matched_names:
                matched.append(sector)
                matched_names.add(sector_name)

    # 策略2: 行业关键字模糊匹配
    industry_keywords = _INDUSTRY_KEYWORD_MAP.get(industry, [])
    for keyword in industry_keywords:
        for sector in sectors:
            sector_name = sector.get("name", "")
            if not sector_name or sector_name in matched_names:
                continue
            if keyword in sector_name or sector_name in keyword:
                matched.append(sector)
                matched_names.add(sector_name)

    # 策略3: 股票名称关键字匹配
    if stock_name:
        for sector in sectors:
            sector_name = sector.get("name", "")
            if not sector_name or sector_name in matched_names:
                continue
            # 板块名包含在股票名中（如 "半导体" 在 "半导体设备" 中）
            if sector_name in stock_name:
                matched.append(sector)
                matched_names.add(sector_name)

    return matched



def _calc_hot_sector_supplement(
    stock_code: str,
    stock_name: str,
    industry: str,
    hot_sectors_data: dict,
    best_sector: dict,
) -> tuple[float, list[str]]:
    """计算热门板块数据的额外加分

    当 calculate_sector_bonus 已经通过板块轮动数据计算了主加分后，
    如果提供了 hot_sectors_data，额外检查是否有其他热门板块也匹配该股票。

    仅在股票匹配到热门板块且该板块不是已匹配的最优板块时加分。
    """
    bonus = 0.0
    reasons: list[str] = []
    best_name = best_sector.get("name", "")

    # 获取股票的行业关键字
    industry_keywords = _INDUSTRY_KEYWORD_MAP.get(industry, [industry])

    for hot_name, hot_change in hot_sectors_data.items():
        if not hot_change or hot_change <= 0:
            continue
        # 跳过已经匹配的最优板块
        if hot_name == best_name:
            continue

        # 检查行业关键字是否匹配热门板块
        is_match = False
        for keyword in industry_keywords:
            if keyword in hot_name or hot_name in keyword:
                is_match = True
                break

        # 股票名匹配
        if not is_match and stock_name:
            if hot_name in stock_name:
                is_match = True

        if is_match:
            # 热门板块加分（涨幅越大加分越多，但上限较低）
            if hot_change >= 3:
                bonus += 2.0
                reasons.append(f"🔥【{hot_name}】+{hot_change:.1f}%")
            elif hot_change >= 2:
                bonus += 1.0
                reasons.append(f"热门【{hot_name}】+{hot_change:.1f}%")
            elif hot_change >= 1:
                bonus += 0.5

    # 上限4分
    bonus = min(bonus, 4.0)
    return (round(bonus, 1), reasons[:2])


def _fallback_hot_sector_bonus(
    stock_code: str,
    stock_name: str,
    stock_industry: str,
    hot_sectors_data: dict,
) -> tuple[float, list[str]]:
    """热门板块降级加分逻辑（当板块轮动数据不可用时）

    当板块轮动数据无法获取时，直接使用热门板块数据进行匹配加分。
    这是 calculate_sector_bonus 的降级策略。
    """
    bonus = 0.0
    reasons: list[str] = []

    # 获取行业关键字
    industry_keywords = _INDUSTRY_KEYWORD_MAP.get(stock_industry, [stock_industry])

    for hot_name, hot_change in hot_sectors_data.items():
        if not hot_change or hot_change <= 0:
            continue

        # 检查行业关键字是否匹配
        is_match = False
        for keyword in industry_keywords:
            if keyword in hot_name or hot_name in keyword:
                is_match = True
                break

        # 股票名匹配
        if not is_match and stock_name:
            if hot_name in stock_name:
                is_match = True

        if is_match:
            if hot_change >= 3:
                bonus += 5.0
                reasons.append(f"🔥【{hot_name}】+{hot_change:.1f}%")
            elif hot_change >= 2:
                bonus += 3.0
                reasons.append(f"热门【{hot_name}】+{hot_change:.1f}%")
            elif hot_change >= 1:
                bonus += 1.0
                reasons.append(f"微热【{hot_name}】+{hot_change:.1f}%")

    bonus = min(bonus, 10.0)
    return (round(bonus, 1), reasons[:3])



def _enrich_capital_flow(sectors: list[dict]) -> None:
    """从东方财富获取板块资金流数据，更新各板块的 capital_flow_days

    尝试从东方财富板块资金流接口获取最近5日的板块资金流数据，
    检测连续净流入天数（正值）或净流出天数（负值）。
    """
    if not sectors:
        return

    try:
        # 东方财富板块资金流排名接口
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 50,
            "po": 1, "np": 1,
            "fltt": 2, "invt": 2,
            "fid": "f62",  # 主力净流入排序
            "fs": "m:90+t:2+f:!50",  # 行业板块
            "fields": "f12,f14,f62,f184,f66,f69,f70,f71,f72,f75,f76,f77,f78,f79,f80,f81,f82,f83,f84,f85",
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/",
        }

        r = session.get(url, params=params, headers=headers, timeout=10)
        data = r.json()

        if not data.get("data") or not data["data"].get("diff"):
            # 尝试概念板块
            params["fs"] = "m:90+t:3+f:!50"  # 概念板块
            r = session.get(url, params=params, headers=headers, timeout=10)
            data = r.json()

        if not data.get("data") or not data["data"].get("diff"):
            return

        # 构建板块名 → 资金流天数映射
        capital_flow_map: dict[str, int] = {}
        for item in data["data"]["diff"]:
            name = item.get("f14", "")  # 板块名称
            # f66=今日主力净流入, f69=3日净流入, f72=5日净流入, f75=10日净流入
            # 通过判断3日和5日资金流的正负来推算连续天数
            net_inflow_1d = float(item.get("f62", 0) or 0)  # 今日
            net_inflow_3d = float(item.get("f66", 0) or 0)  # 3日
            net_inflow_5d = float(item.get("f72", 0) or 0)  # 5日（实际是f72）

            # 推算连续净流入天数
            flow_days = 0
            if net_inflow_5d > 0:
                flow_days = 5  # 5日净流入 > 0, 估算连续5日
            elif net_inflow_3d > 0:
                flow_days = 3  # 3日净流入 > 0
            elif net_inflow_1d > 0:
                flow_days = 1  # 仅今日净流入
            elif net_inflow_1d < 0:
                if net_inflow_3d < 0:
                    flow_days = -3  # 连续净流出
                else:
                    flow_days = -1  # 仅今日净流出

            if name and flow_days != 0:
                capital_flow_map[name] = flow_days

        # 更新板块数据中的 capital_flow_days
        for sector in sectors:
            sector_name = sector.get("name", "")
            if sector_name in capital_flow_map:
                sector["capital_flow_days"] = capital_flow_map[sector_name]
            else:
                # 模糊匹配
                for cf_name, days in capital_flow_map.items():
                    if cf_name in sector_name or sector_name in cf_name:
                        sector["capital_flow_days"] = days
                        break

        log.debug(f"资金流数据已更新: {len(capital_flow_map)} 个板块")

    except Exception as e:
        log.debug(f"资金流数据获取失败: {e}")



def _enrich_multi_day_change(sectors: list[dict]) -> None:
    """从东方财富获取板块多日涨跌幅数据

    尝试获取板块近5日、10日、20日涨跌幅数据。
    如果获取失败，保持默认值0，_calc_relative_strength 会做降级处理。
    """
    if not sectors:
        return

    try:
        # 东方财富板块涨跌幅排名接口
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 100,
            "po": 1, "np": 1,
            "fltt": 2, "invt": 2,
            "fid": "f3",  # 当日涨跌幅排序
            "fs": "m:90+t:2+f:!50",  # 行业板块
            "fields": "f12,f14,f3,f104,f105,f106",  # f3=今日,f104=5日,f105=10日,f106=20日
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/",
        }

        r = session.get(url, params=params, headers=headers, timeout=10)
        data = r.json()

        if not data.get("data") or not data["data"].get("diff"):
            return

        # 构建板块名 → 多日涨跌幅映射
        multi_day_map: dict[str, dict] = {}
        for item in data["data"]["diff"]:
            name = item.get("f14", "")  # 板块名称
            if not name:
                continue
            multi_day_map[name] = {
                "change_5d": float(item.get("f104", 0) or 0),
                "change_10d": float(item.get("f105", 0) or 0),
                "change_20d": float(item.get("f106", 0) or 0),
            }

        # 更新板块数据中的多日涨跌幅
        for sector in sectors:
            sector_name = sector.get("name", "")
            if sector_name in multi_day_map:
                sector.update(multi_day_map[sector_name])
            else:
                # 模糊匹配
                for md_name, md_data in multi_day_map.items():
                    if md_name in sector_name or sector_name in md_name:
                        sector.update(md_data)
                        break

        log.debug(f"多日涨跌幅数据已更新: {len(multi_day_map)} 个板块")

    except Exception as e:
        log.debug(f"多日涨跌幅数据获取失败: {e}")

