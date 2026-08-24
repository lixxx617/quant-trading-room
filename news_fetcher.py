import urllib.parse
import feedparser


def get_stock_news(ticker_or_keyword):
	"""抓取特定股票或關鍵字的 Google News RSS"""
	# 整理搜尋關鍵字（去除 .TW 尾綴）
	clean_keyword = ticker_or_keyword.replace(".TW", "").replace(".TWO", "")

	# 如果是常見代碼，加上中文名稱或「股票」關鍵字搜尋會更精準
	query = f"{clean_keyword} 股票"
	encoded_query = urllib.parse.quote(query)

	# Google News RSS 繁體中文網址
	rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"

	feed = feedparser.parse(rss_url)
	news_list = []

	# 抓取前 5 則新聞
	for entry in feed.entries[:5]:
		news_list.append({
		    "title": entry.title,
		    "link": entry.link,
		    "published": entry.published if hasattr(entry, "published") else "",
		})

	return news_list
