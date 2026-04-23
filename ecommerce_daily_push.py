#!/usr/bin/env python3
"""
每日创业项目灵感推送 - 推送到飞书
数据源: HN API + 百度热搜 + 备选项目
"""

import json
import urllib.request
import urllib.parse
import ssl
import re
import gzip
import zlib
import time
import os
from datetime import datetime

# ========== 配置区 ==========

# 飞书 webhook - 支持环境变量
FEISHU_WEBHOOK = os.environ.get('FEISHU_WEBHOOK',
    "https://open.feishu.cn/open-apis/bot/v2/hook/803f3609-c1e8-4592-931b-71351c57f984")

# 代理配置 - GitHub Actions 等国外环境不需要代理
def needs_proxy():
    """检测是否需要代理"""
    # GitHub Actions 在国外，不需要代理访问国外网站
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return False
    # 本地开发环境，需要代理
    return True

PROXY = "http://127.0.0.1:7897" if needs_proxy() else None

# ========== 翻译 ==========

def translate_text(text):
    """使用免费翻译API翻译英文到中文"""
    if not text or len(text) < 3:
        return text

    # 检查是否包含中文，如果有则不需要翻译
    if re.search(r'[一-鿿]', text):
        return text

    try:
        # 使用 MyMemory 翻译API (免费，无需认证)
        url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}&langpair=en|zh"
        result = fetch(url, timeout=10)
        if result:
            data = json.loads(result)
            translated = data.get('responseData', {}).get('translatedText', '')
            if translated and translated != text:
                return translated
    except:
        pass

    # 备用：使用百度翻译API（如果有API key可以配置）
    # 或者返回原文
    return text


def translate_project(project):
    """翻译项目信息"""
    # 只翻译英文内容
    name = project.get('name', '')
    desc = project.get('description', '')

    # 翻译标题
    translated_name = translate_text(name)
    if translated_name != name:
        project['name'] = translated_name
        project['original_name'] = name  # 保留原文

    # 翻译描述
    translated_desc = translate_text(desc)
    if translated_desc != desc:
        project['description'] = translated_desc
        if desc:
            project['original_desc'] = desc

    return project


# ========== 网络工具 ==========

def create_opener():
    """创建带代理的opener"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    if PROXY:
        proxy_handler = urllib.request.ProxyHandler({
            'http': PROXY,
            'https': PROXY
        })
        opener = urllib.request.build_opener(
            proxy_handler,
            urllib.request.HTTPSHandler(context=ctx)
        )
    else:
        # 无代理环境（如GitHub Actions）
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx)
        )

    opener.addheaders = [
        ('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'),
        ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'),
        ('Accept-Language', 'en-US,en;q=0.9,zh-CN;q=0.8'),
        ('Accept-Encoding', 'gzip, deflate'),
    ]
    return opener


def fetch(url, timeout=20):
    """通用HTTP GET"""
    opener = create_opener()
    try:
        resp = opener.open(url, timeout=timeout)
        raw = resp.read()
        encoding = resp.info().get('Content-Encoding', '')
        if encoding == 'gzip':
            raw = gzip.decompress(raw)
        elif encoding == 'deflate':
            raw = zlib.decompress(raw)
        return raw.decode('utf-8', errors='replace')
    except Exception as e:
        print(f"  ✗ {url[:60]}: {e}")
        return None


def fetch_no_proxy(url, timeout=15):
    """不走代理的请求（用于国内API）"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            encoding = resp.info().get('Content-Encoding', '')
            if encoding == 'gzip':
                raw = gzip.decompress(raw)
            return raw.decode('utf-8', errors='replace')
    except Exception as e:
        print(f"  ✗ {url[:60]}: {e}")
        return None

# ========== 数据源 ==========

def fetch_hackernews():
    """HN - 最可靠的数据源，官方API无需认证"""
    print("📡 Hacker News...")
    ids_json = fetch("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not ids_json:
        return []

    top_ids = json.loads(ids_json)[:40]
    projects = []

    for sid in top_ids:
        story_json = fetch(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
        if not story_json:
            continue
        story = json.loads(story_json)
        title = story.get('title', '')
        score = story.get('score', 0)
        url = story.get('url', f"https://news.ycombinator.com/item?id={sid}")

        # 创业/产品相关关键词 - 精准筛选
        # 必须是真正的项目/产品展示，而非普通新闻
        project_kw = ['show hn', 'i built', 'i made', 'i created', 'launched',
                      'my product', 'my app', 'my tool', 'my startup',
                      'side project', 'indie', 'bootstrap', 'mrr', 'revenue',
                      'we built', 'we launched', 'announcing', 'introducing',
                      'open source', 'available on', 'check out my',
                      '创业项目', '创业', '电商平台', '独立站', '开店',
                      '副业', '赚钱', '变现', '项目分享']

        # 排除普通新闻
        news_kw = ['study', 'report', 'analysis', 'survey', 'research shows',
                   'scientists', 'government', 'policy', 'election', 'politics',
                       'breaking', 'crisis', 'scandal', 'lawsuit', 'dies', 'death',
                       'honda', 'toyota', 'nissan', 'mitsubishi', 'mitsui', 'hitachi']

        title_lower = title.lower()

        # 普通新闻直接跳过
        if any(k in title_lower for k in news_kw):
            continue

        # 必须匹配项目关键词
        if any(k in title_lower for k in project_kw):
            projects.append({
                'name': title[:80],
                'description': f"HN · {score} points · {story.get('descendants', 0)} comments",
                'url': url,
                'source': 'Hacker News'
            })
        time.sleep(0.05)  # 避免请求过快

    print(f"  ✓ {len(projects)} 个创业相关帖子")
    return projects


def fetch_baidu_hot():
    """百度热搜 - 国内数据源"""
    print("📡 百度热搜...")
    data = fetch_no_proxy("https://top.baidu.com/api/board?tab=realtime")
    if not data:
        return []

    try:
        parsed = json.loads(data)
        items = []
        cards = parsed.get('data', {}).get('cards', [])
        for card in cards:
            for c in card.get('content', []):
                word = c.get('word', '')
                hot = c.get('hotScore', c.get('rawHot', ''))
                url = c.get('url', f"https://www.baidu.com/s?wd={urllib.parse.quote(word)}")
                items.append({
                    'name': word,
                    'description': f"热度 {hot}",
                    'url': url,
                    'source': '百度热搜'
                })
        print(f"  ✓ {len(items)} 条热搜")
        return items
    except:
        return []


def fetch_producthunt_rss():
    """Product Hunt RSS - 绕过403"""
    print("📡 Product Hunt RSS...")
    rss = fetch("https://www.producthunt.com/feed")
    if not rss:
        return []

    products = []
    # 解析RSS中的产品
    pattern = r'<title>(.*?)</title>.*?<link>(.*?)</link>'
    for match in re.finditer(pattern, rss, re.DOTALL):
        title = match.group(1).strip()
        link = match.group(2).strip()
        if title and '/posts/' in link:
            products.append({
                'name': re.sub(r'^Product Hunt: ', '', title),
                'description': '',
                'url': link,
                'source': 'Product Hunt'
            })

    print(f"  ✓ {len(products)} 个产品")
    return products[:15]


def fetch_indiehackers_rss():
    """Indie Hackers RSS"""
    print("📡 Indie Hackers RSS...")
    rss = fetch("https://www.indiehackers.com/feed.rss")
    if not rss:
        # 尝试另一个RSS地址
        rss = fetch("https://www.indiehackers.com/rss")
    if not rss:
        return []

    products = []
    pattern = r'<title>(.*?)</title>.*?<link>(.*?)</link>'
    for match in re.finditer(pattern, rss, re.DOTALL):
        title = match.group(1).strip()
        link = match.group(2).strip()
        if title and 'indiehackers' in link:
            products.append({
                'name': title,
                'description': '',
                'url': link,
                'source': 'Indie Hackers'
            })

    print(f"  ✓ {len(products)} 篇文章")
    return products[:15]


def fetch_sideproject():
    """SideProject.org - 专门收集个人项目"""
    print("📡 SideProject...")
    html = fetch("https://www.sideprojectors.com")
    if not html:
        return []

    products = []
    # 解析项目列表
    pattern = r'<a[^>]*href="(/project/[^"]+)"[^>]*>.*?(?:<h[23][^>]*>(.*?)</h[23]>|class="title"[^>]*>(.*?)</)'
    for match in re.finditer(pattern, html, re.DOTALL):
        path = match.group(1)
        name = (match.group(2) or match.group(3) or path).strip()
        name = re.sub(r'<[^>]+>', '', name)
        products.append({
            'name': name,
            'description': '',
            'url': f"https://www.sideprojectors.com{path}",
            'source': 'SideProject'
        })

    print(f"  ✓ {len(products)} 个项目")
    return products[:15]


# ========== 备选项目池 ==========

FALLBACK_POOL = [
    {"name": "Print-on-demand 定制商品店铺", "description": "客户下单后生产，零库存风险。T恤、手机壳、帆布袋等。平台: Printful + Shopify", "url": "https://printful.com", "source": "项目池"},
    {"name": "细分品类独立站", "description": "专注细分市场(宠物/露营/瑜伽/汉服)，建立品牌。采购: 1688代发", "url": "https://shopify.com", "source": "项目池"},
    {"name": "数字产品商店", "description": "Notion模板、Excel工具包、设计素材。零库存，边际成本为零", "url": "https://gumroad.com", "source": "项目池"},
    {"name": "订阅盒子服务", "description": "用户按月付费定期收到精选商品(咖啡/零食/盲盒)。稳定现金流", "url": "https://cratejoy.com", "source": "项目池"},
    {"name": "跨境一件代发 Dropshipping", "description": "海外下单，国内供应商直发，无需囤货。利润率10-30%", "url": "https://aliexpress.com", "source": "项目池"},
    {"name": "AI工具电商套壳", "description": "用AI API包装成垂直工具(文案/图片/翻译)，按次/按月收费", "url": "https://replicate.com", "source": "项目池"},
    {"name": "知识付费/课程平台", "description": "将专业技能做成课程。平台: 小鹅通/知识星球/Gumroad", "url": "https://xiaoe-tech.com", "source": "项目池"},
    {"name": "闲鱼/拼多多无货源", "description": "从1688/拼多多低价采货，闲鱼/其他平台溢价出售", "url": "https://1688.com", "source": "项目池"},
    {"name": "微信小程序工具", "description": "实用小工具(记账/打卡/计算器)，广告变现或付费解锁", "url": "https://developers.weixin.qq.com", "source": "项目池"},
    {"name": "本地生活服务号", "description": "运营本地吃喝玩乐公众号/小红书，接商家广告和团购", "url": "https://mp.weixin.qq.com", "source": "项目池"},
    {"name": "跨境电商-东南亚市场", "description": "Shopee/TikTok Shop东南亚，竞争小于欧美，增长快", "url": "https://shopee.com", "source": "项目池"},
    {"name": "TikTok短视频带货", "description": "短视频+直播带货，无需自己库存，选品+内容是关键", "url": "https://tiktok.com", "source": "项目池"},
    {"name": "Notion/Obsidian模板商店", "description": "制作高质量模板出售，适合有整理和设计能力的人", "url": "https://notion.so", "source": "项目池"},
    {"name": "自动化SaaS工具", "description": "解决某个重复性工作流(报表/排班/提醒)，按月订阅收费", "url": "https://saasify.io", "source": "项目池"},
    {"name": "垂类信息订阅服务", "description": "整理某行业信息(政策/招标/行情)，付费订阅推送", "url": "https://substack.com", "source": "项目池"},
    {"name": "手工/定制商品电商", "description": "手作饰品、定制礼品、文创产品。小红书+淘宝店起步", "url": "https://xiaohongshu.com", "source": "项目池"},
    {"name": "虚拟助理/代运营", "description": "帮商家做客服、社媒运营、选品。轻资产，技能变现", "url": "https://upwork.com", "source": "项目池"},
    {"name": "API数据服务", "description": "整理某个领域数据(价格/评分/舆情)提供API，按调用收费", "url": "https://rapidapi.com", "source": "项目池"},
    {"name": "在线设计/印刷服务", "description": "Canva式在线设计工具，或名片/海报印刷服务", "url": "https://canva.com", "source": "项目池"},
    {"name": "社区+会员制电商", "description": "先建社群(健身/母婴/考研)，再推出自有品牌产品", "url": "https://zhishixingqiu.com", "source": "项目池"},
]

# 追踪已推送过的项目，避免重复
PUSHED_LOG = "pushed_log.json"


def load_pushed_log():
    try:
        with open(PUSHED_LOG, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except:
        return set()


def save_pushed_log(pushed_set):
    with open(PUSHED_LOG, 'w', encoding='utf-8') as f:
        json.dump(list(pushed_set)[-200:], f, ensure_ascii=False)  # 只保留最近200条


def get_fallback_projects(pushed_set, count=5):
    """从项目池中获取未推送过的项目"""
    available = [p for p in FALLBACK_POOL if p['name'] not in pushed_set]
    if len(available) < count:
        # 所有项目都推过一轮，重置
        pushed_set.clear()
        available = FALLBACK_POOL.copy()
    return available[:count]

# ========== 格式化与推送 ==========

def format_items(projects):
    """格式化项目列表"""
    items = []
    for i, p in enumerate(projects[:5], 1):
        # 翻译
        p = translate_project(p)

        name = p['name']
        desc = p.get('description', '')
        source = p.get('source', '')
        url = p['url']

        # 如果有原文，显示双语
        original_name = p.get('original_name', '')
        if original_name:
            name_display = f"{name}\n_({original_name})_"
        else:
            name_display = name

        original_desc = p.get('original_desc', '')
        if original_desc and desc != original_desc:
            desc_display = f"{desc}\n{original_desc}"
        else:
            desc_display = desc

        text = f"**{i}. {name_display}**\n{desc_display}\n👉 [{source}]({url})"
        items.append(text)
    return items


def send_to_feishu(title, content):
    if not content:
        return False

    message = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue"
            },
            "elements": [
                {"tag": "markdown", "content": "\n\n---\n\n".join(content)},
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": f"推送时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"}
                    ]
                }
            ]
        }
    }

    data = json.dumps(message, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(FEISHU_WEBHOOK, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if result.get('StatusCode') == 0:
                print("✅ 推送成功！")
                return True
            else:
                print(f"❌ 推送失败: {result}")
                return False
    except Exception as e:
        print(f"❌ 推送异常: {e}")
        return False

# ========== 主流程 ==========

def main():
    print(f"\n{'='*50}")
    print(f"💡 每日创业项目灵感 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    all_projects = []
    pushed_set = load_pushed_log()

    # 1. HN (最可靠)
    hn = fetch_hackernews()
    all_projects.extend(hn)

    # 2. Product Hunt RSS
    ph = fetch_producthunt_rss()
    all_projects.extend(ph)

    # 3. Indie Hackers RSS
    ih = fetch_indiehackers_rss()
    all_projects.extend(ih)

    # 4. 百度热搜(补充国内视角)
    bd = fetch_baidu_hot()
    all_projects.extend(bd)

    # 5. SideProject
    sp = fetch_sideproject()
    all_projects.extend(sp)

    print(f"\n合计获取: {len(all_projects)} 条")

    # 筛选电商/创业相关 - 更精准
    startup_kw = ['创业', '开店', '电商', '副业', '赚钱', '创业项目',
                   '独立站', '直播带货', '跨境电商', '个体户',
                   '小微企业', '个体创业', '淘宝店', '拼多多',
                   'shop', 'store', 'startup', 'launch', 'business',
                   'side project', 'indie', 'bootstrap', 'show hn',
                   'i built', 'i made', 'my product', 'my app']

    # 排除普通新闻关键词
    exclude_kw = ['研究', '报告', '调查', '科学家', '政府', '政策', '选举',
                  '危机', '丑闻', '诉讼', '逝世', '死亡', '事故', '灾害',
                  'study', 'report', 'research', 'scientist', 'government',
                  'policy', 'crisis', 'scandal', 'lawsuit', 'dies', 'death',
                  'honda', 'toyota', 'mitsubishi', 'mitsui', 'hitachi']

    def is_startup_related(p):
        text = (p['name'] + ' ' + p.get('description', '')).lower()
        # 先排除普通新闻
        if any(k in text for k in exclude_kw):
            return False
        # 再匹配创业关键词
        return any(k in text for k in startup_kw)

    startup_projects = [p for p in all_projects if is_startup_related(p)]

    print(f"创业相关: {len(startup_projects)} 条")

    # 组合策略：外部项目 + 项目池，确保内容质量
    # 至少包含 2-3 个项目池的成熟项目idea
    final = []

    # 1. 优先使用外部获取的创业项目（最多2条）
    if startup_projects:
        final.extend(startup_projects[:2])

    # 2. 补充项目池内容（至少3条，确保有可执行项目）
    pool_projects = get_fallback_projects(pushed_set, 5 - len(final))
    final.extend(pool_projects)

    # 如果外部项目不足，多用项目池补充
    if len(final) < 5:
        more_pool = get_fallback_projects(pushed_set, 5)
        for p in more_pool:
            if p not in final:
                final.append(p)
        final = final[:5]

    # 记录已推送
    for p in final:
        pushed_set.add(p['name'])
    save_pushed_log(pushed_set)

    # 推送
    content = format_items(final)
    send_to_feishu(f"💡 每日创业项目灵感 - {datetime.now().strftime('%Y-%m-%d')}", content)

    print(f"\n推送项目: {[p['name'][:30] for p in final]}")


if __name__ == "__main__":
    main()