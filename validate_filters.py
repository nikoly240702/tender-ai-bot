import yaml, re, sys
from collections import defaultdict, Counter
d = yaml.safe_load(open('filters_v2.yaml'))
errs, warns = [], []
presets = d['region_presets']; expresets = d['exclusion_presets']
never = set(d['regions_never'])
slugs = set()
kw_owner = defaultdict(list)
for f in d['filters']:
    s = f['slug']
    if s in slugs: errs.append(f'дубль slug: {s}')
    slugs.add(s)
    if f.get('regions_preset') not in presets: errs.append(f'{s}: неизвестный пресет {f.get("regions_preset")}')
    pm = f.get('price_max', d['defaults']['price_max'])
    if pm > 3000000: errs.append(f'{s}: потолок {pm} > 3 млн')
    for p in f.get('exclusion_presets', []):
        if p not in expresets: errs.append(f'{s}: неизвестный пресет исключений {p}')
    ex = set(f.get('exclusions') or []) | {e for p in f.get('exclusion_presets',[]) for e in expresets[p]}
    for k in f['keywords']:
        if k != k.lower(): errs.append(f'{s}: ключевик не в нижнем регистре: {k}')
        if 'ё' in k: errs.append(f'{s}: ё в ключевике: {k}')
        if '/' in k: errs.append(f'{s}: слэш в ключевике: {k}')
        if len(k) < 3: errs.append(f'{s}: слишком короткий ключевик: {k}')
        for e in ex:
            if e == k: errs.append(f'{s}: ключевик и исключение совпадают: {k}')
            elif e in k: warns.append(f'{s}: ключевик «{k}» содержит исключение «{e}» — спасает правило keyword_wins_over_exclusion')
        kw_owner[k].append(s)
    for e in (f.get('exclusions') or []):
        if 'ё' in e: errs.append(f'{s}: ё в исключении: {e}')
# ё в пресетах исключений
for p, lst in expresets.items():
    for e in lst:
        if 'ё' in e: errs.append(f'пресет {p}: ё в «{e}»')
dups = {k: v for k, v in kw_owner.items() if len(v) > 1}
for k, v in sorted(dups.items()):
    warns.append(f'ключевик «{k}» в {len(v)} фильтрах: {", ".join(v)}')
# регионы из чёрного списка
for name, lst in presets.items():
    for r in lst:
        if r in never: errs.append(f'пресет {name}: запрещённый регион {r}')
# миграция
mig = d['migration_map']
for old, news in mig.items():
    for n in news:
        if n not in slugs: errs.append(f'migration_map {old}: нет slug {n}')
print(f'=== ОШИБКИ: {len(errs)}')
for e in errs: print(' !', e)
print(f'=== ПЕРЕСЕЧЕНИЯ КЛЮЧЕВИКОВ: {len(warns)}')
for w in warns: print(' ~', w)
print('=== СВОДКА')
print('фильтров:', len(d['filters']), '| ключевиков:', sum(len(f['keywords']) for f in d['filters']), '| уникальных:', len(kw_owner))
