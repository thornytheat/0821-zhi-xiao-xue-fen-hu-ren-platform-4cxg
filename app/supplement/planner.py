"""补录批次的互认分配。

与 ``app.modules.calculate`` 使用同一套最大权重匹配规则：
先最大化学分，再最大化审批等级之和，最后按配对路径稳定择一。
区别是补录时已经签发的外校课/校内课被锁定，不参与重新分配，
因此历史配对不会被整份重算改掉。
"""


def best_allocation(sources, targets, edges):
    """返回 ``(总学分, 等级和, 配对明细)``。

    ``sources``/``targets`` 为 ``{课程键: 学分上限}``，
    ``edges`` 为 ``{(外校课, 校内课): (学分, 优先级)}``。
    """
    bits = {key: 1 << index for index, key in enumerate(sorted(targets))}
    frontier = {0: (0, 0, ())}
    for source in sorted(sources):
        updated = dict(frontier)
        for (left, target), (credits, priority) in edges.items():
            if left != source:
                continue
            bit = bits.get(target)
            if bit is None:
                continue
            for mask, (total, rank, path) in frontier.items():
                if mask & bit:
                    continue
                next_mask = mask | bit
                proposal = (total + credits, rank + priority, (*path, (source, target)))
                current = updated.get(next_mask)
                if current is None or (-proposal[0], -proposal[1], proposal[2]) < (
                    -current[0],
                    -current[1],
                    current[2],
                ):
                    updated[next_mask] = proposal
        frontier = updated
    if not frontier:
        return 0, 0, []
    total, rank, path = min(frontier.values(), key=lambda item: (-item[0], -item[1], item[2]))
    pairs = [
        {
            'external_course': source,
            'local_course': target,
            'external': source,
            'local': target,
            'source': source,
            'target': target,
            'ext': source,
            'credits': edges[(source, target)][0],
            'credit': edges[(source, target)][0],
            'priority': edges[(source, target)][1],
        }
        for source, target in path
    ]
    return total, rank, pairs


def plan_residual(payload, locked_external=(), locked_local=()):
    """在排除已签发课程后求本批最优配对。

    返回结构化结论，包含本批学分、最低学分、配对、碰撞记录和证据。
    同时给出不考虑锁定时的“原方案”，用于判断是否发生撞课。
    """
    sources, targets, edges, floor = payload
    used_external = {_normalize_course(key) for key in locked_external}
    used_local = {_normalize_course(key) for key in locked_local}

    # 不考虑跨批次占用的原方案（与 calculate 同一最优规则）
    original_value, _, original_pairs = best_allocation(sources, targets, edges)
    original_collision = any(
        _normalize_course(pair['external_course']) in used_external
        or _normalize_course(pair['local_course']) in used_local
        for pair in original_pairs
    )

    collisions = []
    for source in sources:
        if _normalize_course(source) in used_external:
            collisions.append({'course': source, 'kind': 'external_course', 'reason': 'COURSE_CLAIMED'})
    for target in targets:
        if _normalize_course(target) in used_local:
            collisions.append({'course': target, 'kind': 'local_course', 'reason': 'COURSE_CLAIMED'})

    free_sources = {s: credits for s, credits in sources.items()
                    if _normalize_course(s) not in used_external}
    free_targets = {t: credits for t, credits in targets.items()
                    if _normalize_course(t) not in used_local}
    free_edges = {}
    for (source, target), value in edges.items():
        if _normalize_course(source) in used_external or _normalize_course(target) in used_local:
            continue
        free_edges[source, target] = value

    total, rank, pairs = best_allocation(free_sources, free_targets, free_edges)
    assigned = {pair['external_course']: pair['local_course'] for pair in pairs}
    unmet = sorted(set(free_targets) - set(assigned.values()))
    evidence = {
        'matched': {str(source): target for source, target in assigned.items()},
        'unmet': unmet,
    }
    feasible = total >= floor
    return {
        'value': total,
        'expected': floor,
        'unit': '学分',
        'rank': rank,
        'pairs': pairs,
        'evidence': evidence,
        'unmet': unmet,
        'feasible': feasible,
        'collisions': collisions,
        'collided': original_collision,
        'requires_recalculation': original_collision and not feasible,
        'original_value': original_value,
        'original_pairs': original_pairs,
        'switched': original_collision and feasible and pairs != original_pairs,
        'locked': {
            'external_course': sorted(used_external),
            'local_course': sorted(used_local),
        },
        'shortfall': max(0, floor - total),
    }


def _normalize_course(value):
    """课程键跨批次按字符串归一；CSV 解析后课程键可能是 int。"""
    if isinstance(value, str):
        text = value.strip()
        # 与 intake.numeric 的全角/千分位归一保持一致的轻量处理
        try:
            return str(int(text))
        except ValueError:
            return text
    return str(value)
