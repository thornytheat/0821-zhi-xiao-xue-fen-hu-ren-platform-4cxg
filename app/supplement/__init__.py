"""补录签发领域包。规范服务实现见 :mod:`app.supplement.service`。"""
from app.supplement.service import *  # noqa: F401,F403
from app.supplement import service  # noqa: F401

__all__ = [  # noqa: F405
    'ISSUED', 'ISSUABLE', 'RECALCULATE', 'SHORTFALL',
    'application_summary', 'evaluate_supplement', 'get_supplement',
    'issue', 'issue_batch', 'issue_supplement', 'list_supplements',
    'plan_supplement', 'recalculate', 'replan', 'recompute',
    'submit_supplement', 'create_supplement', 'execute_supplement',
    'claim_supplement', 'claim_pairs', 'get_batch', 'list_batches',
]
