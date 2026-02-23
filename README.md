# LLM Trading Lab
This repository started as a **6-month live micro-cap trading experiment** in which a large language model (ChatGPT) manages a real-money portfolio under strict, predefined rules.

What began as a single experiment has evolved into a **baseline framework** for studying how large language models behave as portfolio decision-makers.  
All historical data, research artifacts, and logs are preserved for transparency and auditability.

**Full research evaluation out now: [Evaluating ChatGPT as a Portfolio Decision-Maker in Micro-Cap Equities](Experiments/chatgpt_micro-cap/evaluation/paper.pdf)**

---

## Running Your Own Experiment

If you want to run your own AI-managed trading experiment, check out this framework I created for LLM research: [LLM Investor Behavior Benchmark - LIBB](https://github.com/LuckyOne7777/LLM-Investor-Behavior-Benchmark)

## Repository Purpose

This repository serves two primary purposes:

1. A **complete, forward-only record** of a live AI-managed trading experiment  
2. A **reusable foundation** for future AI-driven trading experiments built on the same structure

Historical artifacts remain unchanged. New experiments, analyses, and methodologies are layered on top without rewriting past results.

---

```text
ChatGPT-Micro-Cap-Experiment/
│
├─ README.md
├─ requirements.txt
├─ Makefile
│
├─ experiments/
│  └─ chatgpt_micro_cap/
│     │
│     ├─ trading_script.py
│     │
|     ├─ graphing/
|     │  ├─ daily_returns.py
|     │  ├─ drawdown.py
|     │  └─ ...
|     │ 
│     ├─ csv_files/
│     │  ├─ Daily_Updates.csv
│     │  └─ Trade_Log.csv
│     │
│     ├─ evaluation/
│     │  ├─ evaluation_report.md
│     │  └─ paper.pdf
│     │
│     ├─ collected_artifacts/
│     │  ├─ deep_research_index.md
│     │  ├─ chats.md
│     │  │
│     │  ├─ Weekly_Deep_Research_MD/
│     │  │  ├─ Week_01_Summary.md
│     │  │  ├─ Week_02_Summary.md
│     │  │  └─ ...
│     │  │
│     │  └─ Weekly_Deep_Research_PDF/
│     │     ├─ Starting_Research.pdf
│     │     ├─ Week_01.pdf
│     │     ├─ Week_02.pdf
│     │     └─ ...
│     │
│     ├─ images/
│     │  ├─ equity_vs_baseline.png
│     │  ├─ repeated_exposure.png
│     │  └─ ...
│     │
│     ├─ tables/
│     │  └─ metrics.txt
│     │
│     ├─ metrics/
│     │  ├─ load_dataV3.py
│     │  └─ episode_pcr.py
│     │
│     └─ processing/
│        ├─ ProcessPortfolio.py
|
│
├─

```
---

## The Concept

Every day, I kept seeing the same ad about having some A.I. pick undervalued stocks. It was obvious it was trying to get me to subscribe to some garbage, so I just rolled my eyes. 
Then I started wondering, "How well would that actually work?" 

So, starting with just $100, I wanted to answer a simple but powerful question: **Can powerful large language models like ChatGPT actually generate alpha (or at least make smart trading decisions) using real-time data?**

Today, this repo has evolved into so much more than simply chasing alpha.

---

## Why This Matters

AI is being aggressively marketed as a replacement for human decision-making across industries.  
Trading is a domain where mistakes are measurable, irreversible, and costly.

This platform tests those claims using:

- Forward-only decisions
- Full transparency  
- Publicly logged results

---

## Research & Documentation

Here are the artifacts links for the Micro-Cap Experiment:

- **Research Index:** [Deep Research Index](experiments/chatgpt_micro-cap/collected_artifacts/deep_research_index.md)

- **Decision Logs / Chats:** [Chats](experiments/chatgpt_micro-cap/collected_artifacts/chats.md)

---

## Features of This Repository

- 40 page PDF evaluation over results
- Live trading engine used in production  
- LLM-driven trade selection under hard constraints  
- Daily CSV-based portfolio accounting  
- Automated stop-loss enforcement  
- Benchmark comparisons (S&P 500, Russell 2000)  
- CAPM, Sharpe, Sortino, and drawdown analytics  
- Full trade and decision logs

---

## Tech Stack

- Python 3.11+  
- pandas  
- yfinance (primary data source)  
- Stooq (fallback data source)  
- Matplotlib  

---

## Future Work

I am currently designing the future experiment over newly listed IPOs with monthly analysis on my [Substack](https://nathanbsmith729.substack.com/).

Also, I developing the general experimental framework I created for LLM research [LIBB](https://github.com/LuckyOne7777/LLM-Investor-Behavior-Benchmark) for the upcoming and all future experiments.

---

## Quick Start

To get started quickly with this project:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/LuckyOne7777/LLM-Trading-Lab.git
   cd LLM-Trading-Lab
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the trading script (example):**
   ```bash
   python Experiments/chatgpt_micro-cap/scripts/processing/trading_script.py --file "Experiments/chatgpt_micro-cap/csv_files/Daily_Updates.csv"
   ```
   - You can specify your own CSV file with the `--file` argument.

4. **Explore research and results:**
   - See the `Experiments/chatgpt_micro-cap/evaluation/` folder for reports and papers.
   - Check `Experiments/chatgpt_micro-cap/collected_artifacts/` for research logs and summaries.

5. **Contribute:**
   - See [Other/CONTRIBUTING.md](Other/CONTRIBUTING.md) for how to report issues or submit pull requests.

---

## Contributing

Contributions are welcome.

- Issues: bugs, edge cases, or design critiques  
- Pull Requests: improvements, refactors, or extensions  
- Collaboration: high-quality contributors may be invited to help maintain future experiments

Contributing guide:  
https://github.com/LuckyOne7777/ChatGPT-Micro-Cap-Experiment/blob/main/Other/CONTRIBUTING.md

---

## Contact

All my links can be found on my profile, feel free to reach out anywhere!
