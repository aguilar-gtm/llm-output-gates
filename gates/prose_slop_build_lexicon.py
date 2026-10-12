"""Derive the slop lexicon from MEASURED excess, not from taste.

Source: berenslab/llm-excess-vocab (MIT), the data behind Kobak et al.,
Science Advances 2025 - 15M+ PubMed abstracts, yearly unigram counts 2010-2024.

Method: frequency ratio = share(2024) / share(2022). 2022 is the last fully
pre-ChatGPT year, so the ratio is "how much more does 2024 writing reach for
this word". Keep only words the paper's own authors tagged type=style (content
words are domain vocabulary, e.g. 'aceis', and would fire on any medical text).

The doctrine said "filter to ratio >= 5" but excess_words.csv ships no ratio
column, so the ratio is recomputed here from yearly-counts.csv.gz.
"""
import gzip, csv, io, json, os, sys

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.environ.get('TMP', '/tmp')


def find(name):
    for d in (TMP, '/tmp', HERE, os.getcwd()):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    raise SystemExit('missing ' + name)


style = {}
with io.open(find('excess.csv'), encoding='utf-8') as f:
    for row in csv.DictReader(f):
        if row['type'].startswith('style') or row['type'] == 'content/style':
            style[row['word'].strip().lower()] = row['part_of_speech']
print('style-tagged words in paper: %d' % len(style))

counts, totals = {}, {}
with gzip.open(find('yc.csv.gz'), 'rt', encoding='utf-8') as f:
    r = csv.reader(f)
    hdr = next(r)
    years = hdr[1:]
    i22, i24 = years.index('2022'), years.index('2024')
    t22 = t24 = 0
    for row in r:
        w = row[0].strip().lower()
        try:
            c22, c24 = int(row[1 + i22]), int(row[1 + i24])
        except (ValueError, IndexError):
            continue
        t22 += c22
        t24 += c24
        if w in style:
            counts[w] = (c22, c24)
    totals = (t22, t24)

t22, t24 = totals
print('corpus totals: 2022=%d 2024=%d' % (t22, t24))

lex = []
for w, (c22, c24) in counts.items():
    if c24 < 500:                      # too rare to threshold on
        continue
    s22 = (c22 / t22) if t22 else 0
    s24 = (c24 / t24) if t24 else 0
    if s22 <= 0:
        continue
    ratio = s24 / s22
    lex.append({'word': w, 'ratio': round(ratio, 2), 'pos': style[w],
                'c2022': c22, 'c2024': c24})

lex.sort(key=lambda x: -x['ratio'])
print('style words with 2024 count >=500 and a 2022 baseline: %d' % len(lex))

for cut in (2, 3, 5, 8, 12):
    print('  ratio >= %-3d -> %d words' % (cut, sum(1 for x in lex if x['ratio'] >= cut)))

print('\ntop 40 by measured ratio:')
for x in lex[:40]:
    print('  %-18s %7.2fx  (%s)' % (x['word'], x['ratio'], x['pos']))

print('\nwhere common words land (the false-positive risk):')
for w in ('across', 'analysis', 'approach', 'address', 'aims', 'announced', 'align'):
    hit = next((x for x in lex if x['word'] == w), None)
    print('  %-12s %s' % (w, ('%.2fx' % hit['ratio']) if hit else 'not in list'))

out = os.path.join(HERE, 'lexicon_full.json')
io.open(out, 'w', encoding='utf-8').write(json.dumps(lex, indent=1))
print('\nwrote ' + out)
