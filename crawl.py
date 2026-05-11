import requests
import re

def fetch_tv_links():
    # 我们从范明明的源抓取，因为它的格式最标准，适合 Apple TV
    source_url = "https://live.fanmingming.com/tv/m3u/ipv6.m3u"
    
    try:
        response = requests.get(source_url, timeout=10)
        response.raise_for_status()
        m3u_content = response.text
        
        # 解析 M3U 格式 (提取频道名和 URL)
        channels = []
        # 使用正则匹配 #EXTINF 和 紧随其后的 URL
        pattern = re.compile(r'#EXTINF:.*?,(.*?)\n(http.*?)\n', re.DOTALL)
        matches = pattern.findall(m3u_content)
        
        for name, url in matches:
            channels.append({
                "name": name.strip(),
                "url": url.strip()
            })
        return channels
    except Exception as e:
        print(f"抓取失败: {e}")
        return []

def generate_m3u(channels):
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U x-tvg-url=\"https://live.fanmingming.com/e.xml\"\n") # 加入节目单(EPG)
        for ch in channels:
            # 只有当你想过滤特定频道（比如只看CCTV）时才加判断
            f.write(f'#EXTINF:-1, {ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    tv_list = fetch_tv_links()
    if tv_list:
        generate_m3u(tv_list)
        print(f"成功抓取 {len(tv_list)} 个频道")
