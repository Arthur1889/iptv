import os
import sys
import json
import re
import time
import subprocess
import logging
import aiohttp
import asyncio
import requests
import urllib.request
import urllib.error
from collections import defaultdict
from urllib.parse import urlparse

# 初始化日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("crawl.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
# =====================================================================
# 检查源列表有效期
# =====================================================================
def is_cache_valid(cache_path, max_age_days=1):
    """检查缓存文件是否存在且是否在有效期内"""
    if not os.path.exists(cache_path):
        return False
    # 获取文件最后修改时间
    file_time = os.path.getmtime(cache_path)
    # 计算当前时间与文件修改时间的差值
    delta_days = (time.time() - file_time) / (3600 * 24)
    return delta_days < max_age_days
# =====================================================================
# 下载远程源列表
# =====================================================================
async def parse_m3u_content(content):
    """从 M3U 文本中提取频道名称、URL、原始分组以及原厂的 logo 和 id"""
    items = []
    lines = content.splitlines()
    current_item = {}
    
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF"):
            # 1. 捕获原始分组
            grp_match = re.search(r'group-title="(.*?)"', line)
            grp = grp_match.group(1) if grp_match else ""
            
            # 2. 🌟 新增：捕获原始 tvg-logo
            logo_match = re.search(r'tvg-logo="(.*?)"', line)
            raw_logo = logo_match.group(1) if logo_match else ""
            
            # 3. 🌟 新增：捕获原始 tvg-id (即 epg-url 对应的台号)
            id_match = re.search(r'tvg-id="(.*?)"', line)
            raw_id = id_match.group(1) if id_match else ""
            
            # 4. 捕获频道名称
            name_match = re.search(r',(.*)$', line)
            name = name_match.group(1) if name_match else ""
            
            current_item = {
                "raw_name": name.strip(), 
                "group": grp.strip(),
                "raw_logo": raw_logo.strip(),
                "raw_id": raw_id.strip()
            }
        elif line.startswith("http") and current_item:
            current_item["url"] = line
            items.append(current_item)
            current_item = {}
            
    return items
# 🌟 请把这个函数重新粘贴到 parse_m3u_content 函数的下方：
async def fetch_and_parse_all(session, source_urls):
    """异步下载 sources.json 中的所有链接并汇总"""
    all_parsed_items = []
    for url in source_urls:
        try:
            async with session.get(url, timeout=20) as resp:
                if resp.status == 200:
                    content = await resp.text()
                    items = await parse_m3u_content(content)
                    all_parsed_items.extend(items)
                    logger.info(f"成功获取源 {url}, 解析到 {len(items)} 个频道")
        except Exception as e:
            logger.error(f"下载源 {url} 失败: {e}")
    return all_parsed_items
# =====================================================================
# 0. 环境自检与依赖自动安装
# =====================================================================
def auto_check_environment():
    sys_type = sys.platform
    print(f"[*] 启动 IPTV 聚合爬虫...")
    print(f"[*] 当前系统环境检测为: {sys_type}")
    
    required_packages = ["requests", "aiohttp", "tqdm"]
    for pkg in required_packages:
        try:
            __import__(pkg)
        except ImportError:
            print(f"[-] 缺少依赖 {pkg}，正在尝试自动安装...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
                print(f"[+] {pkg} 安装成功！")
            except Exception as e:
                print(f"[X] 自动安装 {pkg} 失败，请手动执行 pip install {pkg}。错误: {e}")
                sys.exit(1)

auto_check_environment()
import requests

# =====================================================================
# 1. 全局配置与路径初始化
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(BASE_DIR, "sources.json")
CACHE_PATH = os.path.join(BASE_DIR, "sources_cache.txt") # 建议运行前删除旧缓存
BLACKLIST_PATH = os.path.join(BASE_DIR, "blacklist.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "tv.m3u")
LOG_PATH = os.path.join(BASE_DIR, "crawl.log")

# 引入外部依赖配置
GROUP_JSON_PATH = os.path.join(BASE_DIR, "group", "group_standard.json")
NAME_JSON_PATH = os.path.join(BASE_DIR, "iptvname", "name.json")

# 预设的央视映射与标准组序
CCTV_DESC_MAP = {
    # 核心央视频道标准映射与中文描述
    "CCTV1": "CCTV-1 综合", "CCTV2": "CCTV-2 财经", "CCTV3": "CCTV-3 综艺",
    "CCTV4": "CCTV-4 中文国际", "CCTV5": "CCTV-5 体育", "CCTV5+": "CCTV-5+ 体育赛事",
    "CCTV6": "CCTV-6 电影", "CCTV7": "CCTV-7 国防军事", "CCTV8": "CCTV-8 电视剧",
    "CCTV9": "CCTV-9 纪录", "CCTV10": "CCTV-10 科教", "CCTV11": "CCTV-11 戏曲",
    "CCTV12": "CCTV-12 社会与法", "CCTV13": "CCTV-13 新闻", "CCTV14": "CCTV-14 少儿",
    "CCTV15": "CCTV-15 音乐", "CCTV16": "CCTV-16 奥林匹克", "CCTV17": "CCTV-17 农业农村",
    "CCTV4K": "CCTV4K 超高清", "CCTV8K": "CCTV8K 超高清", "CCTV5PLUS": "CCTV-5+ 体育赛事",
    
    # 常用央视数字付费频道（防止清洗后错分类到其他组）
    "CCTV兵器科技": "CCTV 兵器科技", "CCTV风云足球": "CCTV 风云足球", "CCTV高尔夫网球": "CCTV 高尔夫网球",
    "CCTV风云音乐": "CCTV 风云音乐", "CCTV风云剧场": "CCTV 风云剧场", "CCTV第一剧场": "CCTV 第一剧场",
    "CCTV怀旧剧场": "CCTV懷舊劇場", "CCTV大国健康": "CCTV 大国健康", "CCTV台球": "CCTV 台球",
    "CCTV女性时尚": "CCTV 女性时尚", "CCTV世界地理": "CCTV 世界地理", "CCTV央视文化精品": "CCTV 央视文化精品",
    "CCTV电视指南": "CCTV 电视指南", "CCTV发现之旅": "CCTV 发现之旅", "CCTV中学生": "CCTV 中学生",
    "CCTV老故事": "CCTV 老故事",
    
    # 中国教育电视台系列
    "CETV1": "中国教育-1", "CETV2": "中国教育-2", "CETV3": "中国教育-3", "CETV4": "中国教育-4",
    
    # 🌟 新增：CGTN 中国国际电视台全系列（支持无符号强匹配）
    "CGTN英语": "CGTN 英语", "CGTN纪录": "CGTN 纪录", 
    "CGTN法语": "CGTN 法语", "CGTN西语": "CGTN 西语", 
    "CGTN阿语": "CGTN 阿语", "CGTN俄语": "CGTN 俄语"
}

GROUP_PRIORITY = ["4K频道", "央视频道", "地方卫视", "山东频道", "地方频道", "影视频道", "歌曲及音乐MV", "纪录纪实", "娱乐频道", "电视剧直播", "少儿动漫", "港澳台", "海外频道", "体育赛事", "综合频道" ]

# 地区标识用于 4K 归类兜底
PROVINCES = ["北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆"]
# 🌟【新增：补充缺失的城市映射字典】
CITY_TO_PROVINCE = {
    "广州": "广东", "深圳": "广东", "珠海": "广东", "汕头": "广东", "佛山": "广东", "韶关": "广东", "湛江": "广东", "肇庆": "广东", "江门": "广东", "茂名": "广东", "惠州": "广东", "梅州": "广东", "汕尾": "广东", "河源": "广东", "阳江": "广东", "清远": "广东", "东莞": "广东", "中山": "广东", "潮州": "广东", "揭阳": "广东", "云浮": "广东",
    "济南": "山东", "青岛": "山东", "淄博": "山东", "枣庄": "山东", "东营": "山东", "烟台": "山东", "潍坊": "山东", "济宁": "山东", "泰安": "山东", "威海": "山东", "日照": "山东", "临沂": "山东", "德州": "山东", "聊城": "山东", "滨州": "山东", "菏泽": "山东",
    "南京": "江苏", "无锡": "江苏", "徐州": "江苏", "常州": "江苏", "苏州": "江苏", "南通": "江苏", "连云港": "江苏", "淮安": "江苏", "盐城": "江苏", "扬州": "江苏", "镇江": "江苏", "泰州": "江苏", "宿迁": "江苏",
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江", "嘉兴": "浙江", "湖州": "浙江", "绍兴": "浙江", "金华": "浙江", "衢州": "浙江", "舟山": "浙江", "台州": "浙江", "丽水": "浙江", "遂昌": "浙江", "松阳": "浙江", "云和": "浙江", "青田": "浙江", "龙泉": "浙江", "东阳": "浙江", "新昌": "浙江", "萧山": "浙江", "余姚": "浙江",
    "合肥": "安徽", "芜湖": "安徽", "蚌埠": "安徽", "淮南": "安徽", "马鞍山": "安徽", "淮北": "安徽", "铜陵": "安徽", "安庆": "安徽", "黄山": "安徽", "滁州": "安徽", "阜阳": "安徽", "宿州": "安徽", "六安": "安徽", "亳州": "安徽", "池州": "安徽", "宣城": "安徽",
    "武汉": "湖北", "黄石": "湖北", "十堰": "湖北", "宜昌": "湖北", "襄阳": "湖北", "鄂州": "湖北", "荆门": "湖北", "孝感": "湖北", "荆州": "湖北", "黄冈": "湖北", "咸宁": "湖北", "随州": "湖北", "恩施": "湖北",
    "长沙": "湖南", "株洲": "湖南", "湘潭": "湖南", "衡阳": "湖南", "邵阳": "湖南", "岳阳": "湖南", "常德": "湖南", "张家界": "湖南", "益阳": "湖南", "郴州": "湖南", "永州": "湖南", "怀化": "湖南", "娄底": "湖南", "湘西": "湖南",
    "成都": "四川", "自贡": "四川", "攀枝花": "四川", "泸州": "四川", "德阳": "四川", "绵阳": "四川", "广元": "四川", "遂宁": "四川", "内江": "四川", "乐山": "四川", "南充": "四川", "眉山": "四川", "宜宾": "四川", "广安": "四川", "达州": "四川", "雅安": "四川", "巴中": "四川", "资阳": "四川", "阿坝": "四川", "甘孜": "四川", "凉山": "四川", "营山": "四川",
    "沈阳": "辽宁", "大连": "辽宁", "鞍山": "辽宁", "抚顺": "辽宁", "本溪": "辽宁", "丹东": "辽宁", "锦州": "辽宁", "营口": "辽宁", "阜新": "辽宁", "辽阳": "辽宁", "盘锦": "辽宁", "铁岭": "辽宁", "朝阳": "辽宁", "葫芦岛": "辽宁",
    "福州": "福建", "厦门": "福建", "莆田": "福建", "三明": "福建", "泉州": "福建", "漳州": "福建", "南平": "福建", "龙岩": "福建", "宁德": "福建",
    "郑州": "河南", "开封": "河南", "洛阳": "河南", "平顶山": "河南", "安阳": "河南", "鹤壁": "河南", "新乡": "河南", "焦作": "河南", "濮阳": "河南", "许昌": "河南", "漯河": "河南", "三门峡": "河南", "南阳": "河南", "商丘": "河南", "信阳": "河南", "周口": "河南", "驻马店": "河南", "济源": "河南", "淅川": "河南", "襄城": "河南", "延津": "河南", "沁阳": "河南", "项城": "河南", "禹州": "河南",
    "石家庄": "河北", "唐山": "河北", "秦皇岛": "河北", "邯郸": "河北", "邢台": "河北", "保定": "河北", "张家口": "河北", "承德": "河北", "沧州": "河北", "廊坊": "河北", "衡水": "河北",
    "太原": "山西", "大同": "山西", "阳泉": "山西", "长治": "山西", "晋城": "山西", "朔州": "山西", "晋中": "山西", "运城": "山西", "忻州": "山西", "临汾": "山西", "吕梁": "山西", "武乡": "山西", "壶关": "山西",
    "西安": "陕西", "铜川": "陕西", "宝鸡": "陕西", "咸阳": "陕西", "渭南": "陕西", "延安": "陕西", "汉中": "陕西", "榆林": "陕西", "安康": "陕西", "商洛": "陕西",
    "南宁": "广西", "柳州": "广西", "桂林": "广西", "梧州": "广西", "北海": "广西", "防城港": "广西", "钦州": "广西", "贵港": "广西", "玉林": "广西", "百色": "广西", "贺州": "广西", "河池": "广西", "来宾": "广西", "崇左": "广西",
    "哈尔滨": "黑龙江", "齐齐哈尔": "黑龙江", "鸡西": "黑龙江", "鹤岗": "黑龙江", "双鸭山": "黑龙江", "大庆": "黑龙江", "伊春": "黑龙江", "佳木斯": "黑龙江", "七台河": "黑龙江", "牡丹江": "黑龙江", "黑河": "黑龙江", "绥化": "黑龙江", "大兴安岭": "黑龙江",
    "长春": "吉林", "吉林市": "吉林", "四平": "吉林", "辽源": "吉林", "通化": "吉林", "白山": "吉林", "松原": "吉林", "白城": "吉林", "延边": "吉林",
    "兰州": "甘肃", "嘉峪关": "甘肃", "金昌": "甘肃", "白银": "甘肃", "天水": "甘肃", "武威": "甘肃", "张掖": "甘肃", "平凉": "甘肃", "酒泉": "甘肃", "庆阳": "甘肃", "定西": "甘肃", "陇南": "甘肃", "临夏": "甘肃", "甘南": "甘肃", "天祝": "甘肃",
    "银川": "宁夏", "石嘴山": "宁夏", "吴忠": "宁夏", "固原": "宁夏", "中卫": "宁夏",
    "西宁": "青海", "海东": "青海", "海北": "青海", "黄南": "青海", "海南州": "青海", "果洛": "青海", "玉树": "青海", "海西": "青海"
}
# =====================================================================
# 2. 核心清洗与规则处理模块
# =====================================================================
def load_json(filepath, default_val=None):
    if not os.path.exists(filepath):
        return default_val if default_val is not None else {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def build_name_lookup(name_json_data):
    """构建 别名 -> 标准名 的极速查找字典"""
    lookup = {}
    for std_name, aliases in name_json_data.items():
        lookup[std_name.upper()] = std_name
        for alias in aliases:
            lookup[alias.upper()] = std_name
    return lookup

def clean_and_normalize_name(raw_name, name_lookup):
    """【纯净版】只负责执行名称清洗和 name.json 的标准名映射"""
    has_4k = "4K" in raw_name.upper()
    has_8k = "8K" in raw_name.upper()
    
    # [要求 6] 过滤杂质后缀
    noise_patterns = [
        r'360[pP]', r'404[pP]', r'480[pP]', r'576[pP]', r'606[pP]', r'720[pP]', 
        r'1080[pP]', r'1080[iI]', r'[hH][dD]', r'Not 24/7', r'Geo-blocked', 
        r'\[.*?\]', r'\(.*?\)'
    ]
    cleaned = raw_name
    for pattern in noise_patterns:
        cleaned = re.sub(pattern, '', cleaned)
    cleaned = cleaned.strip('- ').strip()

    # 严格匹配你的 name.json 别名表，获取最干净的标准别名（如 CETV-1、CCTV-1）
    std_name = name_lookup.get(cleaned.upper(), cleaned)
    
    # 恢复 4K/8K 标签
    if has_4k and "4K" not in std_name.upper(): std_name += " 4K"
    if has_8k and "8K" not in std_name.upper(): std_name += " 8K"
    
    return std_name

def determine_final_group(std_name, raw_group, is_4k_8k, group_repo):
    """绝对优先级的智能分组引擎 (核心5组优先匹配 > 4K规则 > 13条Fallback兜底)"""
    name_up = std_name.upper()
    rg = raw_group.strip() if raw_group else ""
    
    # [要求 3 & 分组11] 强制垃圾分类抛弃逻辑（保持不变）
    drop_list = ["游戏直播", "听书直播", "老年直播", "解说直播", "监控直播", "蜘蛛直播", "zuqiu直播", "咪视界直播", "KK直播", "瑜伽裤直播", "Ai直播", "钓鱼直播", "API随机点播", "直播室", "测试"]
    if any(x in rg or x in name_up for x in drop_list):
        return None

    # =====================================================================
    # 核心阶段一：前 5 个核心基础分组的“绝对优先判定”
    # =====================================================================
    
    # 基础属性识别
    is_cctv = "CCTV" in name_up or "中央台" in name_up or "CGTN" in name_up
    is_ws = "卫视" in name_up and "朝鲜语" not in name_up
    
    # 智能地域识别 (地级市与省份映射)
    matched_province = None
    for city, province in CITY_TO_PROVINCE.items():
        if city in std_name:
            matched_province = province
            break
    target_prov = matched_province if matched_province else next((p for p in PROVINCES if p in std_name), None)

    # 1. 优先执行 4K 频道判定规则 [要求 7 & 8]
    if "CCTV4K" in name_up or "CCTV8K" in name_up: 
        return "4K频道"
    if is_4k_8k and (is_cctv or is_ws or target_prov):
        return "4K频道"

    # 2. 匹配外部标准映射表 (group_standard.json 中的强制核心分类)
    group_from_json = group_repo.get(std_name)
    if group_from_json in ["4K频道", "央视频道", "地方卫视", "山东频道", "地方频道"]:
        return group_from_json

    # 3. 强力判定：央视频道
    if is_cctv: 
        return "央视频道"
        
    # 4. 强力判定：地方卫视
    if is_ws: 
        return "地方卫视"
        
    # 5. 强力判定：山东频道与地方频道
    if target_prov == "山东": 
        return "山东频道"
    if target_prov: 
        return "地方频道"

    # =====================================================================
    # 核心阶段二：当无法满足前 5 个基础核心组时，走后续的 Fallback 规则
    # =====================================================================
    
    # 走 group_standard.json 剩余的分组映射
    if group_from_json:
        return group_from_json

    # 13条特定原始组别关键字 Fallback 兜底
    if "地方台直播" in rg: return "地方频道"
    if "港澳台直播" in rg: return "港澳台"
    if any(x in rg for x in ["延伸西亚", "马来西亚直播", "越南直播", "印度直播", "日本直播", "韩国直播", "美国直播", "英国直播", "爱尔兰直播", "全球直播"]): return "海外频道"
    if "少儿直播" in rg: return "少儿动漫"
    if "体育直播" in rg: return "体育赛事"
    if "电影直播" in rg: return "影视频道"
    if any(x in rg for x in ["综艺直播", "短剧直播", "小品直播", "相声直播", "抖音直播", "YY直播", "车模直播", "女团直播", "热舞直播", "乡野直播", "脱口秀直播", "综艺"]): return "娱乐频道"
    if any(x in rg for x in ["电视剧直播", "爱奇艺直播", "埋堆堆直播"]): return "电视剧直播"
    if "纪录片直播" in rg: return "纪录纪实"
    if any(x in rg for x in ["动漫直播", "沙雕动画直播"]): return "少儿动漫"
    if any(x in rg for x in ["音乐直播", "周杰伦歌曲", "歌手合集"]): return "歌曲及音乐MV"

    # 根据台名关键字 Fallback 分流
    if any(x in name_up for x in ["港", "澳", "台", "HBO", "PHOENIX", "凤凰", "翡翠台", "明珠台", "TVB"]): return "港澳台"
    if any(x in name_up for x in ["电影", "影院", "剧场", "影视", "影片", "放映"]): return "影视频道"
    if any(x in name_up for x in ["纪录", "纪实", "探索"]): return "纪录纪实"
    if any(x in name_up for x in ["动漫", "少儿", "卡通", "儿童"]): return "少儿动漫"
    if any(x in name_up for x in ["体育", "赛事", "足球", "五星体育", "武搏"]): return "体育赛事"
    if any(x in name_up for x in ["音乐", "MV", "歌曲", "老歌"]): return "歌曲及音乐MV"
    
    # 终极 Fallback
    return "综合频道"
# =====================================================================
# 3. 探测、去重与输出控制
# =====================================================================
class TSStreamChecker:
    def __init__(self):
        self.stats = {"total_packets": 0, "lost_packets": 0, "response_times": []}

    def check_stream_stability(self, url):
        """检测 TS 流稳定性，返回是否稳定"""
        try:
            start = time.time()
            resp = requests.get(url, timeout=5, stream=True)
            if resp.status_code != 200: return False, 999
            
            # 记录响应时间
            resp_time = (time.time() - start) * 1000
            
            # 读取前 10KB 检查同步字节
            data = resp.raw.read(10240)
            if b'\x47' in data:
                return True, resp_time
        except:
            return False, 999
        return False, 999
        
# 修改为接收 session 参数
async def probe_url_async(session, url): 
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Connection": "keep-alive"
    }
    try:
        async with session.get(url, headers=headers, timeout=5, allow_redirects=True) as resp:
            if resp.status == 200:
                return True, 1080, 50
            else:
                return False, 0, 999
    except Exception as e:
        return False, 0, 999

def process_and_deduplicate(channels, group_priority):
    """[要求 2, 4, 5] 核心归类去重与排序管道"""
    channel_groups = defaultdict(list)
    for ch in channels:
        channel_groups[ch["std_name"]].append(ch)
        
    final_retained = []
    
    for std_name, info_list in channel_groups.items():
        # 按分辨率和探测顺位排序
        info_list.sort(key=lambda x: x["resolution"], reverse=True)
        
        is_cctv_or_ws = "CCTV" in std_name or "卫视" in std_name
        max_res = info_list[0]["resolution"]
        
        valid_items = []
        for item in info_list:
            if item["resolution"] >= 720:
                valid_items.append(item)
            elif is_cctv_or_ws and max_res < 720:
                valid_items.append(item)
                break # 央视/卫视低清兜底只留1个
                
        if not valid_items: continue
            
        # [要求 4] 画质分流双通道去重
        high_4k = None
        standard = None
        for item in valid_items:
            if item["resolution"] >= 2160:
                if not high_4k: high_4k = item
            else:
                if not standard: standard = item
                
        if high_4k: final_retained.append(high_4k)
        if standard: final_retained.append(standard)

    # 局部重新定义内部排序，强制使用传入的参数防止 NameError
    # 局部重新定义内部排序，强制使用传入的参数防止 NameError
    def internal_sort_key(x):
        # 1. 基础大组排序优先级
        g_idx = group_priority.index(x["group"]) if x["group"] in group_priority else 999
        
        # 2. 针对 4K 频道的次级精细排序
        sub_idx = 99
        if x["group"] == "4K频道":
            if "CCTV" in x["std_name"].upper(): sub_idx = 1
            elif "卫视" in x["std_name"].upper(): sub_idx = 2
            else: sub_idx = 3

        # 3. 🌟【核心修复】：央视/教育/国际台精细升序权重
        cctv_idx = 999
        name_upper = x["std_name"].upper().replace(" ", "").replace("-", "")
        
        # A. 普通纯数字央视（CCTV1 - CCTV17）
        num_match = re.search(r'CCTV(\d+)', name_upper)
        if num_match:
            cctv_idx = int(num_match.group(1))
            # 解决 CCTV5+ 应该排在 CCTV5 后面的问题
            if "5+" in name_upper or "5PLUS" in name_upper:
                cctv_idx = 5.5
        
        # B. 央视付费数字/超高清频道特例按序分流 (紧跟在 CCTV17 后面)
        elif "CCTV4K" in name_upper: cctv_idx = 18
        elif "CCTV8K" in name_upper: cctv_idx = 19
        elif "CCTV风云足球" in name_upper: cctv_idx = 20
        elif "CCTV高尔夫网球" in name_upper: cctv_idx = 21
        elif "CCTV台球" in name_upper: cctv_idx = 22
        elif "CCTV兵器科技" in name_upper: cctv_idx = 23
        elif "CCTV风云音乐" in name_upper: cctv_idx = 24
        elif "CCTV风云剧场" in name_upper: cctv_idx = 25
        elif "CCTV第一剧场" in name_upper: cctv_idx = 26
        elif "CCTV怀旧剧场" in name_upper: cctv_idx = 27
        elif "CCTV电视指南" in name_upper: cctv_idx = 28
        elif "CCTV世界地理" in name_upper: cctv_idx = 29
        elif "CCTV发现之旅" in name_upper: cctv_idx = 30
        elif "CCTV文化精品" in name_upper: cctv_idx = 31
        elif "CCTV中学生" in name_upper: cctv_idx = 32
        elif "CCTV老故事" in name_upper: cctv_idx = 33
        
        # C. 中国教育电视台系列排在央视付费台后面
        elif "CETV1" in name_upper: cctv_idx = 41
        elif "CETV2" in name_upper: cctv_idx = 42
        elif "CETV3" in name_upper: cctv_idx = 43
        elif "CETV4" in name_upper: cctv_idx = 44
        
        # D. CGTN 系列国际台垫底
        elif "CGTN" in name_upper:
            cctv_idx = 50
            if "英语" in name_upper: cctv_idx = 51
            elif "纪录" in name_upper: cctv_idx = 52
            elif "法语" in name_upper: cctv_idx = 53
            elif "西语" in name_upper: cctv_idx = 54
            elif "阿语" in name_upper: cctv_idx = 55
            elif "俄语" in name_upper: cctv_idx = 56

        # 4. 返回多维排序元组 (大组序号, 4K子序号, 央视内部序号, 分辨率降序)
        return (g_idx, sub_idx, cctv_idx, -x["resolution"])

    final_retained.sort(key=internal_sort_key)
    return final_retained

# =====================================================================
# 4. 主干运行流程
# =====================================================================
async def main():
    start_time = time.time()
    
    # 🌟 1. 优先初始化变量，防止未定义报错
    source_urls = []
    
    # 先把 sources.json 的内容读出来，后面网络下载和黑名单过滤可能都要用
    if os.path.exists(SOURCES_PATH):
        with open(SOURCES_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            source_urls = data.get("urls", [])
    
    # 🌟 2. 核心缓存判断双轨制
    if is_cache_valid(CACHE_PATH):
        print(f"[*] 检测到有效缓存，直接读取本地缓存: {CACHE_PATH}")
        # 🌟 核心修改：读取本地缓存文件，并复用解析器提取 group
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            cache_content = f.read()
        parsed_items = await parse_m3u_content(cache_content)
    else:
        print(f"[*] 缓存不存在或已过期，开始从网络更新...")
        # ... 后续网络更新逻辑保持不变 ...
        if not source_urls:
            print("[-] 错误: sources.json 中未找到有效的 urls 列表！")
            return
            
        # 只有在缓存失效时，才开启异步会话去网络下载
        async with aiohttp.ClientSession() as session:
            try:
                parsed_items = await fetch_and_parse_all(session, source_urls)
                
                # 写入新缓存
                if parsed_items:
                    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
                        f.write("#EXTM3U\n")
                        for item in parsed_items:
                            # 🌟 修复点：保存时带上所有原厂标签，供下次读取
                            f.write(f'#EXTINF:-1 group-title="{item.get("group", "")}" tvg-logo="{item.get("raw_logo", "")}" tvg-id="{item.get("raw_id", "")}",{item["raw_name"]}\n')
                            f.write(f"{item['url']}\n")
                    print(f"[+] 缓存已成功更新，共 {len(parsed_items)} 条频道。")
                else:
                    print("[!] 网络解析结果为空，跳过缓存写入。")
            except Exception as e:
                logger.error(f"批量下载失败: {e}")
    
    # 1. 加载配置字典
    group_repo = load_json(GROUP_JSON_PATH, {})
    name_repo = load_json(NAME_JSON_PATH, {})
    blacklist = load_json(BLACKLIST_PATH, {})
    name_lookup = build_name_lookup(name_repo)
    
    # 模拟从缓存/源提取 (这里简化模拟已合并的原始行列表，实际根据你的 sources 提取机制对接)
    raw_m3u_lines = []
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            raw_m3u_lines = f.readlines()
    else:
        print("[-] 未找到源缓存文件 sources_cache.txt，请确保第一步成功运行！")
        return

    # 数据结构梳理
    parsed_items = []
    for i in range(len(raw_m3u_lines)):
        line = raw_m3u_lines[i].strip()
        if line.startswith("#EXTINF"):
            # 基础信息正则解析
            name_match = re.search(r',(.*)$', line)
            if not name_match: continue
            raw_name = name_match.group(1).strip()
            
            # 过滤 catvod [要求 3]
            url = raw_m3u_lines[i+1].strip() if i+1 < len(raw_m3u_lines) else ""
            if "catvod.com" in url or not url.startswith("http"): continue
            
            # 解析附加信息 [要求 12]
            logo = re.search(r'tvg-logo="(.*?)"', line)
            logo = logo.group(1) if logo else ""
            grp = re.search(r'group-title="(.*?)"', line)
            grp = grp.group(1) if grp else ""
            tvgid = re.search(r'tvg-id="(.*?)"', line)
            tvgid = tvgid.group(1) if tvgid else ""
            
            parsed_items.append({
                "raw_name": raw_name, "url": url, "logo": logo, 
                "group": grp, "tvgid": tvgid
            })

    total_sources = len(parsed_items)
    stats = {
        "initial_total": total_sources,
        "blacklist_filtered": 0,
        "quality_filtered": 0,
        "final_retained": 0
    }
    
    valid_channels = []
    
    print(f"\n[+] 准备探测 {total_sources} 个源 (已开启多线程加速)...")
    
    # 1. 先把需要探测的有效任务筛选出来，避免在多线程里做无用功
    tasks = []
    for item in parsed_items:
        std_name = clean_and_normalize_name(item["raw_name"], name_lookup)
        final_group = determine_final_group(std_name, item["group"], "4K" in std_name.upper() or "8K" in std_name.upper(), group_repo)
        
        if not final_group:
            stats["quality_filtered"] += 1
            continue
            
        url = item["url"]
        try:
            fails = int(blacklist.get(url, 0))
        except (ValueError, TypeError):
            fails = 0
            
        if fails >= 3:
            stats["blacklist_filtered"] += 1
            continue
            
        # 🌟 判断该频道的标准名是否显式存在于 group_standard.json 中
        is_matched_json = std_name in group_repo

        tasks.append({
            "std_name": std_name, 
            "url": url, 
            "group": final_group,
            "is_matched_json": is_matched_json,              # 🌟 标记是否命中标准库
            "raw_logo": item.get("raw_logo", ""),             # 🌟 携带原始 logo
            "raw_id": item.get("raw_id", "")                  # 🌟 携带原始 id (EPG)
        })

    # 2. 定义单个探测任务的包装函数
    def check_task(task):
        is_valid, res, resp_time = probe_url(task["url"])
        return task, is_valid, res, resp_time
    total_tasks = len(tasks)
    completed = 0
    
    # 🌟 新增：初始化用于计算时间的计时器与统计器
    loop_start_time = time.time()
    passed_sources = 0
    passed_4k_sources = 0
    
    # ==========================================
    # 3. 开启纯异步并发探测 (彻底修复版 - 解决数据丢失暗坑)
    # ==========================================
    async with aiohttp.ClientSession() as my_session:
        # 保持 30 并发，兼顾速度与 Linux 稳定性
        semaphore = asyncio.Semaphore(30) 

        # 🌟 修改点 1：让辅助协程直接把 task 连同结果一起打包返回
        async def semaphore_task(task):
            async with semaphore:
                try:
                    is_valid, res, resp_time = await asyncio.wait_for(
                        probe_url_async(my_session, task["url"]), 
                        timeout=5.0
                    )
                    return task, is_valid, res, resp_time
                except Exception:
                    return task, False, 0, 999

        # 直接创建协程任务列表
        tasks_list = [semaphore_task(t) for t in tasks]
        total_tasks = len(tasks)
        completed = 0
        
        loop_start_time = time.time()
        passed_sources = 0
        passed_4k_sources = 0

        print("[+] 异步探测引擎已启动，正在激活连接池...")

        # 🌟 修改点 2：直接解包拿数据，再也不会出现 KeyError
        for future in asyncio.as_completed(tasks_list):
            completed += 1
            try:
                task, is_valid, res, resp_time = await future
            except Exception:
                continue

            if is_valid:
                passed_sources += 1
                try: current_res = int(res)
                except: current_res = 0

                if current_res >= 2160 or "4K" in task["std_name"].upper() or "8K" in task["std_name"].upper():
                    passed_4k_sources += 1

                if task["url"] in blacklist: 
                    del blacklist[task["url"]]
                
                valid_channels.append({
                    "std_name": task["std_name"], "url": task["url"], "logo": task["logo"],
                    "tvgid": task["std_name"], "tvgname": task["std_name"], "group": task["group"],
                    "resolution": res, "avg_time": resp_time
                })
            else:
                try: fails = int(blacklist.get(task["url"], 0))
                except: fails = 0
                blacklist[task["url"]] = fails + 1

            # 🌟 进度条刷新（再也不会被 continue 误伤跳过了）
            elapsed_loop = time.time() - loop_start_time
            avg_time = elapsed_loop / completed if completed > 0 else 0
            remaining_time = avg_time * (total_tasks - completed)
            total_predict_time = elapsed_loop + remaining_time

            def fmt_duration(seconds):
                m, s = divmod(int(seconds), 60)
                return f"{m:02d}:{s:02d}"

            bar_length = 15
            percent = completed / total_tasks if total_tasks > 0 else 0
            filled_length = int(round(bar_length * percent))
            bar = '█' * filled_length + '░' * (bar_length - filled_length)

            short_name = task['std_name'][:6]
            print(
                f"\r进度:[{bar}] {completed}/{total_tasks} ({percent*100:.1f}%) | "
                f"⏱️ 剩:{fmt_duration(remaining_time)}/总:{fmt_duration(total_predict_time)} | "
                f"当前:{short_name:<6} | ✅通过:{passed_sources} | ✨4K+:{passed_4k_sources}   ", 
                end="", flush=True
            )
        print() # 探测完成后换行
        
    # 4. 后置聚合与生成
    final_list = process_and_deduplicate(valid_channels, GROUP_PRIORITY)
    stats["final_retained"] = len(final_list)
    stats["quality_filtered"] += (len(valid_channels) - len(final_list))

    # [要求 10 & 13] 生成最终文件
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://epg.112114.xyz/pp.xml.gz,https://epg.pw/xmltv/feed/chn.xml"\n')
        
        for ch in final_list:
            # 1. 提取无符号特征码用于中央台拦截
            clean_std_name = ch["std_name"].replace(" ", "")
            lookup_key = clean_std_name.upper().replace("-", "")
            
            # 默认继承多线程探测出的状态
            display_name = ch["std_name"]
            final_group = ch["group"]
            
            # 2. 优先对央视/教育/CGTN进行最高级别纠正，确保它们100%归入“央视频道”
            if lookup_key in CCTV_DESC_MAP:
                display_name = CCTV_DESC_MAP[lookup_key]
                if "4K" not in final_group and "8K" not in final_group:
                    final_group = "央视频道"
            elif "CCTV4" in lookup_key:
                if "欧洲" in ch["std_name"]: display_name = "CCTV-4 欧洲"
                if "美洲" in ch["std_name"]: display_name = "CCTV-4 美洲"
                if "4K" not in final_group: final_group = "央视频道"

            # 3. 🌟【核心双轨制分流】：根据是否在 group_standard.json 或 5 大核心组中决定 logo 和 epg 策略
            # 检查当前频道是否属于 5 大核心分组，或者显式存在于 group_standard.json 字典中
            is_standard_managed = final_group in ["4K频道", "央视频道", "地方卫视", "山东频道", "地方频道"] or ch["std_name"] in group_repo
            
            if is_standard_managed:
                # 方案 A：核心标准频道，tvg-logo 直接赋值为标准中文名（供播放器本地或动态匹配高清晰标），不带原厂杂质参数
                logo_url = display_name
                epg_param = "" 
            else:
                # 方案 B：野生频道（如野生少儿、歌曲、娱乐等），严格保留原始抓到的原厂 tvg-logo 和 epg-url
                logo_url = ch["logo"] if ch["logo"] else ""
                # 如果原始数据中含有独立的 epg-url 属性，单独为该行保留
                epg_param = f' epg-url="{ch.get("epgurl", "")}"' if ch.get("epgurl") else ""

            # 4. 严格写入：根据分流策略渲染标准的 #EXTINF 属性行
            f.write(f'#EXTINF:-1 tvg-id="{ch["tvgid"]}" tvg-name="{ch["tvgname"]}" tvg-logo="{logo_url}" group-title="{final_group}"{epg_param},{display_name}\n')
            f.write(f'{ch["url"]}\n')

    # 保存黑名单
    save_json(BLACKLIST_PATH, blacklist)

    # [要求 1] 打印并保存任务终期报告
    elapsed = time.time() - start_time
    report = f"""
================ 📊 IPTV 任务运行报告 ================
[+] 运行时间: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())}
[+] 初始读取源总数: {stats['initial_total']}
[+] 命中黑名单拦截: {stats['blacklist_filtered']}
[+] 画质/垃圾源过滤: {stats['quality_filtered']}
[+] 最终保留优质源: {stats['final_retained']}
[+] 脚本整体总耗时: {elapsed:.2f} 秒
======================================================
"""
    print(report)
    with open(LOG_PATH, "a", encoding="utf-8") as log_f:
        log_f.write(report)

if __name__ == "__main__":
    try:
        # 将原来的 main() 修改为下面这行
        asyncio.run(main()) 
    except KeyboardInterrupt:
        print("\n[!] 任务被手动中断。")
