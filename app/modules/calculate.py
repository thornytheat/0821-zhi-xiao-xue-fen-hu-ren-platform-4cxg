from app.business import measured


def compute(data, *args, **kwargs):
    """最大权重匹配。

    无额外参数时与原有逐组计算完全一致。也接受跨批次锁定信息：

    - ``compute(payload, claims)``：claims 为配对字典/元组的可迭代对象；
    - ``compute(payload, locked_external, locked_local)``；
    - 关键字 ``claims`` / ``used`` / ``locked_external`` / ``locked_local``。
    """
    claims = kwargs.pop('claims', kwargs.pop('used', kwargs.pop('occupied', None)))
    locked_external = kwargs.pop('locked_external', kwargs.pop('used_external', kwargs.pop('occupied_external', ())))
    locked_local = kwargs.pop('locked_local', kwargs.pop('used_local', kwargs.pop('occupied_local', ())))
    if args:
        if len(args) == 1:
            claims = claims if claims is not None else args[0]
        else:
            locked_external, locked_local = args[0], args[1]
    if claims is not None and not locked_external and not locked_local:
        locked_external, locked_local = _locks_from_claims(claims)
    if locked_external or locked_local:
        return _compute_residual(data, locked_external, locked_local)
    return _compute_full(data)


def _locks_from_claims(claims):
    external, local = [], []
    for claim in claims or ():
        if isinstance(claim, dict):
            ext = claim.get('external_course', claim.get('external', claim.get('source')))
            loc = claim.get('local_course', claim.get('local', claim.get('target')))
        else:
            # (external, local[, credits...]) 或 ((external, local), credits)
            first = claim[0]
            if isinstance(first, (tuple, list)):
                ext, loc = first[0], first[1]
            else:
                ext, loc = claim[0], claim[1]
        external.append(ext)
        local.append(loc)
    return external, local


def _compute_residual(data, locked_external, locked_local):
    from app.supplement.planner import plan_residual
    planned = plan_residual(data, locked_external, locked_local)
    result = measured(planned['value'], planned['expected'], '学分', planned['evidence'])
    result['pairs'] = [
        {k: v for k, v in pair.items() if k in ('external_course', 'local_course', 'credits', 'priority')}
        for pair in planned['pairs']
    ]
    result['feasible'] = planned['feasible']
    result['shortfall'] = planned['shortfall']
    result['collisions'] = planned['collisions']
    return result


def _compute_full(data):
    sources, targets, edges, floor = data
    bits = {key:1 << index for index,key in enumerate(sorted(targets))}
    frontier = {0:(0,0,())}
    for source in sorted(sources):
        updated = dict(frontier)
        for (left,target),(credits,priority) in edges.items():
            if left != source:
                continue
            for mask,(total,rank,path) in frontier.items():
                if mask & bits[target]:
                    continue
                next_mask = mask | bits[target]
                proposal = (total+credits,rank+priority,(*path,(source,target)))
                current = updated.get(next_mask)
                if current is None or (-proposal[0],-proposal[1],proposal[2]) < (-current[0],-current[1],current[2]):
                    updated[next_mask] = proposal
        frontier = updated
    total, rank, path = min(frontier.values(),key=lambda x:(-x[0],-x[1],x[2]))
    assigned = dict(path)
    result = measured(total,floor,'学分',{'matched':{str(k):v for k,v in path},'unmet':sorted(targets.keys()-set(assigned.values()))})
    # 结构化配对（含每对学分/优先级），供签发时落 claims；不改变 evidence 的既有形状。
    result['pairs'] = [
        dict(external_course=source, local_course=target,
             credits=edges[(source, target)][0], priority=edges[(source, target)][1])
        for source, target in path
    ]
    return result


calculate = compute
match_courses = compute
allocate = compute
