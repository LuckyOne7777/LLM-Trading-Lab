# LLM Trading Lab 🤖📈

> **Can AI Generate Alpha?** A 6-month live experiment where ChatGPT manages a real-money micro-cap portfolio.

[![Research Paper](https://img.shields.io/badge/Research-Paper-blue)](Experiments/chatgpt_micro-cap/evaluation/paper.pdf)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 📖 Table of Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [How It Works](#how-it-works)
- [Results & Research](#results--research)
- [Repository Structure](#repository-structure)
- [Running Your Own Experiment](#running-your-own-experiment)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Contributing](#contributing)
- [Contact](#contact)

---

## 🎯 Overview

**LLM Trading Lab** started as a simple question: *"Can ChatGPT actually pick winning stocks?"*

What began as a **$100 micro-cap trading experiment** has evolved into a **research framework** for studying how large language models behave as portfolio decision-makers.

### Key Highlights

- 💰 **Real Money**: Started with $100, managed by ChatGPT
- 📊 **6 Months Live**: Forward-only decisions, no backtesting
- 📝 **Full Transparency**: Every decision, trade, and chat logged
- 🔬 **Research-Grade**: 40-page evaluation with CAPM, Sharpe, Sortino analysis
- 🏆 **Benchmarked**: Compared against S&P 500 and Russell 2000

**Full Research Paper**: [Evaluating ChatGPT as a Portfolio Decision-Maker](Experiments/chatgpt_micro-cap/evaluation/paper.pdf)

---

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/LuckyOne7777/LLM-Trading-Lab.git
cd LLM-Trading-Lab

# Install dependencies
pip install -r requirements.txt

# Run the trading script
cd Experiments/chatgpt_micro_cap
python trading_script.py

# Generate performance graphs
make graphs
```

---

## 📦 Installation

### Prerequisites

- **Python 3.11+** (required)
- **OpenAI API Key** (for ChatGPT integration)
- **Internet connection** (for real-time market data)

### Step-by-Step Setup

#### 1. Clone the Repository

```bash
git clone https://github.com/LuckyOne7777/LLM-Trading-Lab.git
cd LLM-Trading-Lab
```

#### 2. Create Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate

# On Windows:
.\venv\Scripts\activate
```

#### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

**Dependencies:**
- `pandas` - Data manipulation
- `yfinance` - Market data (primary source)
- `matplotlib` - Visualization
- `openai` - ChatGPT API
- `python-dotenv` - Environment variables

#### 4. Configure API Keys

Create a `.env` file in the project root:

```bash
OPENAI_API_KEY=your-openai-api-key-here
```

**Get OpenAI API Key:**
1. Go to [platform.openai.com](https://platform.openai.com)
2. Create an account
3. Navigate to API keys
4. Generate a new key

---

## 🎮 How It Works

### The Trading Loop

```
1. ChatGPT analyzes market data
   ↓
2. Selects stocks based on criteria
   ↓
3. Makes buy/sell decisions
   ↓
4. Executes trades (simulated or real)
   ↓
5. Logs decisions and results
   ↓
6. Repeat daily
```

### Trading Rules

**Hard Constraints:**
- Maximum 5 positions at once
- $20 maximum per position
- 10% stop-loss on all positions
- Micro-cap stocks only (market cap < $300M)
- No day trading (hold minimum 1 day)

**Decision Process:**
1. ChatGPT receives daily market data
2. Analyzes fundamentals and technicals
3. Provides reasoning for each decision
4. Executes trades within constraints

---

## 📊 Results & Research

### Performance Summary

**6-Month Results:**
- Starting Capital: $100
- Final Value: [See evaluation paper]
- Total Trades: [See Trade_Log.csv]
- Win Rate: [See metrics]
- Sharpe Ratio: [See evaluation]
- Max Drawdown: [See evaluation]

### Research Artifacts

**📄 Full Evaluation**: [40-page PDF](Experiments/chatgpt_micro-cap/evaluation/paper.pdf)

**📈 Key Findings**:
- [Deep Research Index](Experiments/chatgpt_micro-cap/collected_artifacts/deep_research_index.md)
- [Decision Logs & Chats](Experiments/chatgpt_micro-cap/collected_artifacts/chats.md)
- [Weekly Research Summaries](Experiments/chatgpt_micro-cap/collected_artifacts/Weekly_Deep_Research_MD/)

**📊 Data Files**:
- [Daily Updates CSV](Experiments/chatgpt_micro_cap/csv_files/Daily_Updates.csv)
- [Trade Log CSV](Experiments/chatgpt_micro_cap/csv_files/Trade_Log.csv)

---

## 🏗️ Repository Structure

```
LLM-Trading-Lab/
│
├── README.md                    # This file
├── requirements.txt             # Python dependencies
├── Makefile                     # Build commands
│
├── Experiments/
│   └── chatgpt_micro_cap/       # Main experiment
│       │
│       ├── trading_script.py    # Core trading engine
│       │
│       ├── graphing/            # Visualization scripts
│       │   ├── daily_returns.py
│       │   ├── drawdown.py
│       │   └── ...
│       │
│       ├── csv_files/           # Trading data
│       │   ├── Daily_Updates.csv
│       │   └── Trade_Log.csv
│       │
│       ├── evaluation/          # Research paper
│       │   ├── evaluation_report.md
│       │   └── paper.pdf
│       │
│       ├── collected_artifacts/ # Decision logs
│       │   ├── deep_research_index.md
│       │   ├── chats.md
│       │   └── Weekly_Deep_Research_MD/
│       │
│       ├── images/              # Generated charts
│       │   ├── equity_vs_baseline.png
│       │   └── ...
│       │
│       ├── metrics/             # Performance analytics
│       │   ├── load_dataV3.py
│       │   └── episode_pcr.py
│       │
│       └── processing/          # Data processing
│           └── ProcessPortfolio.py
│
└── Other/
    └── CONTRIBUTING.md
```

---

## 🔬 Running Your Own Experiment

Want to run your own AI trading experiment? Use the **LLM Investor Behavior Benchmark (LIBB)** framework:

👉 [LLM Investor Behavior Benchmark](https://github.com/LuckyOne7777/LLM-Investor-Behavior-Benchmark)

### Steps to Create Your Experiment

1. **Fork this repository**
2. **Modify trading rules** in `trading_script.py`
3. **Configure your LLM** (ChatGPT, Claude, etc.)
4. **Set up data sources** (yfinance, Alpha Vantage, etc.)
5. **Run the experiment** with real or paper money
6. **Log everything** for transparency
7. **Analyze results** using provided metrics

### Example Experiment Ideas

- **Different Asset Classes**: Crypto, forex, commodities
- **Different LLMs**: Claude, GPT-4, Gemini comparison
- **Different Strategies**: Value investing, momentum, mean reversion
- **Different Constraints**: Higher capital, more positions, different stop-loss
- **Different Markets**: International stocks, IPOs, options

---

## ✨ Features

### Trading Engine
- ✅ **LLM-Driven Decisions**: ChatGPT analyzes and selects stocks
- ✅ **Hard Constraints**: Position limits, stop-loss, capital allocation
- ✅ **Real-Time Data**: yfinance + Stooq fallback
- ✅ **Automated Execution**: Daily trading loop
- ✅ **Decision Logging**: Every chat and trade recorded

### Analytics
- ✅ **Performance Metrics**: Sharpe, Sortino, CAPM, drawdown
- ✅ **Benchmark Comparison**: S&P 500, Russell 2000
- ✅ **Visualization**: Equity curves, returns, drawdowns
- ✅ **CSV Export**: Daily updates and trade logs
- ✅ **Research Paper**: 40-page evaluation

### Transparency
- ✅ **Forward-Only**: No backtesting, no cherry-picking
- ✅ **Full Logs**: Every decision documented
- ✅ **Open Source**: All code available
- ✅ **Reproducible**: Clear methodology

---

## 🛠️ Tech Stack

**Core:**
- Python 3.11+
- pandas (data manipulation)
- yfinance (market data)
- openai (ChatGPT API)

**Visualization:**
- matplotlib
- seaborn

**Data Sources:**
- Yahoo Finance (primary)
- Stooq (fallback)

**Analysis:**
- NumPy
- SciPy
- statsmodels

---

## 🎨 Usage Examples

### Generate Performance Charts

```bash
# Generate all charts
make graphs

# Or run individually
cd Experiments/chatgpt_micro_cap/graphing
python daily_returns.py
python drawdown.py
python equity_vs_baseline.py
```

### Analyze Trade Performance

```bash
cd Experiments/chatgpt_micro_cap/metrics
python load_dataV3.py
python episode_pcr.py
```

### View Trading Data

```bash
# View daily updates
cat Experiments/chatgpt_micro_cap/csv_files/Daily_Updates.csv

# View trade log
cat Experiments/chatgpt_micro_cap/csv_files/Trade_Log.csv
```

---

## 🐛 Troubleshooting

### Common Issues

#### 1. "yfinance data not available"

**Solution:**
- Check internet connection
- Verify ticker symbols are correct
- Try Stooq fallback data source

#### 2. "OpenAI API error"

**Solution:**
- Check API key in `.env` file
- Verify API key is active
- Check OpenAI account has credits

#### 3. "Module not found"

**Solution:**
```bash
pip install -r requirements.txt
```

#### 4. "Permission denied"

**Solution:**
```bash
chmod +x trading_script.py
```

---

## 🤝 Contributing

Contributions are welcome! This is a research project, so quality matters.

### How to Contribute

1. **Fork the repository**
2. **Create a feature branch** (`git checkout -b feature/amazing-feature`)
3. **Commit your changes** (`git commit -m 'Add amazing feature'`)
4. **Push to the branch** (`git push origin feature/amazing-feature`)
5. **Open a Pull Request**

### Contribution Areas

- 🐛 **Bug fixes**: Edge cases, data issues
- 📊 **New metrics**: Additional performance analytics
- 🎨 **Visualizations**: Better charts and graphs
- 📝 **Documentation**: Improve clarity
- 🔬 **New experiments**: Different strategies or assets

**Contributing Guide**: [CONTRIBUTING.md](Other/CONTRIBUTING.md)

---

## 📚 Why This Matters

AI is being marketed as a replacement for human decision-making across industries.

**Trading is the perfect test case:**
- ✅ Mistakes are measurable
- ✅ Results are irreversible
- ✅ Costs are real
- ✅ Performance is objective

This project tests those claims with:
- Forward-only decisions (no backtesting)
- Full transparency (all logs public)
- Real constraints (actual trading rules)

---

## 🔮 Future Work

### Upcoming Experiments

1. **IPO Trading**: Monthly analysis of newly listed stocks
2. **Multi-LLM Comparison**: ChatGPT vs Claude vs Gemini
3. **Crypto Trading**: Bitcoin and altcoin portfolio
4. **Options Trading**: Covered calls and protective puts

**Follow the research**: [Substack](https://nathanbsmith729.substack.com/)

### Framework Development

Building a general experimental framework: [LIBB](https://github.com/LuckyOne7777/LLM-Investor-Behavior-Benchmark)

---

## 📄 License

This project is open source. See [LICENSE](LICENSE) for details.

---

## 💬 Contact & Community

**Author**: Nathan Smith

**Links**:
- 📧 Email: [See profile]
- 🐦 Twitter: [See profile]
- 📝 Substack: [nathanbsmith729.substack.com](https://nathanbsmith729.substack.com/)
- 💼 LinkedIn: [See profile]

**Issues**: [GitHub Issues](https://github.com/LuckyOne7777/LLM-Trading-Lab/issues)

---

## ⚠️ Disclaimer

**This is a research project, not financial advice.**

- Trading involves risk of loss
- Past performance does not guarantee future results
- This is for educational purposes only
- Always do your own research
- Never invest more than you can afford to lose

**The author is not a financial advisor and this is not investment advice.**

---

## 🙏 Acknowledgments

- OpenAI for ChatGPT API
- Yahoo Finance for market data
- The open source community

---

**Made with 🤖 and 📊 by Nathan Smith**

*Exploring the intersection of AI and finance, one trade at a time.*
