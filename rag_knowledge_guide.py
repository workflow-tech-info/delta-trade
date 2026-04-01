# 🧠 RAG KNOWLEDGE BASE — TRADING BOT UPGRADE GUIDE
# How to give your bot a "brain" trained on all trading literature

"""
═══════════════════════════════════════════════════════════════
WHAT IS A RAG SYSTEM?
═══════════════════════════════════════════════════════════════

RAG = Retrieval-Augmented Generation

Instead of hardcoding trading rules, your bot queries a
knowledge base of trading books, research papers, BabyPips
content, and strategies — then uses an LLM (like Claude) to
reason about the current market situation in context.

Think of it as:
   Bot sees market data → asks "what does our knowledge base
   say about this setup?" → gets AI reasoning → makes decision

═══════════════════════════════════════════════════════════════
PHASE 1 (Now): Rule-based bot (trading_bot_v2.py)
PHASE 2 (Month 2): Add ChromaDB vector store knowledge base  
PHASE 3 (Month 3): Connect Claude API for market reasoning
PHASE 4 (Month 4): Self-improving bot logs its own decisions
═══════════════════════════════════════════════════════════════
"""

# ──────────────────────────────────────────────────────────────
# INSTALL REQUIREMENTS FOR RAG
# pip install chromadb sentence-transformers anthropic langchain
# ──────────────────────────────────────────────────────────────

import chromadb
from sentence_transformers import SentenceTransformer
import anthropic
import json
import os

# ══════════════════════════════════════════════════════════════
# KNOWLEDGE BASE BUILDER
# Add any trading text — books, articles, strategies
# ══════════════════════════════════════════════════════════════

KNOWLEDGE_SOURCES = """
SOURCES TO ADD TO YOUR KNOWLEDGE BASE:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FREE RESOURCES:
┌─────────────────────────────────────────────────────────┐
│ BabyPips School of Pipsology (babypips.com)             │
│ → Scrape all chapters and add as text chunks            │
│                                                          │
│ Investopedia Trading Guides (investopedia.com)          │
│ → All technical analysis articles                        │
│                                                          │
│ TradingView Pine Script Library                          │
│ → Strategy descriptions and logic                        │
│                                                          │
│ CMT Association Papers (cmtassociation.org)             │
│ → Academic-grade technical analysis research             │
│                                                          │
│ SSRN Finance Papers (ssrn.com)                          │
│ → Quantitative trading research (free access)           │
└─────────────────────────────────────────────────────────┘

BOOKS TO ADD (convert to PDF → text):
┌─────────────────────────────────────────────────────────┐
│ "Trading in the Zone" — Mark Douglas                    │
│ "Technical Analysis of Financial Markets" — John Murphy │
│ "Reminiscences of a Stock Operator" — Edwin Lefèvre     │
│ "Market Wizards" — Jack Schwager                        │
│ "The Art and Science of Technical Analysis" — Grimes    │
│ "Evidence-Based Technical Analysis" — David Aronson     │
└─────────────────────────────────────────────────────────┘
"""

# ══════════════════════════════════════════════════════════════
# RAG TRADING ADVISOR
# ══════════════════════════════════════════════════════════════

class TradingKnowledgeBase:
    """
    Stores trading knowledge as vector embeddings.
    When the bot asks "what should I do?" it searches
    for relevant strategies from all ingested content.
    """

    def __init__(self, db_path: str = "./trading_knowledge"):
        self.client    = chromadb.PersistentClient(path=db_path)
        self.encoder   = SentenceTransformer("all-MiniLM-L6-v2")
        self.collection = self.client.get_or_create_collection(
            name="trading_strategies",
            metadata={"description": "Trading knowledge base"}
        )
        print(f"Knowledge base loaded: {self.collection.count()} documents")

    def add_knowledge(self, texts: list, sources: list = None):
        """
        Add trading knowledge to the vector database.
        texts: list of text chunks (paragraphs, strategies, rules)
        sources: list of source names (book names, URLs, etc.)
        """
        if not texts:
            return

        # Encode texts to vectors
        embeddings = self.encoder.encode(texts).tolist()
        ids        = [f"doc_{self.collection.count() + i}" for i in range(len(texts))]
        metadatas  = [{"source": s} for s in (sources or ["unknown"] * len(texts))]

        self.collection.add(
            documents=embeddings,
            documents=texts,
            metadatas=metadatas,
            ids=ids
        )
        print(f"✅ Added {len(texts)} knowledge chunks")

    def search(self, query: str, n_results: int = 5) -> list:
        """
        Searches knowledge base for relevant trading rules/strategies.
        Returns top n most relevant text chunks.
        """
        query_embedding = self.encoder.encode([query]).tolist()
        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=n_results
        )
        return results.get("documents", [[]])[0]

    def load_babypips_content(self, filepath: str):
        """Load BabyPips content from a text file."""
        if not os.path.exists(filepath):
            print(f"File not found: {filepath}")
            return
        with open(filepath, "r") as f:
            content = f.read()
        # Split into chunks of ~500 words
        words  = content.split()
        chunks = [" ".join(words[i:i+500]) for i in range(0, len(words), 400)]
        self.add_knowledge(chunks, sources=["BabyPips"] * len(chunks))
        print(f"Loaded {len(chunks)} chunks from BabyPips content")


# ══════════════════════════════════════════════════════════════
# AI MARKET ADVISOR — Uses Claude to reason about setups
# ══════════════════════════════════════════════════════════════

class AIMarketAdvisor:
    """
    Combines:
    1. Current market data (price, indicators, conditions)
    2. Relevant knowledge from your knowledge base
    3. Claude AI to reason about whether to trade

    This is the "brain" upgrade for the bot.
    """

    def __init__(self, knowledge_base: TradingKnowledgeBase):
        self.kb     = knowledge_base
        self.claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

    def should_trade(self, market_context: dict) -> dict:
        """
        Given current market conditions, asks Claude + knowledge base:
        'Should we take this trade?'
        Returns: {decision: "BUY/SELL/HOLD", confidence: 0-100, reasoning: str}
        """

        # Build market summary
        market_summary = f"""
Current Market Data:
- Symbol: {market_context.get('symbol')}
- Price: {market_context.get('price')}
- Signal Score: {market_context.get('score')}/100
- Market Condition: {market_context.get('condition')}
- RSI: {market_context.get('rsi')}
- ADX: {market_context.get('adx')}
- Active Sessions: {market_context.get('sessions')}
- Price Action Patterns: {market_context.get('patterns')}
- MTF Bias: {market_context.get('biases')}
- Near Fibonacci Level: {market_context.get('near_fib')}
"""

        # Search knowledge base for relevant strategies
        relevant_knowledge = self.kb.search(
            f"trading strategy for {market_context.get('condition')} market with "
            f"RSI {market_context.get('rsi')} and {market_context.get('patterns')}"
        )
        knowledge_context = "\n".join(relevant_knowledge[:3])  # top 3 results

        # Ask Claude
        prompt = f"""You are an expert day trader analyzing a crypto trade setup.

MARKET DATA:
{market_summary}

RELEVANT TRADING KNOWLEDGE:
{knowledge_context}

Based on all of the above, provide:
1. Trade decision: BUY, SELL, or HOLD
2. Confidence score (0-100)
3. Key reasoning (3 bullet points max)
4. Any major risks to this trade

Respond in JSON format:
{{
  "decision": "BUY/SELL/HOLD",
  "confidence": 75,
  "reasons": ["reason1", "reason2", "reason3"],
  "risks": ["risk1", "risk2"]
}}"""

        try:
            response = self.claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            result_text = response.content[0].text
            # Parse JSON response
            result = json.loads(result_text)
            return result
        except Exception as e:
            print(f"AI Advisor error: {e}")
            return {"decision": "HOLD", "confidence": 0, "reasons": ["AI error"], "risks": []}


# ══════════════════════════════════════════════════════════════
# UPGRADE ROADMAP
# ══════════════════════════════════════════════════════════════

UPGRADE_ROADMAP = """
╔══════════════════════════════════════════════════════════════╗
║                  BOT UPGRADE ROADMAP                         ║
╚══════════════════════════════════════════════════════════════╝

PHASE 1 — NOW (Weeks 1-4): Paper Trade v2.0
─────────────────────────────────────────────
✅ Multi-timeframe analysis (5m/15m/30m/2h)
✅ Multi-indicator stack (RSI, Stoch, EMA, MACD, BB, ATR, ADX)
✅ Market condition detection (trend/range/choppy)
✅ Session timing (only trade high-volume windows)
✅ Fibonacci confluence
✅ Price action patterns
✅ Trailing stop loss
✅ Probability scoring (0-100)
📌 TARGET: 50%+ win rate on paper over 4 weeks

PHASE 2 — Month 2: Knowledge Base
───────────────────────────────────
□ Install ChromaDB vector database
□ Load BabyPips full syllabus as text chunks
□ Load 5-10 classic trading books (PDFs → text)
□ Load historical strategy research papers
□ Connect AIMarketAdvisor to main bot loop
□ Bot now "reads" relevant strategies before each trade
📌 TARGET: Improve win rate to 55%+

PHASE 3 — Month 3: Elliott Wave + Advanced Patterns
────────────────────────────────────────────────────
□ Add Elliott Wave counter (5-wave impulse detection)
□ Add harmonic patterns (Gartley, Bat, Crab)
□ Add divergence detection (RSI divergence = reversal warning)
□ Add supply/demand zone detection (institutional levels)
□ Add news sentiment (scrape crypto news RSS feeds)
📌 TARGET: 60%+ win rate

PHASE 4 — Month 4: Machine Learning Layer
──────────────────────────────────────────
□ Log every trade with 50+ features to CSV
□ Train a simple ML model (XGBoost) on historical trades
□ Model predicts probability of winning given features
□ Only trade when ML model agrees with rule engine
□ Backtest on 2 years of historical data
📌 TARGET: 65%+ win rate, automated backtesting

PHASE 5 — Month 5+: Full Automation
──────────────────────────────────────
□ Add Zerodha Kite API for NSE stocks/F&O
□ Portfolio-level risk management across all markets
□ Self-reporting dashboard (Streamlit web app)
□ Automatic strategy parameter optimization
□ Multiple coins/assets simultaneously
📌 TARGET: Production-grade system
"""

if __name__ == "__main__":
    print(KNOWLEDGE_SOURCES)
    print(UPGRADE_ROADMAP)
    print("\n✅ RAG guide loaded. Follow the roadmap phase by phase.")
    print("   Start with Phase 1 (paper trading v2.0) for minimum 4 weeks.")
    print("   Only move to next phase when current phase target is achieved.")
