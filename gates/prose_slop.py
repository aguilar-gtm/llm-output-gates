#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""prose_slop.py - refuse machine-slop prose before it leaves this machine.

WHY THIS EXISTS
    The house style guide has banned this vocabulary since it was written. Prose reminders
    have a measured failure rate here (2026-07-26: six written rules broken in one
    day), so the rule stops being a paragraph and becomes an exit code.

WHAT IT IS NOT
    Not an AI-text detector. Those are barred outright: 14 systems tested were
    "neither accurate nor reliable" with a bias toward calling output human
    (arXiv:2306.15666), and they flag up to ~98% of non-native-English writing as
    machine-written. the operator is that population. This tool never guesses
    authorship. It measures named, countable properties of the text.

EVIDENCE
    The lexicon is not a taste list. It is derived from berenslab/llm-excess-vocab
    (MIT), the dataset behind Kobak et al., Science Advances 2025 (15M+ PubMed
    abstracts). Ratio = share(2024) / share(2022), 2022 being the last fully
    pre-ChatGPT year. Only words the authors tagged type=style are kept, because
    content words are domain vocabulary and would fire on any technical text.
    Measured: 'delves' 46.4x, 'underscores' 13.4x, 'realm' 4.8x - while ordinary
    words fall out on their own ('analysis' 1.07x, 'approach' 1.23x).

    The published em-dash density threshold is deliberately NOT used: its human
    baseline is 8 essays with a 50x internal spread, and Llama scored below the
    human median, so that gate would pass the most obviously machine-written
    sample in the study. The threshold here is harvested from the operator's own
    approved corpus instead.

CALIBRATION (retro-validated, the slop.py method)
    Thresholds were set AFTER measuring documents already judged, never before:
      REJECTED  a client CV v1 he rejected as fluff ("Advanced mastery of",
                "Proven track record", "Expert in") - must FAIL
      APPROVED  doc-style.md, PROPOSAL.md, BUILT.md, client CV v2 - must PASS
    Run `--calibrate <dir>` to re-derive the table on any corpus.

USAGE
    python prose_slop.py FILE [FILE...]        exit 0 clean, 1 defects, 2 error
    python prose_slop.py --json FILE
    python prose_slop.py --calibrate <dir>
    python prose_slop.py --test
"""
from __future__ import unicode_literals

import io
import json
import os
import re
import sys

VERSION = '1.0.0'
HERE = os.path.dirname(os.path.abspath(__file__))
LEXICON = os.path.join(HERE, 'prose_slop_lexicon.json')

# ---------------------------------------------------------------- thresholds --
# Every number below is justified by the calibration table in --calibrate.
HARD_RATIO = 5.0      # a word this over-represented is a defect on sight
SOFT_RATIO = 3.0      # counted by density instead
SOFT_PER_1K = 4.0     # approved corpus max measured 1.90/1k -> 4.0 leaves headroom
EMDASH_PER_1K = 0.6   # approved corpus measured 0.00/1k (doc-style.md bans them)
MIN_WORDS = 60        # below this, densities are noise; only hard checks run

# The house style guide's register bans. Phrase-level business slop that a
# PubMed-derived lexicon cannot see.
REGISTER = [
    (r'\bproven track record\b', 'proven track record'),
    (r'\badvanced mastery of\b', 'advanced mastery of'),
    (r'\bworld[- ]class\b', 'world-class'),
    (r'\bcutting[- ]edge\b', 'cutting-edge'),
    (r'\bstate[- ]of[- ]the[- ]art\b', 'state-of-the-art'),
    (r'\bbest[- ]in[- ]class\b', 'best-in-class'),
    (r'\bgame[- ]?chang(er|ing)\b', 'game-changer'),
    (r'\bseamless(ly)?\b', 'seamless'),
    (r'\brobust\b', 'robust'),
    (r'\btestament to\b', 'testament to'),
    (r'\bin (?:today|the)\'?s? (?:rapidly |ever[- ])?(?:evolving|changing|digital) '
     r'(?:landscape|world|era)\b', 'in-today-s-landscape opener'),
    (r'\bit(?:\'s| is) (?:important|worth) (?:to note|noting) that\b', 'it is worth noting that'),
    (r'\bnot (?:just|only) [^,.;]{2,40}[,]? but (?:also )?\b', 'negative parallelism (not just X but Y)'),
    (r'\bunlock(?:ing|s)? (?:the )?(?:full )?potential\b', 'unlock the potential'),
    (r'\bsupercharg(?:e|es|ing)\b', 'supercharge'),
    (r'\brevolutioniz(?:e|es|ing)\b', 'revolutionize'),
    (r'\bharness(?:ing|es)? the power\b', 'harness the power'),
    (r'\bdive deep(?:er)? into\b', 'dive deep into'),
    (r'\bwhen it comes to\b', 'when it comes to'),
    (r'\bplays? a (?:crucial|vital|key|pivotal) role\b', 'plays a crucial role'),
]
REGISTER = [(re.compile(p, re.I), n) for p, n in REGISTER]

EMDASH = re.compile('[—–]')

# MENTION vs USE. A style guide names the banned words in order to ban them.
# doc-style.md, SLOP.md and BUILD-DOCTRINE.md all quote this vocabulary, and a
# gate that fires on the rulebook gets commented out within a day. A hit is
# treated as a mention (not a defect) when its own line carries a prohibition
# cue. Scoped to the line, so it cannot launder a slop paragraph.
MENTION_CUE = re.compile(
    r'\b(?:ban(?:ned|s)?|avoid|forbidden|prohibited|blocklist|blacklist|'
    r'do not use|never use|kill the|tells?|slop|lexicon|flagged|offend\w*|'
    r'over-?represented|banned by)\b', re.I)

# The other rulebook shape: a parenthesised enumeration of bare terms, e.g.
# "No hype verbs (transform, unlock, supercharge, revolutionize)". Three or
# more comma-separated short items in brackets is a list being named, not prose.
ENUM = re.compile(r'\(\s*[\w\'’-]+(?:\s+[\w\'’-]+){0,2}'
                  r'(?:\s*,\s*[\w\'’-]+(?:\s+[\w\'’-]+){0,2}){2,}\s*\)')

# One flagged word in a short paragraph is noise, not a pattern. The density
# rule needs a real cluster before it speaks.
SOFT_MIN_HITS = 3


# ------------------------------------------------------------------ scanning --
def visible_text(raw, path=''):
    """Strip everything a reader never reads.

    A check must not read blobs: a city name once matched a client name and a PNG's
    base64 matched 'orca'. Fenced code, inline code, URLs, HTML tags, YAML
    frontmatter and link targets are removed before a single word is counted.
    """
    t = raw
    if t.startswith('---'):
        end = t.find('\n---', 3)
        if 0 < end < 4000:
            t = t[end + 4:]
    t = re.sub(r'```.*?```', ' ', t, flags=re.S)
    t = re.sub(r'~~~.*?~~~', ' ', t, flags=re.S)
    t = re.sub(r'`[^`\n]*`', ' ', t)
    t = re.sub(r'^(?: {4}|\t)\S.*$', ' ', t, flags=re.M)
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', t)
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)     # keep label, drop target
    t = re.sub(r'<[^>]{1,200}>', ' ', t)
    t = re.sub(r'\b(?:https?|ftp|data|file)://\S+', ' ', t)
    t = re.sub(r'\b[\w.+-]+@[\w-]+\.[\w.]+\b', ' ', t)
    t = re.sub(r'\b[A-Za-z0-9+/]{40,}={0,2}\b', ' ', t)   # base64-ish blobs
    return t


def words_of(text):
    return re.findall(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’-]*", text)


def sentences_of(text):
    parts = re.split(r'(?<=[.!?])\s+(?=[A-ZÀ-ɏ])', text)
    return [p.strip() for p in parts if len(p.split()) >= 3]


def load_lexicon():
    if not os.path.exists(LEXICON):
        return {}
    try:
        data = json.load(io.open(LEXICON, encoding='utf-8'))
    except ValueError:
        return {}
    return {e['word']: float(e['ratio']) for e in data}


def analyze(raw, path='', lex=None, tier='deliverable'):
    lex = load_lexicon() if lex is None else lex
    text = visible_text(raw, path)
    words = words_of(text)
    n = len(words)
    per1k = (lambda c: (1000.0 * c / n) if n else 0.0)

    # Lines carrying a prohibition cue are rulebook lines: their hits are
    # mentions of the vocabulary, not uses of it.
    raw_lines = text.split('\n')
    lines, mentions = [], 0
    for ln in raw_lines:
        stripped, k = ENUM.subn(' ', ln)      # drop parenthesised ban lists
        mentions += k
        lines.append(stripped)
    mention_line = [bool(MENTION_CUE.search(ln)) for ln in lines]

    hard, soft = [], []
    for idx, ln in enumerate(lines):
        for w in words_of(ln):
            r = lex.get(w.lower())
            if r is None or r < SOFT_RATIO:
                continue
            if mention_line[idx]:
                mentions += 1
                continue
            (hard if r >= HARD_RATIO else soft).append((w.lower(), r))

    register = []
    for idx, ln in enumerate(lines):
        if mention_line[idx]:
            continue
        for rx, name in REGISTER:
            for m in rx.finditer(ln):
                register.append((name, m.group(0).strip()[:60]))

    em = len(EMDASH.findall(text))

    sents = sentences_of(text)
    lens = [len(s.split()) for s in sents]
    if len(lens) >= 5:
        mean = sum(lens) / float(len(lens))
        var = sum((x - mean) ** 2 for x in lens) / float(len(lens))
        cv = (var ** 0.5) / mean if mean else 0.0
    else:
        cv = None

    metrics = {
        'path': path,
        'tier': tier,
        'words': n,
        'mentions_skipped': mentions,
        'hard_hits': sorted(set(hard), key=lambda x: -x[1]),
        'soft_per_1k': round(per1k(len(soft)), 2),
        'soft_hits': sorted(set(soft), key=lambda x: -x[1])[:12],
        'register_hits': register,
        'emdash': em,
        'emdash_per_1k': round(per1k(em), 2),
        'sentence_len_cv': (round(cv, 3) if cv is not None else None),
    }

    defects = []
    for w, r in metrics['hard_hits']:
        defects.append('LEXICON  "%s" is %.1fx over-represented in post-LLM text' % (w, r))
    for name, hit in register:
        defects.append('REGISTER banned phrase: %s ("%s")' % (name, hit))
    # Density and punctuation are DELIVERABLE checks. Internal scaffolding
    # (routing maps, logs, notes) is exempt from them but never from the
    # lexicon or the register: slop is slop wherever it is written.
    if n >= MIN_WORDS and tier == 'deliverable':
        if len(soft) >= SOFT_MIN_HITS and metrics['soft_per_1k'] > SOFT_PER_1K:
            defects.append('DENSITY  slop-leaning vocabulary %.2f/1k exceeds %.2f/1k'
                           % (metrics['soft_per_1k'], SOFT_PER_1K))
        if metrics['emdash_per_1k'] > EMDASH_PER_1K:
            defects.append('EMDASH   %d em/en dashes (%.2f/1k, ceiling %.2f/1k)'
                           % (em, metrics['emdash_per_1k'], EMDASH_PER_1K))
    metrics['defects'] = defects
    metrics['pass'] = not defects
    return metrics


# --------------------------------------------------------------- calibration --
def calibrate(d):
    rows = []
    lex = load_lexicon()
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if not os.path.isfile(p):
            continue
        raw = io.open(p, encoding='utf-8', errors='replace').read()
        tier = 'internal' if 'INTERNAL' in f.upper() else 'deliverable'
        rows.append(analyze(raw, f, lex, tier))
    print('%-30s %-11s %6s %5s %7s %7s  %s'
          % ('doc', 'tier', 'words', 'hard', 'soft/1k', 'em/1k', 'verdict'))
    print('-' * 92)
    for m in rows:
        print('%-30s %-11s %6d %5d %7.2f %7.2f  %s'
              % (m['path'][:30], m['tier'], m['words'], len(m['hard_hits']),
                 m['soft_per_1k'], m['emdash_per_1k'],
                 'PASS' if m['pass'] else 'FAIL'))
        for dfct in m['defects'][:6]:
            print('%32s%s' % ('', dfct))
    ok = [m for m in rows if m['path'].startswith('APPROVED')]
    bad = [m for m in rows if m['path'].startswith('REJECTED')]
    print('-' * 92)
    good = all(m['pass'] for m in ok) and all(not m['pass'] for m in bad)
    print('CALIBRATION %s  (%d approved must pass, %d rejected must fail)'
          % ('OK' if good else 'BROKEN', len(ok), len(bad)))
    return 0 if good else 1


# ---------------------------------------------------------------------- test --
CLEAN = (
    'Maria ran reporting for eleven client programs and four hundred forty campaigns. '
    'That covered six hundred sixty four thousand emails to two hundred sixty two '
    'thousand prospects. It produced seven hundred thirty eight interested replies and '
    'two hundred ninety eight booked meetings. She wrote the SOPs and the QA framework. '
    'She ran the deliverability audits. Bounce rate, domain health, SPF, DKIM and DMARC '
    'were all covered. Before that she supervised twenty eight audits at an airline. '
    'She learned thirteen payment processes in two months. She built an Excel macro '
    'that cut database download time by seventy percent. The annual budget shipped in '
    'two weeks. She speaks Spanish, English, Mandarin and Cantonese. '
)
SLOPPY = (
    'This document delves into the intricate landscape of modern operations. '
    'It underscores a proven track record of world-class delivery. '
    'Our meticulously crafted approach showcases advanced mastery of the realm. '
    'It is worth noting that this is not just a report, but a testament to excellence. '
    'We harness the power of cutting-edge systems to unlock the full potential of data. '
)


def run_tests():
    lex = load_lexicon()
    if not lex:
        print('FAIL - lexicon missing at %s' % LEXICON)
        return 1
    fails = []
    ran = [0]

    def check(name, cond):
        ran[0] += 1
        if not cond:
            fails.append(name)

    m = analyze(SLOPPY, 'sloppy.md', lex)
    check('sloppy must fail', not m['pass'])
    check('sloppy must catch lexicon', len(m['hard_hits']) > 0)
    check('sloppy must catch register', len(m['register_hits']) > 0)

    m = analyze(CLEAN, 'clean.md', lex)
    check('clean must pass', m['pass'])

    # a blob must not be read: base64 and fenced code cannot create a defect
    blob = CLEAN + '\n\n```\ndelves underscores meticulously realm intricate\n```\n'
    check('fenced code is not scanned', analyze(blob, 'b.md', lex)['pass'])
    b64 = CLEAN + '\n\nZGVsdmVzIHVuZGVyc2NvcmVzIG1ldGljdWxvdXNseSByZWFsbSBpbnRyaWNhdGU=\n'
    check('base64 is not scanned', analyze(b64, 'b.md', lex)['pass'])
    url = CLEAN + '\n\nhttps://example.com/delves/underscores/meticulously/realm\n'
    check('urls are not scanned', analyze(url, 'b.md', lex)['pass'])
    code = CLEAN + '\n\nUse `realm.delves()` and `underscores`.\n'
    check('inline code is not scanned', analyze(code, 'b.md', lex)['pass'])

    # a link label IS read, its target is not
    lnk = CLEAN + '\n\n[the report](https://x.test/a)\n'
    check('link label kept, target dropped', analyze(lnk, 'b.md', lex)['pass'])

    # single banned word is enough on its own
    check('one hard word fails', not analyze(CLEAN + ' It delves further.', 'x.md', lex)['pass'])
    check('one register phrase fails',
          not analyze(CLEAN + ' A proven track record.', 'x.md', lex)['pass'])

    # ordinary vocabulary must never fire
    ordinary = (CLEAN + ' The analysis and the approach are described across the report. '
                        'It aims to address the question and aligns with the plan. ')
    check('ordinary words do not fire', analyze(ordinary, 'x.md', lex)['pass'])

    # short text: densities must not fire on noise
    check('short text skips density', analyze('An em dash — here.', 's.md', lex)['pass'])

    # em dash over the ceiling in a long doc must fire
    check('em dashes fire when dense',
          not analyze(CLEAN + (' A — B — C — D — E — F.'), 'x.md', lex)['pass'])

    # accented Spanish must survive the tokenizer
    esp = analyze('La proyección de ingresos y el análisis de desviación presupuestal. ' * 8,
                  'es.md', lex)
    check('spanish accents tokenize', esp['words'] > 50)

    # MENTION vs USE: a rulebook may name the vocabulary it bans
    rulebook = CLEAN + '\nAvoid AI vocabulary: delve, underscore, robust, seamless.\n'
    check('ban list is a mention, not a defect', analyze(rulebook, 'r.md', lex)['pass'])
    check('mention is counted', analyze(rulebook, 'r.md', lex)['mentions_skipped'] > 0)

    # ...and it must NOT become a bypass: a cue on ONE line cannot clear another
    smuggle = (CLEAN + '\nWe avoid slop here.\n'
               + 'Our meticulously crafted approach delves into the realm.\n')
    check('cue does not clear a different line', not analyze(smuggle, 's.md', lex)['pass'])
    # a cue in the same sentence as genuine use still fails on the register line
    check('register still fires off-cue-line',
          not analyze(CLEAN + '\nA proven track record of delivery.\n', 'x.md', lex)['pass'])

    # TIER: density/punctuation are deliverable-only, lexicon is universal
    dashes = CLEAN + (' A — B — C — D — E — F.')
    check('em dashes fire on deliverable', not analyze(dashes, 'x.md', lex, 'deliverable')['pass'])
    check('em dashes exempt internally', analyze(dashes, 'x.md', lex, 'internal')['pass'])
    check('lexicon still fires internally',
          not analyze(CLEAN + ' It delves further.', 'x.md', lex, 'internal')['pass'])

    # determinism
    check('deterministic', analyze(SLOPPY, 'a', lex) == analyze(SLOPPY, 'a', lex))

    if fails:
        print('FAIL - %d of %d case(s): %s' % (len(fails), ran[0], '; '.join(fails)))
        return 1
    print('PASS - prose_slop %s: %d lexicon words, %d register rules, %d cases.'
          % (VERSION, len(lex), len(REGISTER), ran[0]))
    return 0


# ---------------------------------------------------------------------- main --
def main(argv):
    if '--test' in argv:
        return run_tests()
    if '--calibrate' in argv:
        i = argv.index('--calibrate')
        return calibrate(argv[i + 1])
    as_json = '--json' in argv
    tier = 'internal' if '--internal' in argv else 'deliverable'
    files = [a for a in argv[1:] if not a.startswith('--')]
    if not files:
        print(__doc__.split('USAGE')[-1].strip())
        return 2
    lex = load_lexicon()
    worst = 0
    out = []
    for f in files:
        if not os.path.exists(f):
            sys.stderr.write('no such file: %s\n' % f)
            return 2
        raw = io.open(f, encoding='utf-8', errors='replace').read()
        m = analyze(raw, f, lex, tier)
        out.append(m)
        if not m['pass']:
            worst = 1
    if as_json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
    else:
        for m in out:
            print('%s  %s  (%d words)'
                  % ('PASS' if m['pass'] else 'FAIL', m['path'], m['words']))
            for d in m['defects']:
                print('   - %s' % d)
    return worst


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    sys.exit(main(sys.argv))
