"""Version 2 — demand forecasting.

Pipeline (see docs/ml-pipeline.md):

    stock_movements ──► data.load_panel()        daily consumption per (item, department), censored stockout days
                   ──► features.feature_frame()  lag_1/7/14, rolling means/std, trend, day of week, item, department
                   ──► baselines / xgb_model     candidates trained on the same window
                   ──► metrics                   holdout MAE / RMSE / WAPE / bias, per item and overall
                   ──► pipeline.run_training()   select best by WAPE, refit on all data, store forecasts + registry

Everything here is pure numpy/pandas/xgboost except data.py and pipeline.py, which touch the database.
"""
