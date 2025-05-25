# Reddit MCP Server 🚀

A powerful **Model Context Protocol (MCP) server** that provides Reddit browsing capabilities with advanced stock symbol detection and analysis. This server enables AI assistants to browse Reddit, analyze stock buzz, and detect trending stocks with intelligent context analysis.

## 🌟 Features

### Core Reddit Functionality
- **Browse Hot Posts**: Get trending posts from any subreddit
- **Search Reddit**: Search for posts across Reddit or specific subreddits
- **Comment Analysis**: Extract and analyze comments from specific posts
- **Real-time Data**: Access live Reddit data through PRAW API

### Advanced Stock Detection 🧠
- **Smart Symbol Detection**: Context-aware stock symbol detection with confidence scoring
- **API Validation**: Real-time validation using Yahoo Finance API
- **False Positive Filtering**: Eliminates technical terms like $HTTPS, $API, etc.
- **Confidence Scoring**: Advanced algorithms rate detection quality (0-100%)

### Stock Analysis Tools 📊
- **Stock Buzz Analysis**: Track mention frequency and trends over time
- **Trending Stock Scanner**: Find the most mentioned stocks with context
- **Smart Stock Scanner**: Advanced detection with API validation and confidence metrics
- **Daily Breakdowns**: Visualize mention patterns over time

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Reddit API credentials
- pip package manager

### 1. Clone the Repository
```bash
git clone https://github.com/MaorAmsallem/reddit-mcp-server.git
cd reddit-mcp-server
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Reddit API
1. Go to [Reddit Apps](https://www.reddit.com/prefs/apps/)
2. Click "Create App" or "Create Another App"
3. Choose "script" as the app type
4. Copy your credentials to `.env` file:

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 4. Run the Server
```bash
python server.py
```

## 📋 Available Tools

### Basic Reddit Tools
- `get_hot_posts` - Get trending posts from a subreddit
- `search_reddit` - Search for posts with custom queries
- `get_post_comments` - Extract comments from specific posts

### Stock Analysis Tools
- `analyze_stock_buzz` - Analyze Reddit buzz for specific stock symbols
- `find_trending_stocks` - Find trending stocks (basic detection)
- `smart_stock_scanner` - Advanced stock detection with AI validation

## 🎯 Smart Stock Detection

Our advanced stock detection system uses multiple layers of analysis:

### Layer 1: Pattern Recognition
```python
# Detects both formats
$AAPL  # Dollar prefix (high confidence)
AAPL   # Plain text (requires context validation)
```

### Layer 2: Context Analysis
```python
# Financial context keywords boost confidence
"buying AAPL calls"     # High confidence
"AAPL earnings report"  # Medium confidence
"AAPL website URL"      # Low confidence (filtered out)
```

### Layer 3: API Validation
- Real-time Yahoo Finance API validation
- Caching to prevent duplicate API calls
- Company name and sector information

### Layer 4: Confidence Scoring
```
90%+ = High Confidence (🔥🔥🔥)
70-89% = Medium Confidence (🔥🔥)
50-69% = Low Confidence (🔥)
<50% = Filtered Out (❌)
```

## 🔧 Configuration

### Environment Variables
```bash
# Reddit API Configuration
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USERNAME=your_username
REDDIT_PASSWORD=your_password
REDDIT_USER_AGENT=my-mcp-bot/1.0
```

### MCP Client Setup
Add to your MCP client configuration:
```json
{
  "mcpServers": {
    "reddit-mcp-server": {
      "command": "python",
      "args": ["path/to/reddit-mcp-server/server.py"],
      "env": {
        "REDDIT_CLIENT_ID": "your_client_id",
        "REDDIT_CLIENT_SECRET": "your_client_secret",
        "REDDIT_USERNAME": "your_username",
        "REDDIT_PASSWORD": "your_password"
      }
    }
  }
}
```

## 📊 Example Usage

### Find Trending Stocks
```python
# Basic trending stocks
find_trending_stocks(subreddit="wallstreetbets", limit=100)

# Smart detection with high confidence
smart_stock_scanner(
    subreddit="wallstreetbets",
    limit=200,
    min_confidence=0.8,
    validate_api=True
)
```

### Analyze Stock Buzz
```python
# Analyze TSLA mentions over the last 7 days
analyze_stock_buzz(
    symbol="TSLA",
    days_back=7,
    subreddit="wallstreetbets"
)
```

### Search Reddit
```python
# Search for posts about AI
search_reddit(
    query="artificial intelligence",
    subreddit="technology",
    limit=20
)
```

## 🛡️ Security & Privacy

- **Environment Variables**: Sensitive credentials stored in `.env` file
- **Rate Limiting**: Built-in respect for Reddit API limits
- **Error Handling**: Comprehensive error handling and logging
- **No Data Storage**: No persistent storage of Reddit data

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔗 Dependencies

- **praw**: Reddit API wrapper
- **yfinance**: Yahoo Finance API for stock validation
- **mcp**: Model Context Protocol SDK
- **python-dotenv**: Environment variable management
- **asyncio**: Async programming support

## 🐛 Troubleshooting

### Common Issues

**Reddit Authentication Failed**
```bash
# Check your .env file
cat .env
# Verify credentials at https://www.reddit.com/prefs/apps/
```

**Stock Detection Not Working**
```bash
# Install yfinance for API validation
pip install yfinance
# Check internet connection for API calls
```

**MCP Connection Issues**
```bash
# Check server output for debug messages
python server.py
# Verify MCP client configuration
```

## 📈 Roadmap

- [ ] Add sentiment analysis for stock mentions
- [ ] Support for multiple stock exchanges
- [ ] Historical data analysis
- [ ] Integration with financial news APIs
- [ ] Advanced visualization tools
- [ ] Real-time notifications

## 🙋‍♂️ Support

If you have questions or need help:

1. Check the [Issues](https://github.com/MaorAmsallem/reddit-mcp-server/issues) page
2. Create a new issue with detailed information
3. Join our community discussions

## ⭐ Show Your Support

If this project helped you, please give it a ⭐ on GitHub!

---

**Made with ❤️ for the MCP and Reddit communities**
