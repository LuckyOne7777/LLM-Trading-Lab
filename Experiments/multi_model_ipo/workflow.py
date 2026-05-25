from libb import LIBBmodel
from .prompt_orchestration.prompt_models import *
from multi_model_ipo.miscellaneous.order_verification import *
from multi_model_ipo.miscellaneous.csv_conversion import *
from libb.other.parse import parse_json
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

# TODO: yfinance network failures in codespace environment causing IPO date + shares outstanding lookups to fail
# TODO: Polygon fallback also failing for very recent IPOs (PAYP, MMED, NHP) — tickers not resolving
# TODO: LIMIT orders missing limit_price from model output causing market_cap = 0, triggering false rejections
# TODO: filter_orders incorrectly rejecting BUY_ALLOWED tickers due to above lookup failures

MODELS = ["deepseek", "gpt-4.1"]

TODAY = pd.Timestamp.now().date()

STARTING_DATE = pd.Timestamp("2026-05-20")

def weekly_flow(date):

    prompt_skeleton = assemble_deep_research_prompt_skeleton()

    for model in MODELS:
        libb = LIBBmodel(f"Experiments/multi_model_ipo/artifacts/{model}", run_date=date)
        libb.process_portfolio()
        deep_research_report, prompt = prompt_deep_research(libb, prompt_skeleton)
        libb.save_prompt(prompt)
        libb.save_deep_research(deep_research_report)

        orders_json = parse_json(deep_research_report, "ORDERS_JSON")

        filtered_orders, rejected_orders = filter_orders(orders_json)
        if rejected_orders:
            save_rejections(libb, rejected_orders)

        libb.save_orders(filtered_orders)
        libb.analyze_sentiment(deep_research_report, report_type="deep_research_report")
    return


def daily_flow(date):

    prompt_skeleton = assemble_daily_prompt_skeleton()

    for model in MODELS:
        libb = LIBBmodel(f"Experiments/multi_model_ipo/artifacts/{model}", run_date=date)
        libb.process_portfolio()
        daily_report, prompt = prompt_daily_report(libb, prompt_skeleton)
        libb.save_prompt(prompt)
        libb.analyze_sentiment(daily_report, report_type="daily_report")
        libb.save_daily_update(daily_report)

        orders_json = parse_json(daily_report, "ORDERS_JSON")

        filtered_orders, rejected_orders = filter_orders(orders_json)
        if rejected_orders:
            save_rejections(libb, rejected_orders)

        libb.save_orders(filtered_orders)
    return

def starting_flow(date):

    prompt = create_starting_prompt()

    for model in MODELS:
        libb = LIBBmodel(f"Experiments/multi_model_ipo/artifacts/{model}", run_date=date)
        libb.process_portfolio()
        starting_report, prompt = prompt_starting_report(prompt, libb)
        libb.save_prompt(prompt)
        libb.analyze_sentiment(starting_report, report_type="starting_report")
        libb.save_deep_research(starting_report)

        orders_json = parse_json(starting_report, "ORDERS_JSON")

        filtered_orders, rejected_orders = filter_orders(orders_json)
        if rejected_orders:
            save_rejections(libb, rejected_orders)

        libb.save_orders(filtered_orders)
    return

def main():
    day_num = TODAY.weekday()

    if STARTING_DATE == TODAY:
        print("Starting Date: Running Starting Work Flow...")
        starting_flow(TODAY)

    elif day_num  == 4: # Friday
        print("Friday: Running Weekly Flow...")
        weekly_flow(TODAY)
    else:
        print("Regular Weekday: Running Daily Flow...")
        daily_flow(TODAY) # Mon-Thursday (Non trading days will be logged)
    print("Success!")

def testing_main():
    # TODO: REPLACE FILLER DATE IN PROMPTS
    # TODO: ADD REAL DATE
    for i in range(5):
        run_date =  STARTING_DATE + pd.Timedelta(days=i)    
        day_num = run_date.weekday()

        if run_date == STARTING_DATE:
            print("Starting Date: Running Starting Work Flow...")
            starting_flow(TODAY)

        elif day_num  == 4: # Friday
            print("Friday: Running Weekly Flow...")
            weekly_flow(run_date)

        elif day_num < 4:
            print("Regular Weekday: Running Daily Flow...")
            daily_flow(run_date) # Mon-Thursday
            
        else:  # Weekend (optional, LIBB will automatically skip weekends)
            print("Weekend: Skipping...")
        print("Success!")

if __name__ == "__main__":
    testing_main()