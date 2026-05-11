import requests

def fetch_tv_links():
    # 假设这是某个提供直播源的接口或网页
    target_url = "https://example.com/api/get_links"
    # 这里编写你的抓取逻辑（正则解析、JSON解析等）
    # ...
    return [
        {"name": "CCTV-1", "url": "http://live.stream.com/cctv1.m3u8"},
        {"name": "HBO", "url": "http://live.stream.com/hbo.m3u8"}
    ]

def generate_m3u(channels):
    with open("tv.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            f.write(f'#EXTINF:-1, {ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

if __name__ == "__main__":
    channels = fetch_tv_links()
    generate_m3u(channels)
