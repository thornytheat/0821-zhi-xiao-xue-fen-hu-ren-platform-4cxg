"""兼容别名：补录签发规范实现见 app.supplement.service。"""
from app.supplement.service import *  # noqa: F401,F403
from app.supplement.service import (  # noqa: F401
    application_summary, evaluate_supplement, get_supplement, issue_pairs,
    issue_supplement, list_supplements, plan_supplement, recalculate,
)
