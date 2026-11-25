"""数据黑名单：Dumper 固定不了的导出扰动，在切分时沿用上次的值，避免页面无意义地反复更新。

规则在 config/data_unstable.json，按导出文件分：
- keep：列出记录的指定字段（点号表示嵌套）沿用记下的值，记下时没有的字段保持没有；
- keepFile：整份文件沿用记下的内容；
- keepOrder：记录沿用记下的顺序，新增、删除和改过内容的记录照常生效（排在原来前一条之后）；
- sort：列出记录里的列表按 JSON 文本排序（只有顺序会变的内容，不需要记值）。

记下的值放在状态目录（split_all.py 用 source_state/<模组>/<版本>/stable/）：第一次切分某个版本时记录，之后沿用。
想接受某份文件的新值，删掉它的状态文件。用线上页面对应的那份导出初始化：

    python data_stability.py seed <dumps 目录> <版本>
"""

import json
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / 'config' / 'data_unstable.json'
STATE_ROOT = Path(__file__).resolve().parent / 'source_state' / 'GTNH'


def load_rules(config=CONFIG) -> list:
    config = Path(config)
    if not config.is_file():
        return []
    return json.loads(config.read_text('utf-8'))['rules']


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _record_id(record, key) -> str:
    value = record.get(key)
    return str(value) if isinstance(value, (str, int)) else _canonical(value)


def _parent(record, path, create=False):
    *parents, last = path.split('.')
    for part in parents:
        child = record.get(part)
        if not isinstance(child, dict):
            if not create:
                return None, last
            child = record[part] = {}
        record = child
    return record, last


def _read_field(record, path) -> dict:
    node, last = _parent(record, path)
    return {'value': node[last]} if node is not None and last in node else {}


def _write_field(record, path, saved: dict):
    node, last = _parent(record, path, create='value' in saved)
    if node is None:
        return
    if 'value' in saved:
        node[last] = saved['value']
    else:
        node.pop(last, None)


def _selected(data, rule):
    wanted = {str(key) for key in rule['keys']}
    for record in data.get(rule['records'], []):
        rid = _record_id(record, rule['key'])
        if rid in wanted:
            yield rid, record


def _keep(data, rule, saved: dict) -> int:
    replaced = 0
    for rid, record in _selected(data, rule):
        fields = saved.setdefault(rid, {})
        for path in rule['fields']:
            current = _read_field(record, path)
            if path not in fields:
                fields[path] = current
            elif fields[path] != current:
                _write_field(record, path, fields[path])
                replaced += 1
    return replaced


def _keep_order(data, rule, saved: list) -> int:
    records = data.get(rule['records'], [])
    ids = [_canonical(record) for record in records]
    if saved:
        positions = {}
        for position, rid in enumerate(saved):
            positions.setdefault(rid, []).append(position)
        keys, previous = [], -1
        for index, rid in enumerate(ids):
            if positions.get(rid):
                previous = positions[rid].pop(0)
                keys.append((previous, 0, index))
            else:
                keys.append((previous, 1, index))
        order = sorted(range(len(records)), key=keys.__getitem__)
    else:
        order = list(range(len(records)))
    moved = sum(position != index for position, index in enumerate(order))
    data[rule['records']] = [records[index] for index in order]
    saved[:] = [ids[index] for index in order]
    return moved


def _sort(data, rule) -> int:
    changed = 0
    for _, record in _selected(data, rule):
        for path in rule['fields']:
            node, last = _parent(record, path)
            if node is None or not isinstance(node.get(last), list):
                continue
            ordered = sorted(node[last], key=_canonical)
            changed += ordered != node[last]
            node[last] = ordered
    return changed


def apply(relative: str, data, state_dir, rules=None):
    """按规则处理一份导出文件的内容（relative 是相对 dumps 目录的路径），返回处理后的内容。"""
    relative = relative.replace('\\', '/')
    rules = [rule for rule in (load_rules() if rules is None else rules) if rule['file'] == relative]
    if not rules:
        return data
    state_file = Path(state_dir) / (relative.replace('/', '__'))
    state = json.loads(state_file.read_text('utf-8')) if state_file.is_file() else {}
    before = _canonical(state)
    count = 0
    for rule in rules:
        mode = rule['mode']
        slot = mode + ':' + ','.join(rule.get('fields', []))
        if mode == 'keepFile':
            if slot not in state:
                state[slot] = data
            elif state[slot] != data:
                data = state[slot]
                count += 1
        elif mode == 'keep':
            count += _keep(data, rule, state.setdefault(slot, {}))
        elif mode == 'keepOrder':
            count += _keep_order(data, rule, state.setdefault(slot, []))
        elif mode == 'sort':
            count += _sort(data, rule)
        else:
            raise ValueError(f'数据黑名单：未知的规则类型 {mode}')
    if state and _canonical(state) != before:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(_canonical(state) + '\n', 'utf-8')
    if count:
        print(f'  数据黑名单: {relative} 处理 {count} 处')
    return data


def seed(dumps, version: str, rules=None, state_root=STATE_ROOT) -> list:
    """丢掉记下的值，改从 dumps 这份导出记录。返回记录到的文件。"""
    rules = load_rules() if rules is None else rules
    state_dir = Path(state_root) / version / 'stable'
    recorded = []
    for relative in sorted({rule['file'] for rule in rules if rule['mode'] != 'sort'}):
        source = Path(dumps) / relative
        (state_dir / relative.replace('/', '__')).unlink(missing_ok=True)
        if not source.is_file():
            print(f'  没有 {relative}，跳过')
            continue
        apply(relative, json.loads(source.read_text('utf-8')), state_dir, rules)
        recorded.append(relative)
    return recorded


def main(argv):
    if len(argv) == 3 and argv[0] == 'seed':
        print(seed(argv[1], argv[2]))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == '__main__':
    main(sys.argv[1:])
