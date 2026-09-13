#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 JSON 清单中读取文件路径，逐个删除。

用法：
  python delete_from_json.py delete_list_xxx.json          # dry-run，只列不删
  python delete_from_json.py delete_list_xxx.json --yes    # 实际删除（无二次确认）
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime


def fmt_size(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def load_list(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    items = []
    if isinstance(data, dict):
        for key in ('files', 'list', 'items'):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            if 'path' in data or 'name' in data:
                data = [data]
            else:
                for v in data.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict) and 'path' in v[0]:
                        data = v
                        break

    if not isinstance(data, list):
        sys.exit(f"错误：无法识别 JSON 格式，顶层是 {type(data).__name__}")

    for entry in data:
        if isinstance(entry, dict):
            p = entry.get('path', '')
            size = int(entry.get('size', 0))
            group = entry.get('group_id', '')
        else:
            p = str(entry)
            size = 0
            group = ''
        if p:
            items.append({'path': p, 'size': size, 'group': group})

    return items


def main():
    parser = argparse.ArgumentParser(description='从 JSON 清单批量删除文件')
    parser.add_argument('json', help='JSON 删除清单')
    parser.add_argument('--yes', '-y', action='store_true', help='实际删除（否则 dry-run）')
    parser.add_argument('--dedup', action='store_true', default=True, help='按路径去重（默认开启）')
    parser.add_argument('--no-dedup', action='store_false', dest='dedup', help='不去重（保留所有重复条目）')
    parser.add_argument('--missing-log', default='missing_files.log', help='记录不存在文件的日志')
    args = parser.parse_args()

    if not os.path.exists(args.json):
        sys.exit(f"找不到：{args.json}")

    items = load_list(args.json)
    if not items:
        sys.exit("清单为空")

    if args.dedup:
        seen = set()
        unique = []
        dup_count = 0
        for it in items:
            if it['path'] in seen:
                dup_count += 1
            else:
                seen.add(it['path'])
                unique.append(it)
        if dup_count:
            print(f"[!] 发现 {dup_count} 条重复路径，已自动去重为 {len(unique)} 条")
            items = unique

    existed = [x for x in items if os.path.exists(x['path'])]
    missing = [x for x in items if not os.path.exists(x['path'])]
    total_bytes = sum(x['size'] for x in existed)
    miss_bytes = sum(x['size'] for x in missing)

    print(f"{'='*60}")
    print(f"清单文件 : {args.json}")
    print(f"清单总数 : {len(items)} 个")
    print(f"存在文件 : {len(existed)} 个  可释放 {fmt_size(total_bytes)}")
    print(f"不存在   : {len(missing)} 个  曾占 {fmt_size(miss_bytes)}")
    print(f"模式     : {'实际删除 ✂️' if args.yes else 'DRY-RUN 预览（加 --yes 才会真删）'}")
    print(f"{'='*60}")

    if missing:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(args.missing_log, 'a', encoding='utf-8') as f:
            f.write(f"\n# {ts}  {args.json}\n")
            for m in missing:
                f.write(f"{m['path']}\n")
        print(f"\n⚠️  不存在的文件已记入 {args.missing_log}")

    if not existed:
        print("\n没有可删除的文件。")
        return

    ok = fail = 0
    freed = 0
    failed_paths = []
    t0 = time.time()

    for i, item in enumerate(existed, 1):
        p = item['path']
        size = item['size']
        display = p if len(p) < 100 else p[:45] + '...' + p[-50:]

        if not args.yes:
            print(f"  [{i}/{len(existed)}] 📋 {display}")
            continue

        print(f"  [{i}/{len(existed)}] 🗑  {display}  ({fmt_size(size)})", end='')

        try:
            os.remove(p)
            if os.path.exists(p):
                raise OSError("删除后仍存在")
            freed += size
            ok += 1
            print('  ✅')
        except Exception as e:
            fail += 1
            failed_paths.append((p, str(e)))
            print(f'  ❌ {e}')

    elapsed = time.time() - t0

    if args.yes:
        print(f"\n{'='*60}")
        print(f"删除完成 成功 {ok} 个  失败 {fail} 个")
        print(f"释放空间   : {fmt_size(freed)}")
        print(f"耗时       : {elapsed:.1f}s")
        if failed_paths:
            print(f"\n失败清单（{len(failed_paths)}）:")
            for p, e in failed_paths:
                print(f"  ❌ {p}\n     {e}")
            fail_log = args.json.replace('.json', f'_fail_{datetime.now():%Y%m%d_%H%M%S}.log')
            with open(fail_log, 'w', encoding='utf-8') as f:
                for p, e in failed_paths:
                    f.write(f"{p}\t{e}\n")
            print(f"已写日志 : {fail_log}")
    else:
        print(f"\n👆 以上是预览。确认无误后加 --yes 执行删除。")


if __name__ == '__main__':
    main()