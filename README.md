# llm-output-gates

Quality gates for LLM-written outbound copy. A draft leaves the machine only with a PASS from every gate, recorded against the exact bytes that were checked.

Most AI-SDR setups trust their own output: the model writes the email, then a prompt tells it to double-check. Here the checker and the thing it checks never share an author. Each gate is a separate process, judged by its exit code, and the send step re-hashes the file before it releases anything.

## Two drafts, same pipeline

```
$ python send_gate.py examples/good-email.md examples/claims.json
[slop]   PASS  examples/good-email.md  (67 words)
[claims] PASS - good-email.md (1 claim(s))
CLEARED  every declared gate passed on these exact bytes

$ python send_gate.py examples/slop-email.md examples/claims.json
[slop]   FAIL  examples/slop-email.md  (71 words)
            - LEXICON  "underscores" is 13.4x over-represented in post-LLM text
            - LEXICON  "delve" is 9.4x over-represented in post-LLM text
            - REGISTER banned phrase: cutting-edge ("cutting-edge")
            - REGISTER banned phrase: seamless ("seamlessly")
            - REGISTER banned phrase: robust ("robust")
[claims] FAIL - slop-email.md (1 claim(s))
           UNSOURCED    2,400,000  appears in the deliverable with no claim
           UNSOURCED    63%  appears in the deliverable with no claim
REFUSED  examples/slop-email.md: 'slop' FAILED
```

The second draft is the kind an unguarded AI SDR sends a thousand times a day. It also invents two numbers, and the claims gate catches both.

## The gates

| Gate | What it refuses | Why it's built this way |
|---|---|---|
| `gates/prose_slop.py` | Vocabulary that's statistically over-represented in LLM text, and banned business register ("seamless", "cutting-edge", "I hope this email finds you well") | It's **not** an AI detector. Those are unreliable and flag non-native English writers. The lexicon is measured: each word's 2024/2022 frequency ratio from [berenslab/llm-excess-vocab](https://github.com/berenslab/llm-excess-vocab) (MIT; Kobak et al., *Science Advances* 2025, 15M+ abstracts). Only style words are kept, so technical vocabulary never fires. |
| `gates/claim_check.py` | Any figure (money, %, large numbers) that can't be read at a cited `file:line` | It was built after a multi-agent research run returned `verified: true` on claims nobody had opened, and one of those figures was false. The gate re-opens the cited line itself. Exit 1 means "the document is wrong" and exit 2 means "the gate couldn't run", so the two failures are never confused. |
| `gates/receipts.py` | Sending anything without a PASS receipt from every declared stage, still matching the file's sha256 | Skip a gate and the send is refused: "no receipt". Edit after passing and it's refused: "stale". If no plan was declared, that's a refusal too, not a loophole. |

## Tests

```
python gates/receipts.py --test       # 9/9: skip, fail, stale, re-run, borrowed-receipt cases
python gates/claim_check.py --selftest # 16/16, including a planted probe the gate must catch
python gates/prose_slop.py --test     # 23 cases, 66 lexicon words, 20 register rules
```

Python 3.10+, standard library only. The examples use a synthetic account.

## Where this comes from

These gates are extracted from two private systems. One is an outbound engine whose copywriting step writes hash-bound receipts for every draft. The other is a video production engine, where the same receipts ledger guards 20+ stages, from script to final render. The receipts module here is a clean, generic re-implementation of that ledger.

---
© 2026 Alejandro Aguilar Alarcon. All rights reserved. The source is published for review; no licence is granted.
