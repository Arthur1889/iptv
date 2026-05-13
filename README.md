📺 高画质 IPTV 自动净化工具
这是一个基于物理探测的直播源优化方案。它不依赖于直播源自带的标签（如“4K”、“高清”），而是通过 ffprobe 实时分析流媒体的物理分辨率和真实码率，自动为用户筛选出全网画质最高、响应最快的直播线路。

🌟 项目亮点
⚡ 深度画质跑分：不看标题，只看实测。自动提取视频流的高度（Height）和码率（Bitrate）。

🎯 极致去重择优：同频道全网比对，自动保留质量最高的唯一线路，告别几千个频道却没几个能看的窘境。

🛠️ 数据与代码分离：引入 sources.json 配置文件，无需修改核心代码即可轻松增删直播源。

📊 透明运行报告：配备 tqdm 可视化进度条，并在运行结束后生成详细的源贡献度摘要。

🍏 跨平台适配：完美支持 Windows、macOS 和 Ubuntu，内置 push 脚本实现一键同步至 GitHub。

**🛠️ 本地部署准备**

1. 安装环境
(1)确保你的系统安装了 Python 3.7+。
(2)安装必要依赖：pip install requests tqdm
2. 配置FFmpeg组件
本工具利用 ffprobe 探测流信息，请务必配置：

Windows:

下载 FFmpeg 官网二进制文件。

将 ffprobe.exe 放置于 C:\\ffmpeg\\bin\\ffprobe.exe (或修改 crawl.py 中的路径)。

macOS / Linux:

Bash
brew install ffmpeg  # macOS
sudo apt install ffmpeg  # Ubuntu/Debian
**🚀 使用指南**

1. 配置直播源
编辑根目录下的 sources.json 文件，在 urls 数组中添加你收集到的 .m3u 或 .txt 原始订阅链接。
"urls": \[
"https://example.com/live.m3u",
"https://raw.githubusercontent.com/.../tv.m3u"
2. 运行爬虫
执行脚本开始自动化探测与筛选：

Bash
python crawl.py
运行结束后，根目录下会生成精选后的 tv.m3u 文件。

3. 同步到 GitHub (可选)
如果你在 GitHub 上托管了自己的订阅链接，可以使用配套脚本快速推送：

Windows: 双击运行 push.bat

macOS/Linux: bash push.sh

📂 项目结构
Plaintext
├── .github/workflows  # GitHub Actions 自动更新配置
├── sources.json       # 【核心配置】存放原始订阅源链接
├── crawl.py           # 【核心脚本】负责探测、去重与画质择优
├── tv.m3u             # 【产出文件】筛选后的高画质列表
├── push.bat/sh        # 【工具脚本】一键推送变更到仓库
└── README.md          # 项目说明文档
⚖️ 免责声明
本项目仅供学习交流使用，不存储任何流媒体内容。所有直播源数据均来自互联网公开资源。请遵守当地法律法规，严禁用于商业用途。

💡 提示
如果你使用的是 Apple TV (APTV)，可以直接将你仓库中的 tv.m3u 原始链接（Raw Link）填入 App，享受自动关联的台标与节目预告（EPG）。

