import aiohttp
import asyncio
import re
import os
import json
import datetime
import time
from collections import defaultdict
from typing import Dict, List, Optional, Any
import numpy as np
from urllib.parse import urlparse, urljoin
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# 1. 获取 urls.json 文件的绝对路径
current_dir = os.path.dirname(os.path.abspath(__file__))
urls_file_path = os.path.join(current_dir, 'urls.json')

# 🌟 [新增] 初始化 live_urls.json 和 dead_urls.json(死链库)，确保断点续传
live_urls_file = os.path.join(current_dir, 'live_urls.json')
dead_urls_file = os.path.join(current_dir, 'dead_urls.json')

if not os.path.exists(live_urls_file):
    with open(live_urls_file, 'w', encoding='utf-8') as f:
        json.dump([], f)
if not os.path.exists(dead_urls_file):
    with open(dead_urls_file, 'w', encoding='utf-8') as f:
        json.dump([], f)

file_lock = asyncio.Lock()

urls = []
if os.path.exists(urls_file_path):
    try:
        with open(urls_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            urls = data.get("urls", [])
            print(f"✅ 成功从 urls.json 中加载了 {len(urls)} 个探测目标！")
    except Exception as e:
        print(f"❌ 解析 urls.json 失败，请检查 JSON 格式。原因: {e}")
else:
    print(f"⚠️ 未找到 urls.json 文件: {urls_file_path}")

# 异步HTTP请求工具函数
async def fetch_url(session, url, headers=None, timeout=3, stream=False):
    """异步获取URL内容，支持流式传输"""
    try:
        async with session.get(url, headers=headers, timeout=timeout, stream=stream) as response:
            response.raise_for_status()
            if stream:
                return response
            return await response.text()
    except Exception as e:
        logger.debug(f"请求URL失败: {url}, 错误: {str(e)}")
        return None

class TSStreamChecker:
    """TS流检测器，通过解析TS包数据来检测流的稳定性和响应时间"""
    
    def __init__(self, 
                 buffer_size: int = 8192, 
                 check_duration: int = 5,
                 response_time_threshold: int = 120,  # 响应时间阈值(毫秒)
                 request_timeout: int = 5):            # 请求超时时间(秒)
        self.buffer_size = buffer_size
        self.check_duration = check_duration
        self.response_time_threshold = response_time_threshold
        self.request_timeout = request_timeout
        
        self.pid_continuity: Dict[int, int] = defaultdict(int)
        self.stats: Dict[str, Any] = {
            "total_packets": 0,
            "invalid_packets": 0,
            "lost_packets": 0,
            "rate_history": [],
            "interval_history": [],
            "response_times": []
        }
        self.last_check_time = time.time()
        self.packets_in_window = 0
        self.last_packet_time: Optional[float] = None
        self.current_position = 0 
    
    def _add_response_time(self, response_time: float):
        self.stats["response_times"].append(response_time)
    
    def _reset_stats(self):
        self.stats = {
            "total_packets": 0,
            "invalid_packets": 0,
            "lost_packets": 0,
            "rate_history": [],
            "interval_history": [],
            "response_times": []
        }
        self.pid_continuity.clear()
        self.current_position = 0
        self.last_check_time = time.time()
        self.packets_in_window = 0
        self.last_packet_time = None

    def parse_ts_packet(self, packet: bytes) -> Optional[Dict[str, Any]]:
        if len(packet) != 188:
            return None
        sync_byte = packet[0]
        if sync_byte != 0x47:
            return None
        pid_bytes = packet[1:3]
        pid = (pid_bytes[0] & 0x1F) << 8 | pid_bytes[1]
        continuity_counter = (packet[3] & 0x0F)
        return {"pid": pid, "continuity": continuity_counter, "valid": True}

    def check_continuity(self, pid: int, current_counter: int) -> int:
        last_counter = self.pid_continuity.get(pid, -1)
        if last_counter == -1:
            self.pid_continuity[pid] = current_counter
            return 0
        expected = (last_counter + 1) % 16
        lost = (current_counter - expected) % 16
        if lost > 0:
            self.stats["lost_packets"] += lost
        self.pid_continuity[pid] = current_counter
        return lost

    def update_rate(self) -> None:
        current_time = time.time()
        elapsed = current_time - self.last_check_time
        if elapsed >= 1.0: 
            rate = self.packets_in_window / elapsed if elapsed > 0 else 0.0
            self.stats["rate_history"].append(rate)
            if len(self.stats["rate_history"]) > 5:
                self.stats["rate_history"].pop(0)
            self.last_check_time = current_time
            self.packets_in_window = 0

    def update_interval(self, current_time: float) -> None:
        if self.last_packet_time is not None:
            interval = current_time - self.last_packet_time
            self.stats["interval_history"].append(interval)
            if len(self.stats["interval_history"]) > 100:
                self.stats["interval_history"].pop(0)
        self.last_packet_time = current_time

    def _evaluate_result(self) -> bool:
        if len(self.stats["rate_history"]) < 3: 
            return False
        try:
            rate_std = np.std(self.stats["rate_history"])
            loss_rate = self.stats["lost_packets"] / self.stats["total_packets"] if self.stats["total_packets"] > 0 else 0.0
            avg_response_time = np.mean(self.stats["response_times"]) if self.stats["response_times"] else float('inf')
            is_stable = rate_std < 5 and loss_rate < 0.01
            is_fast_response = avg_response_time < self.response_time_threshold
            return is_stable and is_fast_response
        except Exception:
            return False

    async def _check_ts_stream(self, session, url: str) -> bool:
        self._reset_stats()
        start_time = time.time()
        try:
            parsed_url = urlparse(url)
            if parsed_url.scheme not in ['http', 'https']:
                return False
            while (time.time() - start_time) < self.check_duration:
                elapsed = time.time() - start_time
                if elapsed >= self.check_duration:
                    break
                req_start_time = time.time()
                headers = {"Range": f"bytes={self.current_position}-"} if self.current_position > 0 else {}
                try:
                    async with session.get(url, headers=headers, timeout=self.request_timeout) as response:
                        response.raise_for_status()
                        response_time = (time.time() - req_start_time) * 1000
                        self._add_response_time(response_time)
                        buffer = b""
                        chunk_count = 0
                        try:
                            async for chunk in response.content.iter_chunked(self.buffer_size):
                                if (time.time() - start_time) >= self.check_duration:
                                    break
                                if chunk:
                                    buffer += chunk
                                    chunk_count += 1
                                    while len(buffer) >= 188:
                                        packet = buffer[:188]
                                        buffer = buffer[188:]
                                        self.current_position += 188
                                        self.stats["total_packets"] += 1
                                        current_time = time.time()
                                        parsed = self.parse_ts_packet(packet)
                                        if not parsed:
                                            self.stats["invalid_packets"] += 1
                                            continue
                                        self.check_continuity(parsed["pid"], parsed["continuity"])
                                        self.update_interval(current_time)
                                        self.packets_in_window += 1
                                self.update_rate()
                                if chunk_count > 15: 
                                    break
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            return False
        return self._evaluate_result()

    async def check_stream(self, session, url: str) -> bool:
        lower_url = url.lower()
        if lower_url.endswith(('.m3u', '.m3u8')):
            ts_urls = await self.parse_playlist(session, url)
            if not ts_urls:
                return False
            ts_semaphore = asyncio.Semaphore(3)
            connector = aiohttp.TCPConnector(limit=100, force_close=True)
            async with aiohttp.ClientSession(connector=connector) as ts_session:
                async def check_single_ts(ts_url, index):
                    async with ts_semaphore:
                        try:
                            return await self._check_ts_stream(ts_session, ts_url)
                        except Exception:
                            return False
                ts_tasks = [asyncio.create_task(check_single_ts(ts_url, i+1)) for i, ts_url in enumerate(ts_urls)]
                try:
                    results = await asyncio.gather(*ts_tasks)
                except asyncio.CancelledError:
                    return False
            qualified_count = sum(results)
            total_count = len(results)
            return qualified_count > total_count / 2
        else:
            return await self._check_ts_stream(session, url)

    async def parse_playlist(self, session, url: str) -> List[str]:
        try:
            text = await fetch_url(session, url, timeout=self.request_timeout)
            if text is None:
                return []
            ts_urls = []
            base_url = url.rsplit('/', 1)[0] + '/' if '/' in url else url
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    if line.startswith(('http://', 'https://')):
                        ts_urls.append(line)
                    else:
                        ts_url = urljoin(base_url, line)
                        ts_urls.append(ts_url)
            unique_ts_urls = list(dict.fromkeys(ts_urls))
            return unique_ts_urls[:5]
        except Exception:
            return []

def clean_channel_name(name):
    """清理频道名称，统一格式"""
    if not name: return ""
    name = name.upper().strip()
    
    # 🌟 [修改] 强力过滤：网页代码、CSS乱码直接扔掉
    bad_keywords = ["<", ">", "DOCTYPE", "HTML", "SCRIPT", "DIV", "STYLE", "JSON", "CODE", "AMAP", "TRANSITION", "TRANSFORM", "WEBKIT", "{", "}"]
    if any(keyword in name for keyword in bad_keywords): return ""
    if len(re.findall(r'[A-Z0-9_/\.:-]{12,}', name)) > 0: return ""
        
    replacement_rules = {
        "basic": {
            "cctv": "CCTV", "中央": "CCTV", "央视": "CCTV", "高清": "", "超高": "", "HD": "", "标清": "",
            "频道": "", "*": "", "-": "", " ": "", "PLUS": "+", "＋": "+", "(": "", ")": "", "超":"",
            "KAKU少儿": "卡酷动画", "卡通动画": "卡酷动画", "酷卡动画": "卡酷动画", "北京少儿": "卡酷动画",
            "北京卡通": "卡酷动画", "嘉佳卡": "嘉佳卡通", "嘉佳卡通通": "嘉佳卡通",
        },
        "cctv_channels": {
            "CCTV1综合": "CCTV1", "CCTV2财经": "CCTV2", "CCTV3综艺": "CCTV3", "CCTV4国际": "CCTV4", 
            "CCTV4中文国际": "CCTV4", "CCTV4欧洲": "CCTV4", "CCTV5体育": "CCTV5", "CCTV6电影": "CCTV6", 
            "CCTV7军事": "CCTV7", "CCTV7军农": "CCTV7", "CCTV7农业": "CCTV7", "CCTV7国防军事": "CCTV7",
            "CCTV17军事": "CCTV7", "CCTV8电视剧": "CCTV8", "CCTV9记录": "CCTV9", "CCTV9纪录": "CCTV9",
            "CCTV10科教": "CCTV10", "CCTV11戏曲": "CCTV11", "CCTV12社会与法": "CCTV12", "CCTV13新闻": "CCTV13", 
            "CCTV新闻": "CCTV13", "CCTV14少儿": "CCTV14", "CCTV15音乐": "CCTV15", "CCTV16奥林匹克": "CCTV16",
            "CCTV17农业农村": "CCTV17", "CCTV17农业": "CCTV17", "CCTV5+体育赛视": "CCTV5+", 
            "CCTV5+体育赛事": "CCTV5+", "CCTV5+体育": "CCTV5+"
        }
    }
    regex_rules = [(r"CCTV(\d+)台", r"CCTV\1")]
    for rule_type, rules in replacement_rules.items():
        for old, new in rules.items():
            name = name.replace(old, new)
    for pattern, replacement in regex_rules:
        name = re.sub(pattern, replacement, name)
    return name

async def modify_urls(url):
    modified_urls = []
    parsed_url = urlparse(url)
    if not parsed_url.hostname:
        return modified_urls
    
    ip_parts = parsed_url.hostname.split('.')
    if len(ip_parts) != 4:
        return modified_urls
    
    base_ip = '.'.join(ip_parts[:3])
    port_str = f":{parsed_url.port}" if parsed_url.port else ""
    
    endpoints = [
        "/iptv/live/1000.json?key=txiptv",
        "/ZHGXTV/Public/json/live_interface.txt",
        # --- 👇 以下是你可以扩充的高频探测路径 👇 ---
        "/standard/live.txt",
        "/live/live.txt",
        "/playlist.m3u",
        "/live.m3u",
        "/Public/json/live_interface.txt",
        "/live/iptv.json",
        "/iptv/json/channels.json",
        "/iptv.m3u",
        "/iptv/live/2000.json?key=txiptv"
    ]
    
    for i in range(1, 254):
        modified_ip = f"{base_ip}.{i}"
        for endpoint in endpoints:
            modified_urls.append(f"{parsed_url.scheme}://{modified_ip}{port_str}{endpoint}")
    return modified_urls

async def is_url_accessible(session, url, semaphore):
    async with semaphore:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with session.get(url, timeout=timeout) as response:
                if response.status == 200:
                    return url
                else:
                    return None
        except Exception:
            return None

async def check_urls(session, urls, semaphore):
    tasks = []
    for url in urls:
        url = url.strip()
        modified_urls = await modify_urls(url)
        for modified_url in modified_urls:
            task = asyncio.create_task(is_url_accessible(session, modified_url, semaphore))
            tasks.append(task)
            
    valid_urls = []
    total = len(tasks)
    
    gateway_log_file = os.path.join(current_dir, 'valid_gateways.json')
    
    if os.path.exists(gateway_log_file):
        try:
            with open(gateway_log_file, 'r', encoding='utf-8') as gf:
                valid_urls = json.load(gf)
            if valid_urls:
                print(f"📦 [断点恢复] 检出本地已存在 {len(valid_urls)} 个历史活网关，将直接合并体检！")
        except Exception:
            valid_urls = []

    print(f"\n📡 开始网关探测，共生成 {total} 个地址，包含 ZHGXTV 特征...")
    
    for i, coro in enumerate(asyncio.as_completed(tasks), 1):
        result = await coro
        if result:
            if result not in valid_urls:
                valid_urls.append(result)
            
            async with file_lock:
                try:
                    current_gateways = []
                    if os.path.exists(gateway_log_file):
                        with open(gateway_log_file, 'r', encoding='utf-8') as gf:
                            try: current_gateways = json.load(gf)
                            except: current_gateways = []
                    
                    if result not in current_gateways:
                        current_gateways.append(result)
                        with open(gateway_log_file, 'w', encoding='utf-8') as gf:
                            json.dump(current_gateways, gf, ensure_ascii=False, indent=4)
                except Exception:
                    pass 
                    
        if i % 10 == 0 or i == total:
            print(f"\r🔍 网关探测进度: {i}/{total} ({(i/total*100):.1f}%) | 发现活网关: {len(valid_urls)} (已实时落盘安全锁)", end="", flush=True)
            
    print("\n✅ 网关探测完成！")
    return valid_urls

async def fetch_json(session, url, semaphore):
    async with semaphore:
        try:
            parsed_url = urlparse(url)
            if not parsed_url.hostname:
                return []
            port_str = f":{parsed_url.port}" if parsed_url.port else ""
            url_x = f"{parsed_url.scheme}://{parsed_url.hostname}{port_str}"

            timeout = aiohttp.ClientTimeout(total=5)
            async with session.get(url, timeout=timeout) as response:
                
                # 🌟 [修改] 安全检查：如果返回的是网页大文件，直接拒绝
                content_type = response.headers.get('Content-Type', '').lower()
                if 'html' in content_type:
                    return []
                
                results = []
                
                if "live_interface.txt" in url:
                    text_data = await response.text()
                    for line in text_data.splitlines():
                        line = line.strip()
                        if line and "," in line:
                            name, urlx = line.split(',', 1)
                            name = clean_channel_name(name)
                            if not name or not urlx: continue
                            urld = urlx if urlx.startswith(('http://', 'https://')) else urljoin(url_x, urlx)
                            results.append(f"{name},{urld}")
                else:
                    json_data = await response.json(content_type=None)
                    for item in json_data.get('data', []):
                        if isinstance(item, dict):
                            name = item.get('name')
                            urlx = item.get('url')
                            if not name or not urlx or ',' in urlx:
                                continue
                            name = clean_channel_name(name)
                            if not name: continue
                            
                            if urlx.startswith(('http://', 'https://')):
                                urld = urlx
                            else:
                                urld = f"{url_x}{urlx}"
                            results.append(f"{name},{urld}")
                return results
        except Exception:
            return []

async def main():
    start_time = time.time()
    logger.info("\n脚本开始执行...")
    
    results = []
    error_channels = []
    processed_count = 0
    all_results = []
    total_count = 0
    
    x_urls = []
    for url in urls:
        url = url.strip()
        parsed_url = urlparse(url)
        if not parsed_url.hostname:
            continue
        ip_parts = parsed_url.hostname.split('.')
        if len(ip_parts) != 4:
            continue
        base_ip = '.'.join(ip_parts[:3])
        modified_ip = f"{base_ip}.1"
        port_str = f":{parsed_url.port}" if parsed_url.port else ""
        x_url = f"{parsed_url.scheme}://{modified_ip}{port_str}"
        x_urls.append(x_url)
    unique_urls = set(x_urls)

    semaphore = asyncio.Semaphore(100)
    connector = aiohttp.TCPConnector(
        limit=300, limit_per_host=50, ttl_dns_cache=300, use_dns_cache=True, keepalive_timeout=30
    )
    timeout = aiohttp.ClientTimeout(total=30)
    
    async def check_channel(session, channel_name, channel_url, semaphore):
        """异步检测单个频道"""
        nonlocal processed_count
        try:
            async with semaphore:
                checker = TSStreamChecker(check_duration=5, response_time_threshold=120, request_timeout=5)
                is_stable = await checker.check_stream(session, channel_url)
                avg_response_time = np.mean(checker.stats["response_times"]) if checker.stats["response_times"] else float('inf')
                
                if is_stable:
                    result = channel_name, channel_url, "稳定", avg_response_time
                    results.append(result)
                    print_progress("稳定", channel_name, channel_url)
                    
                    async with file_lock:
                        try:
                            with open(live_urls_file, 'r', encoding='utf-8') as f:
                                try: current_data = json.load(f)
                                except json.JSONDecodeError: current_data = []
                            if not any(item.get('url') == channel_url for item in current_data):
                                current_data.append({
                                    "name": channel_name,
                                    "url": channel_url,
                                    "response_time_ms": round(avg_response_time, 2)
                                })
                                with open(live_urls_file, 'w', encoding='utf-8') as f:
                                    json.dump(current_data, f, ensure_ascii=False, indent=4)
                        except Exception:
                            pass
                else:
                    error_channels.append((channel_name, channel_url))
                    print_progress("不稳定", channel_name, channel_url)
                    
                    # 🌟 [新增] 如果不可用，写入死链记录
                    async with file_lock:
                        try:
                            with open(dead_urls_file, 'r', encoding='utf-8') as f:
                                try: current_dead = json.load(f)
                                except json.JSONDecodeError: current_dead = []
                            if channel_url not in current_dead:
                                current_dead.append(channel_url)
                                with open(dead_urls_file, 'w', encoding='utf-8') as f:
                                    json.dump(current_dead, f, ensure_ascii=False, indent=4)
                        except Exception:
                            pass
        except Exception as e:
            error_channels.append((channel_name, channel_url))
            print_progress("异常", channel_name, error_msg=str(e))
        finally:
            processed_count += 1
            print_progress("更新", "", "") 
    
    def print_progress(status, channel_name, channel_url=None, error_msg=None):
        numberx = processed_count / total_count * 100 if total_count > 0 else 0
        print(f"\r⏱️ 频道体检: {processed_count}/{total_count} ({numberx:.2f}%) | 稳定可用: {len(results)} | 不可用: {len(error_channels)}", end="", flush=True)
    
    def channel_key(channel_name):
        match = re.search(r'\d+', channel_name)
        if match:
            return int(match.group())
        else:
            return 99999
    
    channel_semaphore = asyncio.Semaphore(50) 
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        valid_urls = await check_urls(session, unique_urls, semaphore)
        
        tasks = []
        for url in valid_urls:
            task = asyncio.create_task(fetch_json(session, url, semaphore))
            tasks.append(task)
        json_results = await asyncio.gather(*tasks)
        for sublist in json_results:
            all_results.extend(sublist)
            
        # 🌟 [新增] 断点续传：加载历史数据，用于直接合并和跳过测速
        history_live_urls = set()
        history_dead_urls = set()
        
        if os.path.exists(live_urls_file):
            try:
                with open(live_urls_file, 'r', encoding='utf-8') as f:
                    cached_live = json.load(f)
                    for item in cached_live:
                        history_live_urls.add(item['url'])
                        # 把之前成功的记录直接加回 results，不再重新测
                        results.append((item['name'], item['url'], "稳定", item.get('response_time_ms', 999)))
            except Exception: pass
            
        if os.path.exists(dead_urls_file):
            try:
                with open(dead_urls_file, 'r', encoding='utf-8') as f:
                    history_dead_urls = set(json.load(f))
                    for url in history_dead_urls:
                        error_channels.append(("历史死链", url))
            except Exception: pass
        
        total_count = len(all_results)
        processed_count = len(history_live_urls) + len(history_dead_urls) # 进度条初始值
        print(f"\n📋 频道解析完毕！共计 {total_count} 个流。")
        print(f"📦 已检出历史存活 {len(history_live_urls)} 个，历史阵亡 {len(history_dead_urls)} 个。")
        
        channel_tasks = []
        for result in all_results:
            try:
                channel_name, channel_url = result.split(',', 1)
                
                # 🌟 [新增] 如果在历史活链或死链中出现过，直接跳过不发请求！
                if channel_url in history_live_urls or channel_url in history_dead_urls:
                    continue
                    
                task = asyncio.create_task(check_channel(session, channel_name, channel_url, channel_semaphore))
                channel_tasks.append(task)
            except Exception as e:
                logger.error(f"解析频道数据行失败: {result}, 错误: {e}")
                continue
        
        if channel_tasks:
            await asyncio.gather(*channel_tasks)

    print("\n")
    results.sort(key=lambda x: (channel_key(x[0]), x[3] if len(x) > 3 else float('inf')))
    result_counter = 12

    def write_channel_to_m3u(file, channel_name, channel_url, group_title, response_time=float('inf')):
        file.write(f'#EXTINF:-1 tvg-name="{channel_name}" tvg-logo="https://gitee.com/mytv-android/myTVlogo/raw/main/img/{channel_name}.png" group-title="{group_title}" response-time="{response_time:.0f}ms",{channel_name}\n')
        file.write(f"{channel_url}\n")

    def match_channel_category(channel_name, keywords, exclude_keywords=None):
        if not keywords or (len(keywords) == 1 and not keywords[0]):
            if exclude_keywords:
                for exclude_word in exclude_keywords:
                    if exclude_word in channel_name:
                        return False
            return True
        if exclude_keywords:
            for exclude_word in exclude_keywords:
                if exclude_word in channel_name:
                    return False
        for keyword in keywords:
            if keyword in channel_name:
                return True
        return False

    def write_channels_by_category(file, results, keywords, group_title, channel_counters, exclude_keywords=None):
        for result in results:
            channel_name, channel_url, speed, avg_response_time = result
            if match_channel_category(channel_name, keywords, exclude_keywords):
                if channel_name in channel_counters:
                    if channel_counters[channel_name] >= result_counter:
                        continue
                    else:
                        write_channel_to_m3u(file, channel_name, channel_url, group_title, avg_response_time)
                        channel_counters[channel_name] += 1
                else:
                    write_channel_to_m3u(file, channel_name, channel_url, group_title, avg_response_time)
                    channel_counters[channel_name] = 1

    channel_categories = [
        {"name": "央视频道","keywords": ["CCTV"]},
        {"name": "卫视频道","keywords": ["卫视"]},
        {"name": "影视频道","keywords": ["电影","影院","影视","剧场","电视剧"]},
        {"name": "IPTV频道","keywords": ["IPTV"]},
        {"name": "科教频道","keywords": ["CETV","教育","科教","学堂","科学"]},
        {"name": "卡通频道","keywords": ["CCTV14","少儿","卡通","动画","儿童","宝贝","哈哈"]},
        {"name": "体育频道","keywords": ["体育","赛事","奥运","冬奥","英超","NBA","垂钓","CETV4","足球","台球","CCTV5","CCTV5+","CCTV16","武术","IPTV5+","高尔夫"]},
        {"name": "其他频道","keywords": [""],"exclude_keywords": ["CCTV","卫视","电影","影院","影视","剧场","电视剧","IPTV","CETV","教育","科教","学堂","科学",
        "少儿","卡通","动画","儿童","宝贝","哈哈","体育","赛事","奥运","冬奥","英超","NBA","垂钓","教育","足球","台球","武术","高尔夫","测试","快乐购","广告","购物"]}
    ]

    with open("itvlist.m3u", 'w', encoding='utf-8') as file:
        file.write('#EXTM3U\n')
        for category in channel_categories:
            channel_counters = {}
            exclude_keywords = category.get("exclude_keywords", None)
            write_channels_by_category(
                file, results, category["keywords"], category["name"], channel_counters, exclude_keywords
            )
        
        current_time = datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
        file.write(f'#EXTINF:-1 tvg-name="{current_time}" tvg-logo="https://gitee.com/mytv-android/myTVlogo/raw/main/img/Dog狗频道.png" group-title="更新时间",{current_time}\n')
        file.write(f"http://example.com/update_time.mp4\n")
    
    end_time = time.time()
    total_duration = end_time - start_time
    hours = int(total_duration // 3600)
    minutes = int((total_duration % 3600) // 60)
    seconds = int(total_duration % 60)
    
    logger.info(f"脚本执行完成！")
    logger.info(f"总耗时: {hours}小时{minutes}分钟{seconds}秒 ({total_duration:.2f}秒)")
    logger.info(f"总共处理频道: {len(all_results)} 个")
    logger.info(f"可用频道: {len(results)} 个")
    logger.info(f"不可用频道: {len(error_channels)} 个")
    logger.info(f"成功率: {len(results)/len(all_results)*100:.2f}%" if len(all_results) > 0 else "成功率: 0%")

if __name__ == "__main__":
    asyncio.run(main())
