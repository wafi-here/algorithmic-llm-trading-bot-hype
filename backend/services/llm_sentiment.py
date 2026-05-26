import asyncio
import httpx
import traceback
import xml.etree.ElementTree as ET
import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer
from backend.config import Config
from backend.services.database import db

# Initialize local NLTK VADER for free local sentiment analysis
try:
    nltk.download('vader_lexicon', quiet=True)
    sia = SentimentIntensityAnalyzer()
    VADER_READY = True
except Exception as e:
    print(f"[LLM] NLTK download failed, falling back to basic analysis: {str(e)}")
    VADER_READY = False

class LLMSentimentEngine:
    def __init__(self):
        self.gemini_key = Config.GEMINI_API_KEY
        self.news_sources = [
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://cointelegraph.com/rss"
        ]
        self._client = None

    def get_client(self):
        """Instantiates or returns the shared client session."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    def _analyze_local_vader(self, text: str) -> float:
        """Runs free, local, zero-dependency VADER sentiment analysis."""
        if not VADER_READY:
            # Fallback rule-based matching if NLTK failed to download
            bearish_words = ["dump", "bear", "crash", "hacked", "drop", "fud", "ban", "lawsuit", "illegal"]
            bullish_words = ["pump", "bull", "rally", "moon", "buy", "adoption", "approval", "upgrade", "success"]
            
            text_lower = text.lower()
            score = 0.0
            for w in bearish_words:
                if w in text_lower:
                    score -= 0.15
            for w in bullish_words:
                if w in text_lower:
                    score += 0.15
            return max(-1.0, min(1.0, score))
            
        scores = sia.polarity_scores(text)
        # compound is normalized between -1 and 1
        return float(scores.get("compound", 0.0))

    async def _analyze_with_gemini(self, title: str, text: str) -> dict:
        """
        Uses free-tier Gemini API to perform advanced financial sentiment scoring.
        Returns a dict: { "score": float, "summary": str }
        """
        if not self.gemini_key:
            return None
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.gemini_key}"
        
        prompt = (
            f"You are an expert quantitative crypto analyst. Analyze the following news article:\n"
            f"Title: {title}\n"
            f"Content: {text}\n\n"
            f"Respond strictly in valid JSON format. Do not write markdown tags or extra talk. Output exactly this schema:\n"
            f'{{"sentiment_score": <float between -1.0 and 1.0>, "summary": "<1 sentence financial summary>"}}\n'
            f"where 1.0 is extremely bullish, -1.0 is extremely bearish, and 0.0 is neutral."
        )
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        
        client = self.get_client()
        try:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                resp_json = response.json()
                text_resp = resp_json['candidates'][0]['content']['parts'][0]['text']
                import json
                parsed = json.loads(text_resp.strip())
                return {
                    "score": float(parsed.get("sentiment_score", 0.0)),
                    "summary": parsed.get("summary", "")
                }
        except Exception as e:
            db.log_system("WARNING", f"Gemini API analysis failed, falling back to VADER: {str(e)}")
        return None

    async def scrape_and_analyze(self) -> float:
        """Scrapes RSS feeds, analyzes recent sentiment, and stores to database."""
        db.log_system("LLM", "Starting news scraping process...")
        total_score = 0.0
        articles_processed = 0
        
        client = self.get_client()
        for source in self.news_sources:
            try:
                response = await client.get(source)
                if response.status_code != 200:
                    continue
                    
                # Parse RSS XML
                root = ET.fromstring(response.content)
                items = root.findall(".//item")[:3] # Process top 3 items per feed to avoid rate limits
                
                for item in items:
                    title = item.find("title").text if item.find("title") is not None else ""
                    link = item.find("link").text if item.find("link") is not None else ""
                    desc = item.find("description").text if item.find("description") is not None else ""
                    
                    if not title:
                        continue
                        
                    # Perform Analysis (Gemini primary, VADER secondary)
                    gemini_res = await self._analyze_with_gemini(title, desc)
                    if gemini_res:
                        sentiment_score = gemini_res["score"]
                        summary = gemini_res["summary"]
                    else:
                        sentiment_score = self._analyze_local_vader(title + " " + desc)
                        summary = f"Analyzed locally via VADER engine: {title}"
                        
                    # Save to database
                    db.record_sentiment(title, source, link, desc, sentiment_score, summary)
                    db.log_system("LLM", f"Scraped: {title[:50]}... | Sentiment: {sentiment_score}")
                    
                    total_score += sentiment_score
                    articles_processed += 1
                    
            except Exception as e:
                db.log_system("WARNING", f"Failed to scrape feed {source}: {str(e)}")
                    
        if articles_processed > 0:
            avg_sentiment = total_score / articles_processed
            db.log_system("LLM", f"News scraping completed. Average sentiment score: {avg_sentiment:.2f}")
            return avg_sentiment
        else:
            db.log_system("LLM", "No new articles found. Returning neutral sentiment.")
            return 0.0

# Singleton instance
sentiment_engine = LLMSentimentEngine()

