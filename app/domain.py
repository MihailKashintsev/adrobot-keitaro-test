from copy import deepcopy
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def redistribute(offers):
    """Integer percentages, stable remainder allocation, pins never change."""
    result = deepcopy(offers)
    active = [x for x in result if not x.get('removed')]
    if not active:
        raise ValueError('В потоке должен остаться хотя бы один активный оффер.')
    if len({x['offer_id'] for x in result}) != len(result):
        raise ValueError('Оффер уже есть в потоке.')
    pinned = [x for x in active if x.get('pinned')]
    if any(type(x['share']) is not int or not 0 <= x['share'] <= 100 for x in pinned):
        raise ValueError('Закреплённая доля должна быть целым числом от 0 до 100.')
    rest = 100 - sum(x['share'] for x in pinned)
    free = [x for x in active if not x.get('pinned')]
    if rest < 0 or (not free and rest != 0):
        raise ValueError('Закреплённые доли несовместимы с суммой 100%.')
    if free:
        q, r = divmod(rest, len(free))
        for i, x in enumerate(free):
            x['share'] = q + (i < r)
    for x in result:
        if x.get('removed'):
            x['share'] = 0
    return result


def merge_remote(remote, previous):
    """Remote is authoritative on fetch; retain removed links and local pins."""
    known = {x['offer_id']: x for x in previous}
    result = []
    for item in remote:
        oid = int(item['offer_id'])
        result.append({'offer_id': oid, 'share': int(item['share']),
                       'pinned': known.get(oid, {}).get('pinned', False),
                       'removed': item.get('state', 'active') != 'active'})
    present = {x['offer_id'] for x in result}
    for item in previous:
        if item['offer_id'] not in present:
            result.append({**item, 'removed': True, 'share': 0})
    return result
