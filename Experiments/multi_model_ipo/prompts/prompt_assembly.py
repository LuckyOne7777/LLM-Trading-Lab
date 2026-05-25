from ..prompt_orchestration.main_functions import *
from ..prompts.deep_research_prompt import *
from ..prompts.daily_prompt import *



def create_deep_research_prompt(skeleton: str, libb: LIBBmodel):


    today = libb.run_date

    portfolio_state = libb.portfolio
    portfolio_tickers_eligibility = build_eligibility_series(portfolio_state["ticker"])
    execution_log = libb.recent_execution_logs()

    if execution_log.empty:
        execution_log = "No recent trade logs."

    prompt = skeleton.format(
        today=today,
        PORTFOLIO_STATE=portfolio_state,
        PORTFOLIO_TICKER_ELIGIBILITY=portfolio_tickers_eligibility,
        TRADE_EXECUTION_LOG=execution_log
        )

    return prompt

def create_daily_prompt(skeleton: str, libb: LIBBmodel): 


    today = libb.run_date
    portfolio_state = libb.portfolio
    portfolio_eligibility = build_eligibility_series(portfolio_state["ticker"])
    execution_log = libb.recent_execution_logs()

    if execution_log.empty:
        execution_log = "No recent trade logs."

    prompt = skeleton.format(
            today=today,
            PORTFOLIO_STATE=portfolio_state,
            TRADE_EXECUTION_LOG=execution_log,
            PORTFOLIO_TICKER_ELIGIBILITY=portfolio_eligibility)

    return prompt
