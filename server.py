#!/usr/bin/env python3
"""
Reddit MCP Server
A Model Context Protocol server that provides Reddit browsing capabilities
"""

import asyncio
import os
import sys
import re
from typing import Any, Sequence, List, Dict
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta
import time

import praw
import concurrent.futures
import yfinance as yf
from dotenv import load_dotenv
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, Tool, TextContent, ImageContent, EmbeddedResource
from pydantic import AnyUrl

# Load environment variables
load_dotenv()


def debug_print(message: str):
    """Print debug messages to stderr so they appear in Claude Desktop logs"""
    print(f"[Reddit MCP] {message}", file=sys.stderr)
    sys.stderr.flush()


class RedditMCPServer:
    def __init__(self):
        debug_print("Initializing Reddit MCP Server...")
        
        # Financial context keywords for validation
        self.financial_context = {
            'positive': ['buy', 'call', 'calls', 'long', 'bullish', 'moon', 'rocket', 'dd', 'yolo', 'hodl', 'diamond', 'hands'],
            'negative': ['sell', 'put', 'puts', 'short', 'bearish', 'crash', 'dump', 'bag', 'loss', 'rip'],
            'neutral': ['stock', 'share', 'shares', 'price', 'ticker', 'market', 'trading', 'earnings', 'ipo', 'options'],
            'indicators': ['$', 'usd', '%', 'pt', 'target', 'strike', 'exp', 'expiry', 'position']
        }
        
        # Short squeeze specific context for detection
        self.squeeze_context = {
            'indicators': [
                'squeeze', 'short squeeze', 'covering', 'shorts covering', 
                'short interest', 'CTB', 'cost to borrow', 'utilization',
                'days to cover', 'DTC', 'borrow rate', 'borrow fee',
                'float', 'short float', 'gamma squeeze', 'FTD',
                'failure to deliver', 'naked shorts', 'ortex', 'SI'
            ],
            'momentum': [
                'squeezing', 'mooning', 'shorts trapped', 'forced covering',
                'margin calls', 'breakout', 'parabolic', 'vertical', 'halted'
            ],
            'data_sources': [
                'fintel', 'ortex', 'marketwatch', 'shortablestocks',
                'highshortinterest', 'nakedshortreport'
            ]
        }
        
        # Comprehensive false positives list
        self.false_positives = {
            # Common words
            'IT', 'US', 'ON', 'BE', 'TO', 'OR', 'SO', 'GO', 'DO', 'NO', 'IF', 'IS', 'AS', 'AT', 'IN', 'UP', 'BY',
            'FOR', 'AND', 'THE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN', 'HAS', 'HAD', 'HER', 'HIM', 'HIS', 'HOW',
            'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'OUR', 'OUT', 'SHE', 'TWO', 'WHO', 'BOY', 'GET', 'USE', 'MAN',
            'DAY', 'TOO', 'ANY', 'MY', 'SAY', 'WAY', 'WE', 'WHERE', 'WHEN', 'WHAT', 'WILL', 'WORK',
            
            # Technical terms
            'HTTPS', 'HTTP', 'API', 'URL', 'JSON', 'XML', 'HTML', 'CSS', 'PDF', 'PNG', 'JPG', 'GIF',
            'FIRST', 'TOTAL', 'LAST', 'NEXT', 'PREV', 'FROM', 'INTO', 'OVER', 'UNDER', 'WITH',
            
            # Reddit specific
            'PM', 'AM', 'DD', 'TA', 'YOLO', 'WSB', 'GME', 'MODS', 'BOT', 'EDIT', 'TLDR'
        }
        
        # Symbol validation cache to avoid repeated API calls
        self.symbol_cache = {}
        self.cache_expiry = {}
        self.cache_duration = 3600  # 1 hour

        # Precompile regex patterns for performance
        self._dollar_pattern = re.compile(r'\$([A-Z]{1,5})\b')
        self._plain_pattern = re.compile(r'\b([A-Z]{2,5})\b')

        # Concurrency and rate limiting for external APIs
        self.api_semaphore = asyncio.Semaphore(10)
        self.api_call_times = deque()
        self._rate_lock = asyncio.Lock()
        
        # Initialize Reddit API connection
        try:
            self.reddit = praw.Reddit(
                client_id=os.getenv('REDDIT_CLIENT_ID'),
                client_secret=os.getenv('REDDIT_CLIENT_SECRET'),
                username=os.getenv('REDDIT_USERNAME'),
                password=os.getenv('REDDIT_PASSWORD'),
                user_agent=os.getenv('REDDIT_USER_AGENT', 'my-mcp-bot/1.0')
            )
            debug_print("Reddit API client created successfully")
        except Exception as e:
            debug_print(f"Failed to create Reddit API client: {e}")
            raise
        
        # Test the connection
        self.test_connection()
        
        # Initialize MCP server
        self.server = Server("reddit-mcp-server")
        debug_print("MCP Server initialized")
        self.setup_handlers()
        debug_print("MCP handlers set up successfully")
    
    async def validate_symbol_api(self, symbol: str) -> Dict:
        """Validate stock symbol using Yahoo Finance API with caching and timeouts"""
        current_time = time.time()
        
        # Check cache first
        if symbol in self.symbol_cache:
            if current_time < self.cache_expiry.get(symbol, 0):
                return self.symbol_cache[symbol]
        
        try:
            async with self.api_semaphore:
                await self._await_rate_limit()
                def fetch_info():
                    ticker = yf.Ticker(symbol)
                    return ticker.info
                info = await asyncio.wait_for(asyncio.to_thread(fetch_info), timeout=5.0)
            
            # Check if it's a valid stock
            if info and 'symbol' in info and info.get('regularMarketPrice'):
                result = {
                    'valid': True,
                    'name': info.get('longName', info.get('shortName', symbol)),
                    'price': info.get('regularMarketPrice', 0),
                    'market_cap': info.get('marketCap', 0),
                    'sector': info.get('sector', 'Unknown')
                }
            else:
                result = {'valid': False}
            
            # Cache the result
            self.symbol_cache[symbol] = result
            self.cache_expiry[symbol] = current_time + self.cache_duration
            
            return result
        
        except asyncio.TimeoutError:
            debug_print(f"API validation timed out for {symbol}")
            result = {'valid': False}
            self.symbol_cache[symbol] = result
            self.cache_expiry[symbol] = current_time + self.cache_duration
            return result
        except Exception as e:
            debug_print(f"API validation failed for {symbol}: {str(e)}")
            # Cache negative result to avoid repeated failures
            result = {'valid': False}
            self.symbol_cache[symbol] = result
            self.cache_expiry[symbol] = current_time + self.cache_duration
            return result
    
    async def detect_stocks_with_context(self, text: str, check_api: bool = True) -> List[Dict]:
        """Advanced stock detection with context analysis and confidence scoring (async)"""
        detected_stocks: List[Dict] = []
        text_upper = text.upper()
        
        # Run regex scans in parallel threads for large texts
        dollar_task = asyncio.to_thread(lambda: list(self._dollar_pattern.finditer(text_upper)))
        plain_task = asyncio.to_thread(lambda: list(self._plain_pattern.finditer(text_upper)))
        try:
            dollar_matches, plain_matches = await asyncio.gather(dollar_task, plain_task)
        except Exception as e:
            debug_print(f"Regex detection failed: {e}")
            dollar_matches, plain_matches = [], []

        # Dollar-prefixed symbols
        for match in dollar_matches:
            symbol = match.group(1)
            start_pos = match.start()
            if symbol not in self.false_positives and len(symbol) >= 1:
                context_info = self._analyze_context(text, start_pos, symbol)
                confidence = 0.8 + context_info['financial_score'] * 0.2
                stock_info = {
                    'symbol': symbol,
                    'confidence': min(confidence, 1.0),
                    'context': context_info['context_text'],
                    'pattern': 'dollar_prefix',
                    'financial_score': context_info['financial_score'],
                }
                squeeze_analysis = self._analyze_squeeze_context(context_info['context_text'])
                stock_info['squeeze_score'] = squeeze_analysis['squeeze_score']
                stock_info['is_squeeze_candidate'] = squeeze_analysis['is_squeeze_related']
                detected_stocks.append(stock_info)

        # Plain text symbols with stricter context filter
        for match in plain_matches:
            symbol = match.group(1)
            start_pos = match.start()
            if (symbol not in self.false_positives and
                len(symbol) >= 2 and
                symbol.isalpha() and
                symbol not in [s['symbol'] for s in detected_stocks]):
                context_info = self._analyze_context(text, start_pos, symbol)
                if context_info['financial_score'] >= 0.3:
                    confidence = 0.4 + context_info['financial_score'] * 0.4
                    stock_info = {
                        'symbol': symbol,
                        'confidence': confidence,
                        'context': context_info['context_text'],
                        'pattern': 'plain_text',
                        'financial_score': context_info['financial_score'],
                    }
                    squeeze_analysis = self._analyze_squeeze_context(context_info['context_text'])
                    stock_info['squeeze_score'] = squeeze_analysis['squeeze_score']
                    stock_info['is_squeeze_candidate'] = squeeze_analysis['is_squeeze_related']
                    detected_stocks.append(stock_info)

        # Optional API validation in batch
        if check_api and detected_stocks:
            unique_symbols = sorted({s['symbol'] for s in detected_stocks})
            results_map: Dict[str, Dict] = {}
            batch_size = 10
            for i in range(0, len(unique_symbols), batch_size):
                batch = unique_symbols[i:i + batch_size]
                tasks = [
                    asyncio.wait_for(self.validate_symbol_api(sym), timeout=5.0)
                    for sym in batch
                ]
                responses = await asyncio.gather(*tasks, return_exceptions=True)
                for sym, resp in zip(batch, responses):
                    if isinstance(resp, Exception):
                        if isinstance(resp, asyncio.TimeoutError):
                            debug_print(f"Validation timeout for {sym}")
                        else:
                            debug_print(f"Validation error for {sym}: {resp}")
                        results_map[sym] = {'valid': False}
                    else:
                        results_map[sym] = resp

            # Apply validation results
            validated_list: List[Dict] = []
            for stock in detected_stocks:
                api_result = results_map.get(stock['symbol'], {'valid': False})
                stock['api_validated'] = api_result.get('valid', False)
                if stock['api_validated']:
                    stock['company_name'] = api_result.get('name', stock['symbol'])
                    if stock['pattern'] == 'dollar_prefix':
                        stock['confidence'] = min(stock['confidence'] + 0.1, 1.0)
                    elif stock['pattern'] == 'plain_text':
                        stock['confidence'] = min(stock['confidence'] + 0.2, 1.0)
                    validated_list.append(stock)
                else:
                    # Drop plain symbols if API says invalid
                    if stock['pattern'] == 'plain_text':
                        continue
                    validated_list.append(stock)
            detected_stocks = validated_list

        # Sort by confidence
        detected_stocks.sort(key=lambda x: x['confidence'], reverse=True)
        return detected_stocks
    
    def _analyze_context(self, text: str, symbol_pos: int, symbol: str) -> Dict:
        """Analyze the context around a potential stock symbol"""
        # Extract context window (±50 characters around symbol)
        context_start = max(0, symbol_pos - 50)
        context_end = min(len(text), symbol_pos + len(symbol) + 50)
        context_text = text[context_start:context_end].strip()
        
        # Convert to lowercase for analysis
        context_lower = context_text.lower()
        
        # Score based on financial keywords
        financial_score = 0.0
        keyword_counts = defaultdict(int)
        
        for category, keywords in self.financial_context.items():
            for keyword in keywords:
                count = context_lower.count(keyword.lower())
                if count > 0:
                    keyword_counts[category] += count
                    
                    # Weight different categories
                    if category == 'indicators':
                        financial_score += count * 0.3
                    elif category in ['positive', 'negative']:
                        financial_score += count * 0.25
                    else:  # neutral
                        financial_score += count * 0.15
        
        # Normalize score
        financial_score = min(financial_score, 1.0)
        
        return {
            'context_text': context_text,
            'financial_score': financial_score,
            'keyword_counts': dict(keyword_counts)
        }
    
    def _analyze_squeeze_context(self, text: str) -> Dict:
        """
        Analyze text for short squeeze indicators and momentum
        Returns squeeze score and detailed metrics
        """
        
        # Convert text to lowercase for analysis
        text_lower = text.lower()
        
        # Initialize counters
        indicator_count = 0
        momentum_count = 0
        source_count = 0
        found_indicators = []
        
        # Check for squeeze indicators
        for indicator in self.squeeze_context['indicators']:
            if indicator.lower() in text_lower:
                indicator_count += 1
                found_indicators.append(indicator)
        
        # Check for momentum words
        for word in self.squeeze_context['momentum']:
            if word.lower() in text_lower:
                momentum_count += 1
        
        # Check for data source mentions
        for source in self.squeeze_context['data_sources']:
            if source.lower() in text_lower:
                source_count += 1
        
        # Calculate squeeze score (0-1)
        squeeze_score = min(
            (indicator_count * 0.4 + 
             momentum_count * 0.3 + 
             source_count * 0.3) / 5, 
            1.0
        )
        
        return {
            'squeeze_score': round(squeeze_score, 3),
            'indicator_count': indicator_count,
            'momentum_count': momentum_count,
            'source_mentions': source_count,
            'found_indicators': found_indicators,
            'is_squeeze_related': squeeze_score > 0.2
        }

    async def _await_rate_limit(self) -> None:
        """Enforce max 30 API calls per minute for external validation."""
        while True:
            async with self._rate_lock:
                now = time.monotonic()
                # Drop timestamps older than 60 seconds
                while self.api_call_times and now - self.api_call_times[0] > 60.0:
                    self.api_call_times.popleft()
                if len(self.api_call_times) < 30:
                    self.api_call_times.append(now)
                    return
                # Compute wait time until next slot is available
                sleep_time = 60.0 - (now - self.api_call_times[0])
        await asyncio.sleep(min(max(sleep_time, 0.05), 1.0))
    
    # Legacy method for backward compatibility
    def detect_stock_symbols(self, text: str) -> List[str]:
        """Legacy method - returns just symbol list for backward compatibility"""
        try:
            loop = asyncio.get_running_loop()
            in_loop = loop.is_running()
        except RuntimeError:
            in_loop = False
        
        if in_loop:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(lambda: asyncio.run(self.detect_stocks_with_context(text, check_api=False)))
                stocks = future.result(timeout=15)
        else:
            stocks = asyncio.run(self.detect_stocks_with_context(text, check_api=False))
        return [stock['symbol'] for stock in stocks if stock['confidence'] >= 0.6]
    
    def test_connection(self):
        """Test Reddit API connection"""
        debug_print("Testing Reddit API connection...")
        
        try:
            # Check environment variables first
            env_vars = {
                'REDDIT_CLIENT_ID': os.getenv('REDDIT_CLIENT_ID'),
                'REDDIT_CLIENT_SECRET': os.getenv('REDDIT_CLIENT_SECRET'),
                'REDDIT_USERNAME': os.getenv('REDDIT_USERNAME'),
                'REDDIT_PASSWORD': os.getenv('REDDIT_PASSWORD')
            }
            
            for var_name, var_value in env_vars.items():
                if var_value:
                    debug_print(f"✅ {var_name}: Set (length: {len(var_value)})")
                else:
                    debug_print(f"❌ {var_name}: Missing")
                    
            # Try to get the authenticated user
            debug_print("Attempting to authenticate with Reddit...")
            user = self.reddit.user.me()
            debug_print(f"✅ Successfully authenticated as Reddit user: {user}")
            
            # Test basic API call
            debug_print("Testing basic subreddit access...")
            test_subreddit = self.reddit.subreddit("test")
            debug_print(f"✅ Successfully connected to Reddit API")
            
            # Test getting a few posts to make sure everything works
            debug_print("Testing post retrieval...")
            posts = list(test_subreddit.hot(limit=2))
            debug_print(f"✅ Successfully retrieved {len(posts)} test posts")
            
            return True
            
        except Exception as e:
            debug_print(f"❌ Reddit API connection failed: {e}")
            debug_print("Environment variables status:")
            for var_name, var_value in env_vars.items():
                status = '✅ Set' if var_value else '❌ Missing'
                debug_print(f"  - {var_name}: {status}")
            raise e
    
    def setup_handlers(self):
        """Set up all the MCP handlers"""
        
        @self.server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            """List available tools"""
            debug_print("Listing available tools...")
            return [
                Tool(
                    name="get_hot_posts",
                    description="Get hot posts from a subreddit",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "subreddit": {
                                "type": "string",
                                "description": "Name of the subreddit (without r/)"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Number of posts to fetch (default: 10, max: 25)",
                                "default": 10,
                                "maximum": 25
                            }
                        },
                        "required": ["subreddit"]
                    }
                ),
                Tool(
                    name="search_reddit",
                    description="Search for posts across Reddit",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query"
                            },
                            "subreddit": {
                                "type": "string",
                                "description": "Limit search to specific subreddit (optional)"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Number of results (default: 10, max: 25)",
                                "default": 10,
                                "maximum": 25
                            }
                        },
                        "required": ["query"]
                    }
                ),
                Tool(
                    name="get_post_comments",
                    description="Get comments from a specific Reddit post",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "post_id": {
                                "type": "string",
                                "description": "Reddit post ID"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Number of top comments to fetch (default: 10)",
                                "default": 10
                            }
                        },
                        "required": ["post_id"]
                    }
                ),
                Tool(
                    name="analyze_stock_buzz",
                    description="Analyze Reddit buzz for a specific stock symbol",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Stock symbol to analyze (e.g., AAPL, TSLA)"
                            },
                            "days_back": {
                                "type": "integer",
                                "description": "Number of days to look back (default: 7)",
                                "default": 7,
                                "maximum": 30
                            },
                            "subreddit": {
                                "type": "string",
                                "description": "Specific subreddit to search (optional, default: all)"
                            }
                        },
                        "required": ["symbol"]
                    }
                ),
                Tool(
                    name="find_trending_stocks",
                    description="Find trending stocks mentioned on Reddit",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "subreddit": {
                                "type": "string",
                                "description": "Subreddit to scan (default: wallstreetbets)",
                                "default": "wallstreetbets"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Number of posts to scan (default: 50, max: 100)",
                                "default": 50,
                                "maximum": 100
                            }
                        }
                    }
                ),
                Tool(
                    name="smart_stock_scanner",
                    description="Advanced stock detection with context analysis and API validation",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "subreddit": {
                                "type": "string",
                                "description": "Subreddit to scan (default: wallstreetbets)",
                                "default": "wallstreetbets"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Number of posts to scan (default: 100, max: 200)",
                                "default": 100,
                                "maximum": 200
                            },
                            "min_confidence": {
                                "type": "number",
                                "description": "Minimum confidence threshold (0.0-1.0, default: 0.7)",
                                "default": 0.7,
                                "minimum": 0.0,
                                "maximum": 1.0
                            },
                            "validate_api": {
                                "type": "boolean",
                                "description": "Whether to validate symbols with Yahoo Finance API (default: true)",
                                "default": True
                            }
                        }
                    }
                ),
                Tool(
                    name="short_squeeze_scanner",
                    description="Scan Reddit for potential short squeeze candidates with detailed analysis",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "subreddits": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of subreddits to scan (default: ['shortsqueeze', 'supershortsqueeze', 'wallstreetbets'])",
                                "default": ["shortsqueeze", "supershortsqueeze", "wallstreetbets"]
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Posts per subreddit to scan (default: 50, max: 100)",
                                "default": 50,
                                "maximum": 100
                            },
                            "min_squeeze_score": {
                                "type": "number",
                                "description": "Minimum squeeze score threshold (0.0-1.0, default: 0.3)",
                                "default": 0.3,
                                "minimum": 0.0,
                                "maximum": 1.0
                            }
                        }
                    }
                )
            ]

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
            """Handle tool calls"""
            debug_print(f"Tool called: {name} with arguments: {arguments}")
            
            if arguments is None:
                arguments = {}
                
            try:
                if name == "get_hot_posts":
                    return await self.get_hot_posts(
                        arguments.get("subreddit", ""),
                        arguments.get("limit", 10)
                    )
                elif name == "search_reddit":
                    return await self.search_reddit(
                        arguments.get("query", ""),
                        arguments.get("subreddit", ""),
                        arguments.get("limit", 10)
                    )
                elif name == "get_post_comments":
                    return await self.get_post_comments(
                        arguments.get("post_id", ""),
                        arguments.get("limit", 10)
                    )
                elif name == "analyze_stock_buzz":
                    return await self.analyze_stock_buzz(
                        arguments.get("symbol", ""),
                        arguments.get("days_back", 7),
                        arguments.get("subreddit", "")
                    )
                elif name == "find_trending_stocks":
                    return await self.find_trending_stocks(
                        arguments.get("subreddit", "wallstreetbets"),
                        arguments.get("limit", 50)
                    )
                elif name == "smart_stock_scanner":
                    return await self.smart_stock_scanner(
                        arguments.get("subreddit", "wallstreetbets"),
                        arguments.get("limit", 100),
                        arguments.get("min_confidence", 0.7),
                        arguments.get("validate_api", True)
                    )
                elif name == "short_squeeze_scanner":
                    return await self.short_squeeze_scanner(
                        arguments.get("subreddits", ["shortsqueeze", "supershortsqueeze", "wallstreetbets"]),
                        arguments.get("limit", 50),
                        arguments.get("min_squeeze_score", 0.3)
                    )
                else:
                    debug_print(f"Unknown tool requested: {name}")
                    raise ValueError(f"Unknown tool: {name}")
                    
            except Exception as e:
                debug_print(f"Error in tool {name}: {str(e)}")
                return [TextContent(
                    type="text",
                    text=f"Error: {str(e)}"
                )]

    async def get_hot_posts(self, subreddit_name: str, limit: int) -> list[TextContent]:
        """Get hot posts from a subreddit"""
        debug_print(f"Getting {limit} hot posts from r/{subreddit_name}")
        
        try:
            subreddit = self.reddit.subreddit(subreddit_name)
            posts = []
            
            for post in subreddit.hot(limit=limit):
                post_info = f"""
**{post.title}**
- Author: u/{post.author}
- Score: {post.score} points
- Comments: {post.num_comments}
- Posted: {post.created_utc}
- URL: https://reddit.com{post.permalink}

{post.selftext[:300]}{'...' if len(post.selftext) > 300 else ''}

---
"""
                posts.append(post_info)
            
            debug_print(f"Successfully retrieved {len(posts)} hot posts from r/{subreddit_name}")
            result = f"🔥 Hot posts from r/{subreddit_name}:\n\n" + "\n".join(posts)
            return [TextContent(type="text", text=result)]
            
        except Exception as e:
            debug_print(f"Error fetching posts from r/{subreddit_name}: {str(e)}")
            return [TextContent(type="text", text=f"Error fetching posts from r/{subreddit_name}: {str(e)}")]

    async def search_reddit(self, query: str, subreddit_name: str, limit: int) -> list[TextContent]:
        """Search Reddit for posts"""
        search_location = f"r/{subreddit_name}" if subreddit_name else "all of Reddit"
        debug_print(f"Searching for '{query}' in {search_location} (limit: {limit})")
        
        try:
            if subreddit_name:
                subreddit = self.reddit.subreddit(subreddit_name)
                search_results = subreddit.search(query, limit=limit)
            else:
                search_results = self.reddit.subreddit("all").search(query, limit=limit)
            
            posts = []
            for post in search_results:
                post_info = f"""
**{post.title}**
- Subreddit: r/{post.subreddit}
- Author: u/{post.author}
- Score: {post.score} points
- Comments: {post.num_comments}
- URL: https://reddit.com{post.permalink}

{post.selftext[:200]}{'...' if len(post.selftext) > 200 else ''}

---
"""
                posts.append(post_info)
            
            debug_print(f"Found {len(posts)} results for '{query}' in {search_location}")
            
            if not posts:
                result = f"🔍 No results found for '{query}' in {search_location}"
            else:
                result = f"🔍 Search results for '{query}' in {search_location}:\n\n" + "\n".join(posts)
                
            return [TextContent(type="text", text=result)]
            
        except Exception as e:
            debug_print(f"Error searching for '{query}': {str(e)}")
            return [TextContent(type="text", text=f"Error searching for '{query}': {str(e)}")]

    async def get_post_comments(self, post_id: str, limit: int) -> list[TextContent]:
        """Get comments from a Reddit post"""
        debug_print(f"Getting {limit} comments for post ID: {post_id}")
        
        try:
            submission = self.reddit.submission(id=post_id)
            submission.comments.replace_more(limit=0)
            
            post_info = f"""
**Post: {submission.title}**
- Author: u/{submission.author}
- Score: {submission.score} points
- Subreddit: r/{submission.subreddit}
- URL: https://reddit.com{submission.permalink}

{submission.selftext[:300]}{'...' if len(submission.selftext) > 300 else ''}

---

**Top Comments:**

"""
            
            comments = []
            for i, comment in enumerate(submission.comments[:limit]):
                if hasattr(comment, 'body'):
                    comment_text = f"""
💬 **Comment {i+1}** (Score: {comment.score})
Author: u/{comment.author}

{comment.body[:400]}{'...' if len(comment.body) > 400 else ''}

---
"""
                    comments.append(comment_text)
            
            debug_print(f"Successfully retrieved {len(comments)} comments for post {post_id}")
            result = post_info + "\n".join(comments)
            return [TextContent(type="text", text=result)]
            
        except Exception as e:
            debug_print(f"Error fetching comments for post {post_id}: {str(e)}")
            return [TextContent(type="text", text=f"Error fetching comments for post {post_id}: {str(e)}")]

    async def analyze_stock_buzz(self, symbol: str, days_back: int, subreddit_name: str) -> list[TextContent]:
        """Analyze Reddit buzz for a specific stock symbol"""
        symbol = symbol.upper().strip()
        debug_print(f"Analyzing buzz for {symbol} over {days_back} days in {subreddit_name or 'all subreddits'}")
        
        if not symbol:
            return [TextContent(type="text", text="Error: No stock symbol provided")]
        
        try:
            # Search for the stock symbol
            search_queries = [f"${symbol}", symbol]
            all_posts = []
            daily_counts = Counter()
            
            for query in search_queries:
                if subreddit_name:
                    subreddit = self.reddit.subreddit(subreddit_name)
                    search_results = subreddit.search(query, limit=100, time_filter="week")
                    search_location = f"r/{subreddit_name}"
                else:
                    search_results = self.reddit.subreddit("all").search(query, limit=100, time_filter="week")
                    search_location = "all of Reddit"
                
                for post in search_results:
                    # Check if post is within our date range
                    post_date = datetime.fromtimestamp(post.created_utc)
                    days_ago = (datetime.now() - post_date).days
                    
                    if days_ago <= days_back:
                        all_posts.append(post)
                        day_key = post_date.strftime("%Y-%m-%d")
                        daily_counts[day_key] += 1
            
            # Remove duplicates by post ID
            unique_posts = {post.id: post for post in all_posts}.values()
            total_mentions = len(unique_posts)
            
            if total_mentions == 0:
                result = f"📊 **Stock Buzz Analysis for ${symbol}**\n\n❌ No mentions found in {search_location} over the last {days_back} days."
                return [TextContent(type="text", text=result)]
            
            # Calculate trend
            avg_per_day = total_mentions / days_back
            recent_days = sorted(daily_counts.keys())[-3:] if len(daily_counts) >= 3 else sorted(daily_counts.keys())
            recent_avg = sum(daily_counts[day] for day in recent_days) / len(recent_days) if recent_days else 0
            
            # Determine trend
            if recent_avg > avg_per_day * 1.2:
                trend = "📈 **TRENDING UP**"
                trend_emoji = "🔥"
            elif recent_avg < avg_per_day * 0.8:
                trend = "📉 **TRENDING DOWN**"
                trend_emoji = "❄️"
            else:
                trend = "➡️ **STABLE**"
                trend_emoji = "📊"
            
            # Build result
            result = f"""📊 **Stock Buzz Analysis for ${symbol}** {trend_emoji}

**📍 Search Location:** {search_location}
**📅 Time Period:** Last {days_back} days
**💬 Total Mentions:** {total_mentions} posts
**📈 Average per Day:** {avg_per_day:.1f} mentions
**🎯 Recent Trend:** {trend}

**📅 Daily Breakdown:**
"""
            
            # Add daily breakdown
            for day in sorted(daily_counts.keys()):
                count = daily_counts[day]
                bar = "█" * min(count, 20)
                result += f"  {day}: {count:2d} mentions {bar}\n"
            
            # Add top posts
            sorted_posts = sorted(unique_posts, key=lambda x: x.score, reverse=True)[:5]
            if sorted_posts:
                result += f"\n**🔥 Top Posts Mentioning ${symbol}:**\n\n"
                for i, post in enumerate(sorted_posts, 1):
                    result += f"**{i}.** {post.title[:80]}{'...' if len(post.title) > 80 else ''}\n"
                    result += f"   📍 r/{post.subreddit} | 👍 {post.score} | 💬 {post.num_comments}\n"
                    result += f"   🔗 https://reddit.com{post.permalink}\n\n"
            
            return [TextContent(type="text", text=result)]
            
        except Exception as e:
            debug_print(f"Error analyzing stock buzz for {symbol}: {str(e)}")
            return [TextContent(type="text", text=f"Error analyzing buzz for ${symbol}: {str(e)}")]
    
    async def smart_stock_scanner(self, subreddit_name: str, limit: int, min_confidence: float, validate_api: bool) -> list[TextContent]:
        """Advanced stock scanner with context analysis and API validation (async, non-blocking)."""
        debug_print(f"Smart stock scanning in r/{subreddit_name} (limit: {limit}, min_confidence: {min_confidence}, API validation: {validate_api})")
        
        start_time = time.monotonic()
        all_detected_stocks: List[Dict] = []
        posts_scanned = 0
        posts_with_stocks = 0
        
        try:
            # Fetch posts without blocking the event loop
            try:
                posts = await asyncio.wait_for(
                    asyncio.to_thread(lambda: list(self.reddit.subreddit(subreddit_name).hot(limit=limit))),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                debug_print("Timeout fetching hot posts")
                posts = []
            
            debug_print(f"Fetched {len(posts)} posts to scan")
            
            for idx, post in enumerate(posts, 1):
                posts_scanned += 1
                text_to_check = f"{post.title} {post.selftext}"
                stocks = await self.detect_stocks_with_context(text_to_check, check_api=False)
                if stocks:
                    posts_with_stocks += 1
                    for stock in stocks:
                        stock['post_title'] = post.title
                        stock['post_score'] = post.score
                        stock['post_comments'] = post.num_comments
                        stock['post_url'] = f"https://reddit.com{post.permalink}"
                        all_detected_stocks.append(stock)
                # Check top comments for additional context (non-blocking)
                try:
                    # Replace 'more' comments
                    await asyncio.wait_for(asyncio.to_thread(post.comments.replace_more, 0), timeout=5.0)
                    # Take top 5 comments
                    comments = await asyncio.wait_for(
                        asyncio.to_thread(lambda: [c for c in post.comments[:5] if hasattr(c, 'body')]),
                        timeout=5.0,
                    )
                    for comment in comments:
                        comment_stocks = await self.detect_stocks_with_context(comment.body, check_api=False)
                        for stock in comment_stocks:
                            stock['confidence'] *= 0.8
                            stock['post_title'] = post.title
                            stock['post_score'] = post.score
                            stock['found_in'] = 'comment'
                            all_detected_stocks.append(stock)
                except asyncio.TimeoutError:
                    debug_print(f"Timeout fetching comments for post {getattr(post, 'id', '?')}")
                except Exception as e:
                    debug_print(f"Error fetching comments: {e}")
                
                # Progress logs and time budget check
                if idx % 10 == 0:
                    debug_print(f"Scanned {idx}/{len(posts)} posts...")
                if time.monotonic() - start_time > 25.0:
                    debug_print("Time budget nearing limit; stopping post scan early")
                    break
            
            # Batch API validation after collection to maximize concurrency
            if validate_api and all_detected_stocks:
                unique_symbols = sorted({s['symbol'] for s in all_detected_stocks})
                debug_print(f"Validating {len(unique_symbols)} unique symbols in batches")
                results_map: Dict[str, Dict] = {}
                batch_size = 10
                for i in range(0, len(unique_symbols), batch_size):
                    if time.monotonic() - start_time > 28.0:
                        debug_print("Time budget nearing limit; stopping validation early")
                        break
                    batch = unique_symbols[i:i + batch_size]
                    tasks = [
                        asyncio.wait_for(self.validate_symbol_api(sym), timeout=5.0)
                        for sym in batch
                    ]
                    responses = await asyncio.gather(*tasks, return_exceptions=True)
                    for sym, resp in zip(batch, responses):
                        if isinstance(resp, Exception):
                            if isinstance(resp, asyncio.TimeoutError):
                                debug_print(f"Validation timeout for {sym}")
                            else:
                                debug_print(f"Validation error for {sym}: {resp}")
                            results_map[sym] = {'valid': False}
                        else:
                            results_map[sym] = resp
                
                # Apply validation results to mentions
                updated_mentions: List[Dict] = []
                for m in all_detected_stocks:
                    api_result = results_map.get(m['symbol'], {'valid': False})
                    m['api_validated'] = api_result.get('valid', False)
                    if m['api_validated']:
                        m['company_name'] = api_result.get('name', m['symbol'])
                        if m.get('pattern') == 'dollar_prefix':
                            m['confidence'] = min(m['confidence'] + 0.1, 1.0)
                        elif m.get('pattern') == 'plain_text':
                            m['confidence'] = min(m['confidence'] + 0.2, 1.0)
                        updated_mentions.append(m)
                    else:
                        # Drop unvalidated plain text mentions
                        if m.get('pattern') == 'plain_text':
                            continue
                        updated_mentions.append(m)
                all_detected_stocks = updated_mentions
            
            # Filter by confidence and aggregate
            high_confidence_stocks = [s for s in all_detected_stocks if s['confidence'] >= min_confidence]
            if not high_confidence_stocks:
                result = f"🎯 **SMART STOCK DETECTION RESULTS** - r/{subreddit_name}\n\n"
                result += f"❌ No high-confidence stocks found (min confidence: {min_confidence:.0%})\n"
                result += f"📊 Scanned {posts_scanned} posts, found stocks in {posts_with_stocks} posts"
                return [TextContent(type="text", text=result)]
            
            # Aggregate by symbol
            symbol_data = defaultdict(list)
            for stock in high_confidence_stocks:
                symbol_data[stock['symbol']].append(stock)
            
            # Calculate aggregated metrics
            aggregated_stocks = []
            for symbol, mentions in symbol_data.items():
                avg_confidence = sum(m['confidence'] for m in mentions) / len(mentions)
                total_mentions = len(mentions)
                max_confidence = max(m['confidence'] for m in mentions)
                best_mention = max(mentions, key=lambda x: x['confidence'])
                aggregated_stocks.append({
                    'symbol': symbol,
                    'total_mentions': total_mentions,
                    'avg_confidence': avg_confidence,
                    'max_confidence': max_confidence,
                    'api_validated': best_mention.get('api_validated', False),
                    'company_name': best_mention.get('company_name', symbol),
                    'best_context': best_mention['context'],
                    'top_post': best_mention.get('post_title', ''),
                    'top_score': best_mention.get('post_score', 0)
                })
            
            # Sort by total mentions and confidence
            aggregated_stocks.sort(key=lambda x: (x['total_mentions'], x['max_confidence']), reverse=True)
            
            # Build result
            result = f"🎯 **SMART STOCK DETECTION RESULTS** - r/{subreddit_name}\n\n"
            result += f"📊 **Scan Summary:**\n"
            result += f"- Posts Scanned: {posts_scanned}\n"
            result += f"- Posts with Stocks: {posts_with_stocks}\n"
            result += f"- High Confidence Detections: {len(high_confidence_stocks)}\n"
            result += f"- Unique Symbols: {len(symbol_data)}\n"
            result += f"- Min Confidence Threshold: {min_confidence:.0%}\n\n"
            
            # Categorize by confidence
            high_conf = [s for s in aggregated_stocks if s['max_confidence'] >= 0.9]
            medium_conf = [s for s in aggregated_stocks if 0.7 <= s['max_confidence'] < 0.9]
            low_conf = [s for s in aggregated_stocks if s['max_confidence'] < 0.7]
            
            if high_conf:
                result += "🔥 **HIGH CONFIDENCE (90%+):**\n"
                for stock in high_conf[:10]:
                    status = "✅ VERIFIED" if stock['api_validated'] else "⚠️ UNVERIFIED"
                    result += f"**${stock['symbol']}** - {stock['total_mentions']} mentions - {status}\n"
                    result += f"   💼 {stock['company_name']}\n"
                    result += f"   📝 Context: \"{stock['best_context'][:60]}...\"\n"
                    result += f"   🏆 Top post: {stock['top_post'][:50]}... ({stock['top_score']} upvotes)\n\n"
            
            if medium_conf:
                result += "⚠️ **MEDIUM CONFIDENCE (70-89%):**\n"
                for stock in medium_conf[:5]:
                    status = "✅ VERIFIED" if stock['api_validated'] else "❌ UNVERIFIED"
                    result += f"**${stock['symbol']}** - {stock['total_mentions']} mentions - {status}\n"
                    result += f"   📝 \"{stock['best_context'][:40]}...\"\n\n"
            
            # Show filtered out examples
            low_conf_symbols = [s['symbol'] for s in all_detected_stocks if s['confidence'] < min_confidence]
            if low_conf_symbols:
                filtered_count = Counter(low_conf_symbols)
                result += f"🚫 **FILTERED OUT (< {min_confidence:.0%} confidence):**\n"
                for symbol, count in filtered_count.most_common(5):
                    result += f"- ${symbol} ({count} mentions)\n"
                result += "\n"
            
            result += "💡 **Tips:**\n"
            result += "- Higher confidence = better context + API validation\n"
            result += "- Use `analyze_stock_buzz` for detailed analysis of specific symbols\n"
            result += "- Adjust `min_confidence` to filter results (0.7 = balanced, 0.9 = very strict)"
            
            return [TextContent(type="text", text=result)]
        
        except Exception as e:
            debug_print(f"Error in smart stock scanner: {str(e)}")
            return [TextContent(type="text", text=f"Error in smart stock scanner: {str(e)}")]

    async def find_trending_stocks(self, subreddit_name: str, limit: int) -> list[TextContent]:
        """Enhanced trending stocks with smart detection"""
        debug_print(f"Finding trending stocks in r/{subreddit_name} using smart detection")
        
        try:
            subreddit = self.reddit.subreddit(subreddit_name)
            all_stocks = []
            posts_scanned = 0
            
            for post in subreddit.hot(limit=limit):
                posts_scanned += 1
                text_to_check = f"{post.title} {post.selftext}"
                stocks = await self.detect_stocks_with_context(text_to_check, check_api=False)
                
                # Only include high-confidence detections
                for stock in stocks:
                    if stock['confidence'] >= 0.7:
                        all_stocks.append(stock)
            
            if not all_stocks:
                result = f"📊 **Trending Stocks in r/{subreddit_name}** (Smart Detection)\n\n"
                result += f"❌ No high-confidence stocks found in {posts_scanned} posts"
                return [TextContent(type="text", text=result)]
            
            # Count occurrences
            symbol_counts = Counter(stock['symbol'] for stock in all_stocks)
            
            result = f"📊 **Trending Stocks in r/{subreddit_name}** (Smart Detection) 🧠\n\n"
            result += f"**📈 Scan Results:**\n"
            result += f"- Posts Scanned: {posts_scanned}\n"
            result += f"- High-Confidence Detections: {len(all_stocks)}\n"
            result += f"- Unique Symbols: {len(symbol_counts)}\n\n"
            result += f"**🏆 Top Trending (High Confidence Only):**\n\n"
            
            for i, (symbol, count) in enumerate(symbol_counts.most_common(15), 1):
                if count >= 5:
                    indicator = "🔥🔥🔥"
                elif count >= 3:
                    indicator = "🔥🔥"
                elif count >= 2:
                    indicator = "🔥"
                else:
                    indicator = "📈"
                
                bar = "█" * min(count * 2, 20)
                result += f"**{i:2d}.** ${symbol} {indicator}\n"
                result += f"     {count} high-confidence mentions {bar}\n\n"
            
            result += "💡 **Smart Detection Features:**\n"
            result += "- Context analysis eliminates false positives\n"
            result += "- Only shows symbols with 70%+ confidence\n"
            result += "- Filters out technical terms like $HTTPS, $API, etc."
            
            return [TextContent(type="text", text=result)]
            
        except Exception as e:
            debug_print(f"Error in enhanced trending stocks: {str(e)}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    async def short_squeeze_scanner(self, subreddits: list, limit: int, min_squeeze_score: float) -> list[TextContent]:
        """Scan multiple subreddits for potential short squeeze candidates"""
        debug_print(f"Scanning for squeeze candidates in {subreddits} (limit: {limit}, min_score: {min_squeeze_score})")
        
        try:
            all_squeeze_candidates = []
            posts_analyzed = 0
            
            # Scan each subreddit
            for subreddit_name in subreddits:
                debug_print(f"Scanning r/{subreddit_name}...")
                try:
                    subreddit = self.reddit.subreddit(subreddit_name)
                    
                    # Get hot posts
                    for post in subreddit.hot(limit=limit):
                        posts_analyzed += 1
                        text_to_analyze = f"{post.title} {post.selftext}"
                        
                        # Detect stocks with context
                        stocks = await self.detect_stocks_with_context(text_to_analyze, check_api=False)
                        
                        # Filter for squeeze candidates
                        for stock in stocks:
                            if stock.get('squeeze_score', 0) >= min_squeeze_score:
                                stock['post_title'] = post.title[:100]
                                stock['post_score'] = post.score
                                stock['post_url'] = f"https://reddit.com{post.permalink}"
                                stock['subreddit'] = subreddit_name
                                all_squeeze_candidates.append(stock)
                                
                except Exception as e:
                    debug_print(f"Error scanning r/{subreddit_name}: {str(e)}")
                    continue
        
            if not all_squeeze_candidates:
                return [TextContent(
                    type="text",
                    text=f"🔍 **SHORT SQUEEZE SCANNER**\n\n"
                         f"❌ No squeeze candidates found above {min_squeeze_score:.0%} threshold\n"
                         f"📊 Scanned {posts_analyzed} posts across {len(subreddits)} subreddits"
                )]
        
            # Aggregate by symbol
            from collections import defaultdict
            symbol_aggregates = defaultdict(list)
            for candidate in all_squeeze_candidates:
                symbol_aggregates[candidate['symbol']].append(candidate)
        
            # Build results
            result = f"🎯 **SHORT SQUEEZE SCANNER RESULTS**\n\n"
            result += f"📊 **Scan Summary:**\n"
            result += f"- Subreddits: {', '.join(['r/'+s for s in subreddits])}\n"
            result += f"- Posts Analyzed: {posts_analyzed}\n"
            result += f"- Squeeze Candidates Found: {len(all_squeeze_candidates)}\n"
            result += f"- Unique Symbols: {len(symbol_aggregates)}\n"
            result += f"- Min Squeeze Score: {min_squeeze_score:.0%}\n\n"
        
            # Sort by average squeeze score
            sorted_symbols = sorted(
                symbol_aggregates.items(),
                key=lambda x: sum(s['squeeze_score'] for s in x[1]) / len(x[1]),
                reverse=True
            )
        
            result += "🔥 **TOP SQUEEZE CANDIDATES:**\n\n"
            for rank, (symbol, mentions) in enumerate(sorted_symbols[:10], 1):
                avg_squeeze = sum(m['squeeze_score'] for m in mentions) / len(mentions)
                max_squeeze = max(m['squeeze_score'] for m in mentions)
                mention_count = len(mentions)
                
                # Determine squeeze level
                if avg_squeeze > 0.7:
                    level = "🚨 CRITICAL"
                elif avg_squeeze > 0.5:
                    level = "🔥 HIGH"
                elif avg_squeeze > 0.3:
                    level = "⚠️ MODERATE"
                else:
                    level = "👀 WATCH"
                
                result += f"**{rank}. ${symbol}** - {level}\n"
                result += f"   📈 Squeeze Score: {avg_squeeze:.1%} (max: {max_squeeze:.1%})\n"
                result += f"   💬 Mentions: {mention_count} across subreddits\n"
                result += f"   🏆 Top post: {mentions[0]['post_title']}\n"
                result += f"   📍 Most active: r/{mentions[0]['subreddit']}\n\n"
        
            result += "💡 **Tips:**\n"
            result += "- Higher squeeze scores indicate more squeeze-related discussion\n"
            result += "- Check r/shortsqueeze for dedicated squeeze plays\n"
            result += "- Use analyze_stock_buzz for detailed timeline analysis"
        
            return [TextContent(type="text", text=result)]
        
        except Exception as e:
            debug_print(f"Error in squeeze scanner: {str(e)}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    async def run(self):
        """Run the server"""
        debug_print("Starting MCP server...")
        
        try:
            async with stdio_server() as (read_stream, write_stream):
                debug_print("MCP server stdio connection established")
                await self.server.run(
                    read_stream,
                    write_stream,
                    InitializationOptions(
                        server_name="reddit-mcp-server",
                        server_version="1.0.0",
                        capabilities=self.server.get_capabilities(
                            notification_options=NotificationOptions(),
                            experimental_capabilities={},
                        ),
                    ),
                )
        except Exception as e:
            debug_print(f"Error running MCP server: {str(e)}")
            raise


if __name__ == "__main__":
    debug_print("Reddit MCP Server starting up...")
    try:
        server = RedditMCPServer()
        debug_print("Server initialized successfully, starting async loop...")
        asyncio.run(server.run())
    except Exception as e:
        debug_print(f"Failed to start server: {str(e)}")
        sys.exit(1)