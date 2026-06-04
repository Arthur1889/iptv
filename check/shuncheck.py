import os
import re
import json
import asyncio
import time
import datetime
from collections import defaultdict
from typing import Dict, List, Optional, Any, Set
from urllib.parse import urlparse, urljoin
import numpy as np
import aiohttp
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def get_stb_headers(url: str) -> Dict[str, str]:
    return {
        "User-Agent": "CTC/1.0 (STB, Set-Top-Box; IPB/2.0)", 
        "Accept": "*/*",
        "Connection": "keep-alive",
        "X-Forwarded-For": f"210.13.111.{np.random.randint(1, 254)}",
        "Referer": "http://sh.unicomiptv.com/stb/boot"
    }

RESPONSE_TIME_THRESHOLD = 50  

# ======================================================
# 第一部分：硬核 TS 流体检模块
# ======================================================
class TSStreamChecker:
    def __init__(self, buffer_size: int = 8192, check_duration: int = 4):
        self.buffer_size = buffer_size
        self.check_duration = check_duration
        self.pid_continuity: Dict[int, int] = defaultdict(int)
        self.stats = {"total_packets": 0, "invalid_packets": 0, "lost_packets": 0, "rate_history": [], "response_times": []}
        self.last_check_time = time.time()
        self.packets_in_window = 0

    def parse_ts_packet(self, packet: bytes) -> Optional[Dict[str, Any]]:
        if len(packet) != 188 or packet[0] != 0x47: return None
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        return {"pid": pid, "continuity": packet[3] & 0x0F}

    async def check_stream(self, session, url: str) -> bool:
        self.pid_continuity.clear()
        self.stats = {"total_packets": 0, "invalid_packets": 0, "lost_packets": 0, "rate_history": [], "response_times": []}
        start_time = time.time()
        req_start = time.time()
        
        try:
            timeout = aiohttp.ClientTimeout(total=5, connect=2)
            async with session.get(url, headers=get_stb_headers(url), timeout=timeout) as response:
                if response.status != 200: return False
                ctype = response.headers.get('Content-Type', '').lower()
                if 'html' in ctype or 'text' in ctype: return False

                resp_time = (time.time() - req_start) * 1000
                self.stats["response_times"].append(resp_time)
                if resp_time > RESPONSE_TIME_THRESHOLD: return False

                buffer = b""
                async for chunk in response.content.iter_chunked(self.buffer_size):
                    if (time.time() - start_time) >= self.check_duration: break
                    if chunk: buffer += chunk
                    while len(buffer) >= 188:
                        packet = buffer[:188]
                        buffer = buffer[188:]
                        self.stats["total_packets"] += 1
                        self.packets_in_window += 1
                        parsed = self.parse_ts_packet(packet)
                        if not parsed:
                            self.stats["invalid_packets"] += 1
                            continue
                        
                        pid, c_counter = parsed["pid"], parsed["continuity"]
                        last_c = self.pid_continuity.get(pid, -1)
                        if last_c != -1:
                            lost = (c_counter - (last_c + 1) % 16) % 16
                            if lost > 0: self.stats["lost_packets"] += lost
                        self.pid_continuity[pid] = c_counter

                        cur_time = time.time()
                        if (cur_time - self.last_check_time) >= 1.0:
                            self.stats["rate_history"].append(self.packets_in_window / (cur_time - self.last_check_time))
                            self.packets_in_window = 0
                            self.last_check_time = cur_time

                if self.stats["total_packets"] < 20: return False
                loss_rate = self.stats["lost_packets"] / self.stats["total_packets"]
                rate_std = np.std(self.stats["rate_history"]) if self.stats["rate_history"] else 100
                return rate_std < 8 and loss_rate < 0.01
        except:
            return False

# ======================================================
# 第二部分：双模 C 段矩阵动态扩展
# ======================================================
async def generate_dual_mode_c_class_urls(url: str, scanned_nets: Set[str]) -> List[Dict[str, str]]:
    url_tasks = []
    parsed_url = urlparse(url)
    if not parsed_url.hostname: return url_tasks
    
    ip_parts = parsed_url.hostname.split('.')
    if len(ip_parts) != 4: return url_tasks
    
    port_str = f":{parsed_url.port}" if parsed_url.port else ""
    net_prefix = f"{'.'.join(ip_parts[:3])}{port_str}"
    
    if net_prefix in scanned_nets:
        return url_tasks
    scanned_nets.add(net_prefix)
    
    base_ip = '.'.join(ip_parts[:3])
    endpoint_tx = "/iptv/live/1000.json?key=txiptv"
    endpoint_zh = "/ZHGXTV/Public/json/live_interface.txt"
    
    for i in range(1, 254):
        full_ip = f"{base_ip}.{i}{port_str}"
        url_tasks.append({"url": f"{parsed_url.scheme}://{full_ip}{endpoint_tx}", "type": "txiptv", "host": f"{base_ip}.{i}", "port": parsed_url.port or 80})
        url_tasks.append({"url": f"{parsed_url.scheme}://{full_ip}{endpoint_zh}", "type": "zhgxtv", "host": f"{base_ip}.{i}", "port": parsed_url.port or 80})
    return url_tasks

def clean_channel_name(name: str) -> str:
    name = name.upper()
    rules = {
        "cctv": "CCTV", "中央": "CCTV", "央视": "CCTV", "高清": "", "超高": "", "HD": "", "标清": "", "频道": "",
        "*": "", "-": "", " ": "", "PLUS": "+", "＋": "+", "机制": "", "CCTV1综合": "CCTV1",
        "CCTV2财经": "CCTV2", "CCTV3综艺": "CCTV3", "CCTV4国际": "CCTV4", "CCTV4中文国际": "CCTV4",
        "CCTV5体育": "CCTV5", "CCTV6电影": "CCTV6", "CCTV7军事": "CCTV7", "CCTV7国防军事": "CCTV7",
        "CCTV8电视剧": "CCTV8", "CCTV9纪录": "CCTV9", "CCTV10科教": "CCTV10", "CCTV11戏曲": "CCTV11",
        "CCTV12社会与法": "CCTV12", "CCTV13新闻": "CCTV13", "CCTV新闻": "CCTV13", "CCTV14少儿": "CCTV14",
        "CCTV15音乐": "CCTV15", "CCTV16奥林匹克": "CCTV16", "CCTV17农业农村": "CCTV17", "CCTV5+体育赛事": "CCTV5+",
        "新闻综合": "上海新闻综合", "五星体育": "五星体育"
    }
    for old, new in rules.items(): name = name.replace(old, new)
    return re.sub(r"CCTV(\d+)台", r"CCTV\1", name)

# ======================================================
# 第三部分：双模异步流式收割机主逻辑
# ======================================================
async def main():
    start_time = time.time()
    logger.info("🎬 启动 [安全控速·双重过滤] 本地火种库并发探测引擎...")

    local_ips = set()
    base_hosts = set()
    all_exploit_tasks = []
    active_interfaces = []
    seen_live_urls = set()

    # 🌟 优化调整：降低限流阈值，对家用路由器更友好，彻底防止断网
    scan_semaphore = asyncio.Semaphore(150)  
    check_semaphore = asyncio.Semaphore(15)   
    
    # 🌟 引入核心锁与缓存器
    file_io_lock = asyncio.Lock()
    tcp_cache_lock = asyncio.Lock()
    tcp_check_cache: Dict[str, bool] = {} # 格式 -> "ip:port": True/False

    current_dir = os.path.dirname(os.path.abspath(__file__))
    interface_save_path = os.path.join(current_dir, 'live_interfaces.json')

    priority_tasks = []
    if os.path.exists(interface_save_path):
        try:
            with open(interface_save_path, 'r', encoding='utf-8') as jf:
                cache_nodes = json.load(jf).get("live_nodes", [])
                for node in cache_nodes:
                    if "url" in node and "type" in node:
                        p_parsed = urlparse(node["url"])
                        priority_tasks.append({
                            "url": node["url"], "type": node["type"], "is_priority": True,
                            "host": p_parsed.hostname, "port": p_parsed.port or 80
                        })
            logger.info(f"⚡ 【优先队列拦截】成功提取 {len(priority_tasks)} 个历史缓存节点，开局优先轰炸！")
        except: pass

    # 🌟 已移除了 FOFA 空间浏览器探测源，完全依赖本地 urls.json
    urls_file_path = os.path.join(current_dir, 'urls.json')
    if os.path.exists(urls_file_path):
        try:
            with open(urls_file_path, 'r', encoding='utf-8') as f:
                local_ips = set(json.load(f).get("urls", []))
                logger.info(f"📂 成功加载本地火种库，共读取 {len(local_ips)} 个基础节点。")
        except: pass

    if not local_ips and not priority_tasks:
        logger.error("❌ 本地 urls.json 库与缓存皆为空，无探测源目标，脚本退出。")
        return

    for url in local_ips:
        url = url.strip()
        if not url: continue
        try:
            parsed = urlparse(url)
            if parsed.hostname:
                port = f":{parsed.port}" if parsed.port else ""
                base_hosts.add(f"{parsed.scheme}://{parsed.hostname}{port}")
        except: pass

    logger.info(f"📡 正在对 {len(base_hosts)} 个主集群进行全 C 段网段矩阵剪枝生成...")
    scanned_nets = set()  
    for p_task in priority_tasks:
        try:
            p_parts = p_task["host"].split('.')
            if len(p_parts) == 4:
                scanned_nets.add(f"{'.'.join(p_parts[:3])}:{p_task['port']}")
        except: pass

    blind_tasks = []
    for host_url in base_hosts:
        tasks_extended = await generate_dual_mode_c_class_urls(host_url, scanned_nets)
        blind_tasks.extend(tasks_extended)

    all_exploit_tasks = priority_tasks + blind_tasks
    total_tasks = len(all_exploit_tasks)
    logger.info(f"🔥 网段去重剪枝完毕！优先通道: {len(priority_tasks)} 个，常规爆破: {len(blind_tasks)} 个。总任务矩阵: {total_tasks}")

    # 双重过滤探测器闭包
    async def check_interface_node(session_obj, task_node):
        async with scan_semaphore:
            target_url = task_node["url"]
            mode = task_node["type"]
            host = task_node["host"]
            port = int(task_node["port"])
            is_priority = task_node.get("is_priority", False)
            cache_key = f"{host}:{port}"
            
            # 1. 第一层过滤：TCP 状态智能复用层（省流不崩溃的关键）
            if not is_priority:
                async with tcp_cache_lock:
                    cached_status = tcp_check_cache.get(cache_key)
                
                # 🌟 如果另一个模式的任务已经探明该 IP 死亡，直接无条件拦截，不发任何网络报文
                if cached_status is False:
                    return None
                
                # 🌟 如果尚未探测过该 IP，则发起一次安全的轻量级握手
                elif cached_status is None:
                    try:
                        # 稍微放宽至 150ms 锁死，给路由器留出喘息缓冲时间
                        _, writer = await asyncio.wait_for(
                            asyncio.open_connection(host, port), timeout=0.15
                        )
                        writer.close()
                        await writer.wait_closed()
                        
                        async with tcp_cache_lock:
                            tcp_check_cache[cache_key] = True
                    except:
                        async with tcp_cache_lock:
                            tcp_check_cache[cache_key] = False
                        return None
            
            # 2. 第二层过滤：通过 TCP 验证的节点发送精准 HTTP 业务握手
            try:
                timeout_sec = 4.0 if is_priority else 1.5
                timeout = aiohttp.ClientTimeout(total=timeout_sec, connect=1.0)
                async with session_obj.get(target_url, headers=get_stb_headers(target_url), timeout=timeout) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        is_valid = False
                        if mode == "txiptv" and "data" in text: is_valid = True
                        elif mode == "zhgxtv" and "," in text and "#" not in text: is_valid = True
                        
                        if is_valid:
                            if target_url not in seen_live_urls:
                                seen_live_urls.add(target_url)
                                
                                # 🌟 引入异步文件锁，确保多工并发写入 JSON 文件不发生撞车冲突
                                async with file_io_lock:
                                    current_cached = []
                                    if os.path.exists(interface_save_path):
                                        try:
                                            with open(interface_save_path, 'r', encoding='utf-8') as rf:
                                                current_cached = json.load(rf).get("live_nodes", [])
                                        except: pass
                                    if not any(c["url"] == target_url for c in current_cached):
                                        current_cached.append({
                                            "url": target_url, "type": mode,
                                            "discovered_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                        })
                                        try:
                                            with open(interface_save_path, 'w', encoding='utf-8') as wf:
                                                json.dump({"live_nodes": current_cached}, wf, ensure_ascii=False, indent=4)
                                            logger.info(f"✨ [{ '历史优先' if is_priority else '新捕捉'}] 成功安全持久化存活接口: {target_url}")
                                        except: pass
                            return {"url": target_url, "content": text, "type": mode}
            except: pass
            return None

    # 流式无级变速队列消费引擎
    connector = aiohttp.TCPConnector(limit=300, use_dns_cache=True, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        task_queue = asyncio.Queue()
        for t in all_exploit_tasks:
            task_queue.put_nowait(t)

        processed_tasks_count = 0
        queue_start_time = time.time()

        async def worker():
            nonlocal processed_tasks_count
            while not task_queue.empty():
                try: task_node = task_queue.get_nowait()
                except asyncio.QueueEmpty: break
                
                res = await check_interface_node(session, task_node)
                if res: active_interfaces.append(res)
                
                task_queue.task_done()
                processed_tasks_count += 1
                
                if processed_tasks_count % 3000 == 0 or processed_tasks_count == total_tasks:
                    elapsed = time.time() - queue_start_time
                    speed = processed_tasks_count / elapsed if elapsed > 0 else 0
                    percent = (processed_tasks_count / total_tasks) * 100
                    logger.info(f"⚡ [绿能控速推进中] {processed_tasks_count}/{total_tasks} ({percent:.2f}%) | 当前时速: {speed*3600:.0f}码/小时 | 抓获活跃接口: {len(active_interfaces)}个")

        # 🌟 优化调整：精简工作流管道数量至 120，完美适配大众宽带与光猫硬件
        WORKER_CONCURRENCY = 120
        worker_tasks = [asyncio.create_task(worker()) for _ in range(WORKER_CONCURRENCY)]
        await asyncio.gather(*worker_tasks)

        logger.info(f"✨ 接口大捷！网段爆破收工，共计捕捉到 {len(active_interfaces)} 个可用动态源数据入口。")

        # 6. 数据池化解包
        raw_channels_pool = []
        for node in active_interfaces:
            parsed_url = urlparse(node["url"])
            host_base = f"{parsed_url.scheme}://{parsed_url.hostname}:{parsed_url.port}"
            if node["type"] == "zhgxtv":
                for line in node["content"].splitlines():
                    line = line.strip()
                    if line and "," in line:
                        try:
                            ch_name, ch_url_path = line.split(',', 1)
                            full_url = ch_url_path if ch_url_path.startswith('http') else urljoin(host_base, ch_url_path)
                            raw_channels_pool.append((ch_name, full_url))
                        except: pass
            elif node["type"] == "txiptv":
                try:
                    json_data = json.loads(node["content"])
                    for item in json_data.get('data', []):
                        if isinstance(item, dict):
                            ch_name, ch_url_path = item.get('name'), item.get('url')
                            if not ch_name or not ch_url_path or ',' in ch_url_path: continue
                            full_url = ch_url_path if ch_url_path.startswith('http') else f"{host_base}{ch_url_path}"
                            raw_channels_pool.append((ch_name, full_url))
                except: pass

        raw_channels_pool = list(set(raw_channels_pool))
        logger.info(f"📊 双模共提取出 {len(raw_channels_pool)} 个独立流。开启“硬核”TS流体检...")

        # 7. 深度TS流体检
        async def perform_anatomy(name, stream_url):
            async with check_semaphore:
                checker = TSStreamChecker()
                if await checker.check_stream(session, stream_url):
                    avg_ms = np.mean(checker.stats["response_times"]) if checker.stats["response_times"] else 0
                    return clean_channel_name(name), stream_url, avg_ms
                return None

        channel_tasks = [asyncio.create_task(perform_anatomy(n, u)) for n, u in raw_channels_pool]
        anatomy_results = await asyncio.gather(*channel_tasks)
        qualified_channels = [r for r in anatomy_results if r is not None]

        # 8. 优选排序并分流写入 M3U
        categories = [
            {"name": "央视频道", "keywords": ["CCTV"]},
            {"name": "上海本地", "keywords": ["上海", "东方卫视", "五星体育", "新闻综合", "纪实人文", "都市", "哈哈"]},
            {"name": "卫视频道", "keywords": ["卫视"]}
        ]

        qualified_channels.sort(key=lambda x: x[2])
        name_counters = defaultdict(int)
        allocated_urls = set()

        output_path = os.path.join(os.path.dirname(current_dir), "shunlist.m3u")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            f.write('#EXTINF:-1 tvg-name="联通专享" tvg-logo="https://raw.githubusercontent.com/Arthur1889/iptv/main/dog_icons/Unicom.png" group-title="说明",🥇双模绿色雷达·上海联通专享黄金源\n')
            f.write("http://127.0.0.1/readme.mp4\n")

            for cat in categories:
                f.write(f"\n# ====== {cat['name']} ====== \n")
                count_cat = 0
                for name, url, ms in qualified_channels:
                    if url in allocated_urls or name_counters[name] >= 3: continue
                    is_match = any(k in name for k in cat["keywords"])
                    if is_match:
                        if cat["name"] == "卫视频道" and "CCTV" in name: continue
                        if cat["name"] == "上海本地" and "CCTV" in name: continue
                        f.write(f'#EXTINF:-1 tvg-name="{name}" tvg-logo="https://gitee.com/mytv-android/myTVlogo/raw/main/img/{name}.png" group-title="{cat["name"]}" response-time="{ms:.0f}ms",{name}\n')
                        f.write(f"{url}\n")
                        name_counters[name] += 1
                        allocated_urls.add(url)
                        count_cat += 1
                logger.info(f"📝 写入分类 [{cat['name']}]，入库 {count_cat} 个超优质源。")

            stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
            f.write(f'#EXTINF:-1 tvg-name="更新时间" tvg-logo="https://raw.githubusercontent.com/Arthur1889/iptv/main/dog_icons/Update.png" group-title="更新时间",⏱️整理完成时间：{stamp}\n')
            f.write("http://127.0.0.1/time.mp4\n")

    duration = time.time() - start_time
    logger.info(f" \n🎉 绿色安全版引擎清洗结束！总耗时: {int(duration // 60)}分{int(duration % 60)}秒，产出已安全写入 [shunlist.m3u]！")

if __name__ == "__main__":
    asyncio.run(main())