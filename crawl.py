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
from collections import defaultdict

# 初始化日志配置（你现有的代码）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("crawl.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 🌟【第二道保险】：为全局根记录器注入动态过滤器，只要日志文本包含 shielded future 瞬间蒸发
class SilenceShieldedFutureFilter(logging.Filter):
    def filter(self, record):
        log_msg = record.getMessage()
        if "shielded future" in log_msg or "gaierror" in log_msg:
            return False  # 返回 False 代表拒绝这条日志写入文件和终端
        return True

logging.getLogger().addFilter(SilenceShieldedFutureFilter())
# =====================================================================
# 检查源列表有效期
# =====================================================================
def is_cache_valid(cache_path, max_age_days=0.5):
    """检查缓存文件是否存在且是否在有效期内(默认0.5天即12小时)"""
    if not os.path.exists(cache_path):
        return False
    file_time = os.path.getmtime(cache_path)
    delta_days = (time.time() - file_time) / (3600 * 24)
    return delta_days < max_age_days

# =====================================================================
# 下载远程源列表与属性精确抓取
# =====================================================================
async def parse_m3u_content(content):
    """从 M3U 文本中全面捕获频道名称、URL、原始分组以及原厂的 logo、id、epg-url"""
    items = []
    lines = content.splitlines()
    current_item = {}
    
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF"):
            grp_match = re.search(r'group-title="(.*?)"', line)
            grp = grp_match.group(1) if grp_match else ""
            
            logo_match = re.search(r'tvg-logo="(.*?)"', line)
            raw_logo = logo_match.group(1) if logo_match else ""
            
            id_match = re.search(r'tvg-id="(.*?)"', line)
            raw_id = id_match.group(1) if id_match else ""
            
            # 🌟 核心修复：单独捕获原厂自带的特异性 epg-url
            epg_match = re.search(r'epg-url="(.*?)"', line)
            raw_epg = epg_match.group(1) if epg_match else ""
            
            name_match = re.search(r',(.*)$', line)
            name = name_match.group(1) if name_match else ""
            
            current_item = {
                "raw_name": name.strip(), 
                "group": grp.strip(),
                "raw_logo": raw_logo.strip(),
                "raw_id": raw_id.strip(),
                "raw_epg": raw_epg.strip()
            }
        elif line.startswith("http") and current_item:
            current_item["url"] = line
            items.append(current_item)
            current_item = {}
            
    return items

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
    
    required_packages = ["requests", "aiohttp"]
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

# =====================================================================
# 1. 全局配置与路径初始化
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(BASE_DIR, "sources.json")
CACHE_PATH = os.path.join(BASE_DIR, "sources_cache.txt") 
BLACKLIST_PATH = os.path.join(BASE_DIR, "blacklist.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "tv.m3u")
LOG_PATH = os.path.join(BASE_DIR, "crawl.log")

GROUP_JSON_PATH = os.path.join(BASE_DIR, "group", "group_standard.json")
NAME_JSON_PATH = os.path.join(BASE_DIR, "iptvname", "name.json")

CCTV_DESC_MAP = {
    "CCTV1": "CCTV-1 综合", "CCTV2": "CCTV-2 财经", "CCTV3": "CCTV-3 综艺",
    "CCTV4": "CCTV-4 中文国际", "CCTV5": "CCTV-5 体育", "CCTV5+": "CCTV-5+ 体育赛事",
    "CCTV6": "CCTV-6 电影", "CCTV7": "CCTV-7 国防军事", "CCTV8": "CCTV-8 电视剧",
    "CCTV9": "CCTV-9 纪录", "CCTV10": "CCTV-10 科教", "CCTV11": "CCTV-11 戏曲",
    "CCTV12": "CCTV-12 社会与法", "CCTV13": "CCTV-13 新闻", "CCTV14": "CCTV-14 少儿",
    "CCTV15": "CCTV-15 音乐", "CCTV16": "CCTV-16 奥林派克", "CCTV17": "CCTV-17 农业农村",
    "CCTV4K": "CCTV4K 超高清", "CCTV8K": "CCTV8K 超高清", "CCTV5PLUS": "CCTV-5+ 体育赛事",
    
    "CCTV兵器科技": "CCTV 兵器科技", "CCTV风云足球": "CCTV 风云足球", "CCTV高尔夫网球": "CCTV 高尔夫网球",
    "CCTV风云音乐": "CCTV 风云音乐", "CCTV风云剧场": "CCTV 风云剧场", "CCTV第一剧场": "CCTV 第一剧场",
    "CCTV怀旧剧场": "CCTV 怀旧剧场", "CCTV大国健康": "CCTV 大国健康", "CCTV央视台球": "CCTV 央视台球",
    "CCTV女性时尚": "CCTV 女性时尚", "CCTV世界地理": "CCTV 世界地理", "CCTV央视文化精品": "CCTV 央视文化精品",
    "CCTV电视指南": "CCTV 电视指南", "CCTV发现之旅": "CCTV 发现之旅", "CCTV中学生": "CCTV 中学生",
    "CCTV老故事": "CCTV 老故事","CCTV4欧洲": "CCTV4 欧洲","CCTV4美洲": "CCTV4 美洲","CCTV卫生健康": "CCTV 卫生健康",
    
    "CETV1": "中国教育-1", "CETV2": "中国教育-2", "CETV3": "中国教育-3", "CETV4": "中国教育-4",
    
    "CGTN英语": "CGTN 英语", "CGTN纪录": "CGTN 纪录", 
    "CGTN法语": "CGTN 法语", "CGTN西语": "CGTN 西语", 
    "CGTN阿语": "CGTN 阿语", "CGTN俄语": "CGTN 俄语"
}

PRIORITY_GROUPS = ["4K频道", "央视频道", "地方卫视", "山东频道", "地方频道"]
PROVINCES = ["北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆"]
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

    std_name = name_lookup.get(cleaned.upper(), cleaned)
    
    if has_4k and "4K" not in std_name.upper(): std_name += " 4K"
    if has_8k and "8K" not in std_name.upper(): std_name += " 8K"
    
    return std_name

def determine_final_group(std_name, raw_group, is_4k_8k, group_repo):
    """绝对优先级的智能分组引擎 (核心5组优先匹配 > 4K规则 > 13条Fallback兜底)"""
    name_up = std_name.upper()
    rg = raw_group.strip() if raw_group else ""
    
    drop_list = ["游戏直播", "听书直播", "老年直播", "解说直播", "监控直播", "蜘蛛直播", "zuqiu直播", "咪视界直播", "KK直播", "瑜伽裤直播", "Ai直播", "钓鱼直播", "API随机点播", "直播室", "测试"]
    if any(x in rg or x in name_up for x in drop_list):
        return None

    is_cctv = "CCTV" in name_up or "中央台" in name_up or "CGTN" in name_up
    is_ws = "卫视" in name_up and "朝鲜语" not in name_up
    
    matched_province = None
    for city, province in CITY_TO_PROVINCE.items():
        if city in std_name:
            matched_province = province
            break
    target_prov = matched_province if matched_province else next((p for p in PROVINCES if p in std_name), None)

    # 🌟【核心修复】：只要名称带有显式 4K/8K 标签，无条件直接归入“4K频道”，防止 CHC电影4K 等野生优质源漏网
    if "CCTV4K" in name_up or "CCTV8K" in name_up or is_4k_8k: 
        return "4K频道"

    group_from_json = group_repo.get(std_name)
    if group_from_json in ["4K频道", "央视频道", "地方卫视", "山东频道", "地方频道"]:
        return group_from_json

    if is_cctv: 
        return "央视频道"
    if is_ws: 
        return "地方卫视"
    if target_prov == "山东": 
        return "山东频道"
    if target_prov: 
        return "地方频道"

    if group_from_json:
        return group_from_json

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

    if any(x in name_up for x in ["港", "澳", "台", "HBO", "PHOENIX", "凤凰", "翡翠台", "明珠台", "TVB"]): return "港澳台"
    if any(x in name_up for x in ["电影", "影院", "剧场", "影视", "影片", "放映"]): return "影视频道"
    if any(x in name_up for x in ["纪录", "纪实", "探索"]): return "纪录纪实"
    if any(x in name_up for x in ["动漫", "少儿", "卡通", "儿童"]): return "少儿动漫"
    if any(x in name_up for x in ["体育", "赛事", "足球", "五星体育", "武搏"]): return "体育赛事"
    if any(x in name_up for x in ["音乐", "MV", "歌曲", "老歌"]): return "歌曲及音乐MV"
    
    return "综合频道"

# =====================================================================
# 3. 异步轻量级多维测速探测引擎
# =====================================================================
async def probe_url_async(session, url): 
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Connection": "keep-alive"
    }
    try:
        async with session.get(url, headers=headers, timeout=4, allow_redirects=True) as resp:
            if resp.status == 200:
                lower_url = url.lower()
                detected_res = 1080  # 基础默认值
                
                # 🌟【核心修复】：完全取消从 URL 文本猜测 4K，只保留常规高清/标清的文本大致分流
                if "720" in lower_url:
                    detected_res = 720
                elif "576" in lower_url or "480" in lower_url:
                    detected_res = 480
                
                return True, detected_res, 60
            else:
                return False, 0, 999
    except Exception:
        return False, 0, 999

# =====================================================================
# 4. 主干运行流程
# =====================================================================
async def main():
    start_time = time.time()
    # 🌟【终极修复】：接管 asyncio 事件循环的全局异常处理器，彻底封杀 shielded future 刷屏
    loop = asyncio.get_running_loop()
    def custom_loop_exception_handler(loop, context):
        msg = context.get("message", "")
        exception = context.get("exception")
        # 只要发现是 aiohttp 那个甩锅给事件循环的后台 DNS 报错，直接无视并丢弃
        if "shielded future" in msg or "gaierror" in str(exception) or "11001" in str(exception):
            return 
        # 其他真正的核心系统崩溃错误，依然放行交给标准处理器打印
        loop.default_exception_handler(context)
        
    loop.set_exception_handler(custom_loop_exception_handler)
    
    # 👇 下方继续保持你原有的逻辑：
    source_urls = []
    
    if os.path.exists(SOURCES_PATH):
        with open(SOURCES_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            source_urls = data.get("urls", [])
            
    # ==========================================
    # 2. 核心缓存判断与结构化提取双轨制
    # ==========================================
    parsed_items = []
    if is_cache_valid(CACHE_PATH, max_age_days=0.5):
        print(f"[*] 检测到有效缓存(12H内)，直接读取本地缓存: {CACHE_PATH}")
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            cache_content = f.read()
        parsed_items = await parse_m3u_content(cache_content)
    else:
        print(f"[*] 缓存不存在或已过期，开始从网络更新...")
        if not source_urls:
            print("[-] 错误: sources.json 中未找到有效的 urls 列表！")
            return
            
        async with aiohttp.ClientSession() as session:
            try:
                parsed_items = await fetch_and_parse_all(session, source_urls)
                if parsed_items:
                    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
                        f.write("#EXTM3U\n")
                        for item in parsed_items:
                            # 使用 f-string 拼接全量属性，确保所有数据都被固化到缓存中
                            f.write(f'#EXTINF:-1 group-title="{item.get("group", "")}" '
                                    f'tvg-logo="{item.get("raw_logo", "")}" '
                                    f'tvg-id="{item.get("raw_id", "")}" '
                                    f'epg-url="{item.get("raw_epg", "")}",'  # 🌟 修复：补齐了 epg-url
                                    f'{item["raw_name"]}\n')
                            f.write(f'{item["url"]}\n')
                    print(f"[+] 缓存已成功更新，共 {len(parsed_items)} 条频道。")
                else:
                    print("[!] 网络解析结果为空，跳过缓存写入。")
            except Exception as e:
                logger.error(f"批量下载失败: {e}")

    if not parsed_items:
        print("[-] 错误: 未能获取到任何有效的频道源数据，程序退出。")
        return
    # 🌟 满足要求 3 预洗过滤：统一全自动拦截清洗
    cleaned_parsed_items = []
    for item in parsed_items:
        url = item.get("url", "").strip()
        raw_name = item.get("raw_name", "").strip()
        
        url_lower = url.lower()
        name_upper = raw_name.upper()
        
        # A. 基础垃圾源过滤
        if "catvod.com" in url_lower or "直播室" in raw_name or not url.startswith("http"):
            continue
            
        # B. 🌟【核心新增】：彻底封杀所有 FM 广播、音频流域名 (蜻蜓FM/各类国家广播)
        if "qingting.fm" in url_lower or "radio" in url_lower or "64k.m3u8" in url_lower:
            continue
            
        # C. 🌟【核心新增】：通过台名关键字拦截野生广播电台 (防止漏网之鱼)
        if "FM" in name_upper or "广播" in name_upper or "调频" in name_upper or "之声" in name_upper:
            # 排除掉类似于“少儿动漫”里的某些带“声”的特定电视节目，其余全干掉
            if "CCTV" not in name_upper and "卫视" not in name_upper:
                continue
                
        cleaned_parsed_items.append(item)
    
    parsed_items = cleaned_parsed_items
    total_sources = len(parsed_items)
    
    stats = {
        "initial_total": total_sources,
        "blacklist_filtered": 0,
        "quality_filtered": 0,
        "final_retained": 0
    }
    
    # 🌟【核心修复】：将资源加载完全提至最前，杜绝下方循环读取时的 NameError 崩溃
    group_repo = load_json(GROUP_JSON_PATH, {})
    name_repo = load_json(NAME_JSON_PATH, {})
    blacklist = load_json(BLACKLIST_PATH, {})
    name_lookup = build_name_lookup(name_repo)

    valid_channels = []
    print(f"\n[+] 准备探测 {total_sources} 个源 (已开启多线程加速)...")
    
    # 筛选并填充有效任务
    tasks = []
    for item in parsed_items:
        std_name = clean_and_normalize_name(item["raw_name"], name_lookup)
        final_group = determine_final_group(std_name, item["group"], "4K" in std_name.upper() or "8K" in std_name.upper(), group_repo)
        
        if not final_group:
            stats["quality_filtered"] += 1
            continue
            
        url = item["url"]
        try: fails = int(blacklist.get(url, 0))
        except: fails = 0
            
        if fails >= 5:
            stats["blacklist_filtered"] += 1
            continue
            
        # 🌟【核心修复】：对齐 parse_m3u_content 产生的结构化键名，彻底打通数据管道
        tasks.append({
            "std_name": std_name, 
            "url": url, 
            "group": final_group,
            "raw_logo": item.get("raw_logo", ""),             
            "raw_id": item.get("raw_id", ""),                  
            "epgurl": item.get("raw_epg", "")  
        })

    total_tasks = len(tasks)
    completed = 0
    loop_start_time = time.time()
    passed_sources = 0
    passed_4k_sources = 0
    
    # ==========================================
    # 3. 开启纯异步并发探测
    # ==========================================
    # 🌟 缝合点：创建带 12H 内 DNS 内存缓存的连接池
    my_connector = aiohttp.TCPConnector(use_dns_cache=True, ttl_dns_cache=300, limit=30)

    async with aiohttp.ClientSession(connector=my_connector) as my_session:
        semaphore = asyncio.Semaphore(30) 

        # 🌟 完美的嵌套：让任务内部探针直接共享这个带缓存的 my_session
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
                    
        tasks_list = [semaphore_task(t) for t in tasks]
        print("[+] 异步探测引擎已启动，正在激活连接池...")

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
                    "std_name": task["std_name"], 
                    "url": task["url"], 
                    "logo": task["raw_logo"], 
                    "tvgid": task["raw_id"], 
                    "tvgname": task["raw_id"], 
                    "group": task["group"],
                    "epgurl": task["epgurl"], 
                    "resolution": res, 
                    "avg_time": resp_time
                })
            else:
                try: fails = int(blacklist.get(task["url"], 0))
                except: fails = 0
                blacklist[task["url"]] = fails + 1

            # 进度条单行强力刷新
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
        print() 
        
    # ==========================================
    # 4. 标准化、清洗、画质过滤与双轨制去重核心引擎
    # ==========================================
    print("[*] 探测结束，开始执行标准化命名与智能去重...")
    
    # 加载标准化别名查找表
    alias_to_std = {}
    if name_repo:
        for std_name, aliases in name_repo.items():
            for alias in aliases:
                alias_to_std[alias.replace(" ", "").upper()] = std_name
            alias_to_std[std_name.replace(" ", "").upper()] = std_name

    standardized_groups = defaultdict(list)

    for ch in valid_channels:
        # --- A. 清洗名称杂质 ---
        raw_name = ch["std_name"]
        clean_name = re.sub(
            r'(360P|404P|480P|576P|606P|720P|1080P|HD|FHD|Not 24/7|Geo-blocked)', 
            '', raw_name, flags=re.IGNORECASE
        ).strip()
        clean_name = re.sub(r'[\s_]+', ' ', clean_name).strip()
        
        # --- B. 匹配 name.json 确立标准大名 ---
        lookup_key = clean_name.replace(" ", "").upper()
        if lookup_key in alias_to_std:
            std_mapped_name = alias_to_std[lookup_key]
            ch["tvgid"] = std_mapped_name
            ch["tvgname"] = std_mapped_name
            ch["matched_std"] = std_mapped_name
        else:
            ch["tvgid"] = clean_name
            ch["tvgname"] = clean_name
            ch["matched_std"] = clean_name

        # --- C. 确定最终显示名称与中央台特正纠正 (防漏增强前缀匹配版) ---
        display_name = ch["matched_std"]
        norm_key = display_name.replace(" ", "").upper().replace("-", "")
        
        matched_desc = None
        if norm_key in CCTV_DESC_MAP:
            matched_desc = CCTV_DESC_MAP[norm_key]
        else:
            # 🌟【核心修复】：按长度从长到短降序排列键名，彻底杜绝 CCTV4 误拦截 CCTV4K、CCTV5 误拦截 CCTV5+
            for k in sorted(CCTV_DESC_MAP.keys(), key=len, reverse=True):
                if norm_key.startswith(k):
                    matched_desc = CCTV_DESC_MAP[k]
                    break
                elif norm_key.startswith(k) and k in ["CCTV1", "CCTV2", "CCTV3", "CCTV4", "CCTV5", "CCTV6", "CCTV7", "CCTV8", "CCTV9"]:
                    matched_desc = v
                    break

        if matched_desc:
            display_name = matched_desc
        elif "CCTV4" in norm_key:
            if "欧洲" in raw_name: display_name = "CCTV-4 欧洲"
            elif "美洲" in raw_name: display_name = "CCTV-4 美洲"
            else: display_name = "CCTV-4 中文国际"
            
        ch["display_name"] = display_name

        # --- D. 确定智能分组 group-title ---
        current_g = ch["group"]
        res_val = 0
        try: res_val = int(ch.get("resolution", 0))
        except: pass

        if "CCTV4K" in norm_key or "CCTV8K" in norm_key or res_val >= 2160:
            if "CCTV" in norm_key or "卫视" in current_g or current_g in ["地方卫视", "山东频道", "地方频道", "央视频道"]:
                current_g = "4K频道"
        ch["group"] = current_g

        standardized_groups[ch["matched_std"]].append(ch)

    # --- E. 强力去重与画质保底策略 ---
    final_retained_list = []
    
    for std_title, sources in standardized_groups.items():
        def get_res(x):
            try: return int(x.get("resolution", 0))
            except: return 0
        sources_sorted = sorted(sources, key=get_res, reverse=True)

        sources_4k = [s for s in sources_sorted if get_res(s) >= 2160]
        sources_normal = [s for s in sources_sorted if get_res(s) < 2160]

        best_4k = sources_4k[0] if sources_4k else None
        eligible_normal = [s for s in sources_normal if get_res(s) >= 720]
        best_normal = eligible_normal[0] if eligible_normal else None

        is_core_brand = "CCTV" in std_title or any(w in std_title for w in ["卫视", "教育", "CETV"])
        if not best_normal and is_core_brand and sources_normal:
            best_normal = sources_normal[0]

        if best_4k: final_retained_list.append(best_4k)
        if best_normal: final_retained_list.append(best_normal)

    # --- F. 定制权重与自然地理位置排序 (严格对齐要求 2 & 要求 9) ---
    # 完整劣后组权重序列
    POSTERIOR_GROUPS = ["少儿动漫", "港澳台", "影视频道", "歌曲及音乐MV", "纪录纪实", "娱乐频道", "电视剧直播", "海外频道", "体育赛事", "综合频道"]

    def natural_sort_transformer(ch):
        g = ch["group"]
        disp_name = ch["display_name"]
        name_up = disp_name.upper().replace(" ", "")

        # 1. 决定大组层面的全局权重
        if g in PRIORITY_GROUPS:
            g_weight = PRIORITY_GROUPS.index(g)  # 优先组权重 0 到 4
        elif g in POSTERIOR_GROUPS:
            g_weight = 5 + POSTERIOR_GROUPS.index(g)  # 劣后组权重 5 到 14
        else:
            g_weight = 99  # 未知大组垫底

        # 2. 决定 4K 组内部的次级排序 (4K频道按央、卫、地排序)
        sub_4k_weight = 99
        if g == "4K频道":
            if "CCTV" in name_up or "中央" in name_up or "CGTN" in name_up:
                sub_4k_weight = 0  
            elif "卫视" in name_up:
                sub_4k_weight = 1  
            else:
                sub_4k_weight = 2  

        # 3. 🌟【新增】：决定央视频道内部的精细梯队排序
        sub_cctv_tier = 99
        sub_cctv_num = 99
        
        if g == "央视频道":
            # 梯队 A：CCTV 1-17 核心数字台
            cctv_match = re.search(r'CCTV[-_]*(\d+)', name_up)
            if cctv_match:
                num = int(cctv_match.group(1))
                if 1 <= num <= 17:
                    sub_cctv_tier = 0
                    sub_cctv_num = num
            
            # 梯队 B：CGTN 系列国际台
            if sub_cctv_tier == 99 and "CGTN" in name_up:
                sub_cctv_tier = 1
                sub_cctv_num = 0  # CGTN内部默认按字母自然排
                
            # 梯队 C：中国教育电视台系列 (CETV)
            if sub_cctv_tier == 99 and any(x in name_up for x in ["中国教育", "CETV"]):
                sub_cctv_tier = 2
                cetv_match = re.search(r'(?:CETV|中国教育)[-_]*(\d+)', name_up)
                sub_cctv_num = int(cetv_match.group(1)) if cetv_match else 99
                
            # 梯队 D：其他央视台 (5+, 4K, 8K, 兵器科技等付费数字台)
            if sub_cctv_tier == 99 and "CCTV" in name_up:
                sub_cctv_tier = 3
                if "5+" in name_up or "5PLUS" in name_up:
                    sub_cctv_num = 5.5  # 特殊照顾 5+，使其在“其他”里置顶靠前
                else:
                    sub_cctv_num = 99

        # 4. 基础自然排序转换器（防 CCTV-10 跑到 CCTV-2 前面）
        split_segments = [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', disp_name)]
        
        # 返回全新多维矩阵特征码：大组 ➡️ 4K组小排 ➡️ 央视小梯队 ➡️ 央视内编号 ➡️ 基础自然序列
        return (g_weight, sub_4k_weight, sub_cctv_tier, sub_cctv_num, split_segments)

    final_retained_list.sort(key=natural_sort_transformer)

    # ==========================================
    # 5. 生成最终带有高聚合属性的 M3U 文件
    # ==========================================
    stats["final_retained"] = len(final_retained_list)
    stats["quality_filtered"] = len(valid_channels) - len(final_retained_list)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write('#EXTM3U x-tvg-url="https://epg.112114.xyz/pp.xml.gz,https://gitee.com/gsls200808/xmltvepg/raw/master/e9.xml.gz"\n')
        
        for ch in final_retained_list:
            final_group = ch["group"]
            tvg_id = ch["tvgid"]
            tvg_name = ch["tvgname"]
            display_name = ch["display_name"]

            # --- G. 确定 tvg-logo 与 epg-url 双轨制分流 ---
            if final_group in PRIORITY_GROUPS:
                clean_logo_id = tvg_id.replace(" ", "")
                logo_url = f"https://epg.112114.xyz/logo/{clean_logo_id}.png"
                epg_param = "" 
            else:
                logo_url = ch["logo"] if ch["logo"] else ""
                epg_param = f' epg-url="{ch.get("epgurl", "")}"' if ch.get("epgurl") else ""

            f.write(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{tvg_name}" tvg-logo="{logo_url}" group-title="{final_group}"{epg_param},{display_name}\n')
            f.write(f'{ch["url"]}\n')

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
        asyncio.run(main()) 
    except KeyboardInterrupt:
        print("\n[!] 任务被手动中断。")
